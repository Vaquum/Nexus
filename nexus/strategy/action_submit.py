'''Action submission helper for runtime strategy → Praxis flow.

Used by `PredictLoop._tick` and `TimerLoop._tick` to push the
`list[Action]` returned from a strategy callback through the
validator and into Praxis. ENTER/EXIT/MODIFY go through
`ValidationPipeline.validate` → `translate_to_trade_command` →
`PraxisOutbound.send_command`. ABORT bypasses the validator and
goes directly to `PraxisOutbound.send_abort`.

Callers inject `build_context(action, strategy_id)` which returns
a fully-populated `ValidationRequestContext` or `None` when the
context cannot be assembled (e.g. EXIT for an unknown `trade_id`).
The helper itself stays free of fee / notional / budget arithmetic
so unit tests can drive it with a fake context builder.
'''

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from nexus.core.capital_controller.capital_controller import CapitalController
from nexus.core.capital_controller.lifecycle_result import LifecycleResult
from nexus.core.validator.pipeline_executor import ValidationPipeline
from nexus.core.validator.pipeline_models import (
    ValidationDecision,
    ValidationRequestContext,
)
from nexus.infrastructure.praxis_connector.praxis_outbound import PraxisOutbound
from nexus.infrastructure.praxis_connector.translate import (
    translate_to_trade_command,
)
from nexus.instance_config import InstanceConfig
from nexus.strategy.action import Action, ActionType

__all__ = [
    'ContextBuilder',
    'SubmissionOutcome',
    'SubmissionStatus',
    'bridge_to_capital',
    'submit_actions',
]

_log = logging.getLogger(__name__)

_RUNTIME_ABORT_REASON = 'runtime_strategy_abort'


class SubmissionStatus(Enum):
    '''Outcome of attempting to submit a single Action.'''

    SUBMITTED = 'submitted'
    REJECTED = 'rejected'
    SUBMIT_FAILED = 'submit_failed'
    INVALID = 'invalid'


@dataclass(frozen=True)
class SubmissionOutcome:
    '''Per-action result returned by `submit_actions`.

    Args:
        status: Terminal status of the submission attempt.
        command_id: Praxis-assigned command id when status is SUBMITTED.
        decision: Validator decision when ENTER/EXIT/MODIFY was attempted.
        error: Short error string when status is SUBMIT_FAILED or INVALID.
    '''

    status: SubmissionStatus
    command_id: str | None = None
    decision: ValidationDecision | None = None
    error: str | None = None


ContextBuilder = Callable[[Action, str], ValidationRequestContext | None]


def submit_actions(
    actions: list[Action],
    *,
    strategy_id: str,
    config: InstanceConfig,
    praxis_outbound: PraxisOutbound,
    validator: ValidationPipeline,
    build_context: ContextBuilder,
    now: Callable[[], datetime],
) -> list[tuple[Action, SubmissionOutcome]]:
    '''Run a list of strategy actions through validation and submission.

    Args:
        actions: Strategy-returned actions for one tick.
        strategy_id: Strategy identifier these actions belong to.
        config: Per-instance runtime config (account_id, venue, intake
            tunables) — passed through to `translate_to_trade_command`.
        praxis_outbound: Bridge for `send_command` / `send_abort`.
        validator: Configured validator pipeline (capital, risk, intake,
            health, platform_limits, price stages).
        build_context: Caller-supplied builder. For ENTER/EXIT/MODIFY
            actions, returns a populated `ValidationRequestContext` or
            `None` when the context is unavailable (e.g. unknown
            `trade_id` on EXIT). Not invoked for ABORT.
        now: UTC-now provider injected for deterministic tests.

    Returns:
        One `(action, SubmissionOutcome)` tuple per input action, in
        the original order. Submission failures of one action do not
        abort the iteration.
    '''

    results: list[tuple[Action, SubmissionOutcome]] = []

    for action in actions:
        if action.action_type == ActionType.ABORT:
            results.append((action, _submit_abort(action, config, praxis_outbound, now())))
            continue

        try:
            ctx = build_context(action, strategy_id)
        except Exception as e:  # noqa: BLE001 - context builder must not abort the tick
            _log.exception(
                'build_context raised',
                extra={
                    'strategy_id': strategy_id,
                    'action_type': action.action_type.value,
                },
            )
            results.append((
                action,
                SubmissionOutcome(
                    status=SubmissionStatus.SUBMIT_FAILED,
                    error=f'build_context: {e}',
                ),
            ))
            continue

        if ctx is None:
            _log.warning(
                'context unavailable, skipping action',
                extra={
                    'strategy_id': strategy_id,
                    'action_type': action.action_type.value,
                },
            )
            results.append((
                action,
                SubmissionOutcome(
                    status=SubmissionStatus.INVALID,
                    error='context unavailable',
                ),
            ))
            continue

        try:
            decision = validator.validate(ctx)
        except Exception as e:  # noqa: BLE001 - validator must not abort the tick
            _log.exception(
                'validator raised',
                extra={
                    'strategy_id': strategy_id,
                    'action_type': action.action_type.value,
                },
            )
            results.append((
                action,
                SubmissionOutcome(
                    status=SubmissionStatus.SUBMIT_FAILED,
                    error=f'validator: {e}',
                ),
            ))
            continue

        if not decision.allowed:
            _log.info(
                'action rejected by validator',
                extra={
                    'strategy_id': strategy_id,
                    'action_type': action.action_type.value,
                    'failed_stage': (
                        decision.failed_stage.value
                        if decision.failed_stage is not None else None
                    ),
                    'reason_code': decision.reason_code,
                },
            )
            results.append((
                action,
                SubmissionOutcome(
                    status=SubmissionStatus.REJECTED,
                    decision=decision,
                ),
            ))
            continue

        try:
            cmd = translate_to_trade_command(action, ctx, decision, config, now())
        except Exception as e:  # noqa: BLE001 - translator must not abort the tick
            _log.exception(
                'translate_to_trade_command raised',
                extra={
                    'strategy_id': strategy_id,
                    'action_type': action.action_type.value,
                },
            )
            results.append((
                action,
                SubmissionOutcome(
                    status=SubmissionStatus.SUBMIT_FAILED,
                    decision=decision,
                    error=f'translate: {e}',
                ),
            ))
            continue

        try:
            command_id = praxis_outbound.send_command(cmd)
        except Exception as e:  # noqa: BLE001 - per-action submit failure is local
            _log.exception(
                'send_command failed',
                extra={
                    'strategy_id': strategy_id,
                    'action_type': action.action_type.value,
                },
            )
            results.append((
                action,
                SubmissionOutcome(
                    status=SubmissionStatus.SUBMIT_FAILED,
                    decision=decision,
                    error=str(e),
                ),
            ))
            continue

        if action.action_type == ActionType.EXIT and action.trade_id is not None:
            position = ctx.state.positions.get(action.trade_id)
            if position is not None and action.size is not None:
                position.pending_exit += action.size

        _log.info(
            'action submitted',
            extra={
                'strategy_id': strategy_id,
                'action_type': action.action_type.value,
                'command_id': command_id,
            },
        )
        results.append((
            action,
            SubmissionOutcome(
                status=SubmissionStatus.SUBMITTED,
                command_id=command_id,
                decision=decision,
            ),
        ))

    return results


def bridge_to_capital(
    controller: CapitalController,
    outcome: SubmissionOutcome,
) -> LifecycleResult | None:
    '''Convert a SUBMITTED reservation into a tracked IN_FLIGHT order.

    `submit_actions` returns the Praxis-assigned `command_id` and the
    capital-stage `Reservation` on the `SubmissionOutcome`, but does
    not invoke `CapitalController.send_order(reservation_id, command_id)`
    itself. Without that call, every later `OutcomeProcessor.process(...)`
    looks up `self._orders[command_id]` and gets `None` →
    `INVARIANT_BREACH: order not found` → ACK / FILL / REJECT / CANCEL
    silently no-op, capital stays parked in `reservation_notional`
    until TTL expiry, and `position_notional` never grows.

    The launcher must thread this for every SUBMITTED action. This
    helper centralises the wiring so the contract lives next to
    `submit_actions` instead of being re-implemented (and forgotten)
    in every launcher.

    The `command_id` argument to `CapitalController.send_order` is the
    `order_id` that `OutcomeProcessor.process(outcome, context)` will
    look up in `self._orders` via `outcome.command_id`. Renaming
    either side without renaming the other will break the round trip
    silently — see the contract note on `OutcomeProcessor.process`.

    Args:
        controller: Per-account `CapitalController` to mutate.
        outcome: Result of a single action's `submit_actions` pass.

    Returns:
        `LifecycleResult` from `controller.send_order` when the
        outcome carries a reservation and a `command_id`; `None`
        when the outcome did not produce a tracked order. Three
        early-return cases:

        * `status != SUBMITTED` (REJECTED, SUBMIT_FAILED, INVALID)
          — validator/translator/outbound rejected the action, so
          no capital was reserved.
        * `status == SUBMITTED` but `command_id is None` — defensive
          guard; should never trip in production.
        * `status == SUBMITTED` and `command_id` set, but
          `decision is None` or `decision.reservation is None` —
          covers ABORT (validator bypassed entirely, decision is
          None) and EXIT/MODIFY (validator runs but does not
          reserve capital).
    '''

    if outcome.status != SubmissionStatus.SUBMITTED:
        _log.debug(
            'bridge_to_capital skipped: status=%s (no tracked order to convert)',
            outcome.status.value,
        )
        return None

    if outcome.command_id is None:
        _log.warning(
            'bridge_to_capital skipped: SUBMITTED outcome has no command_id; '
            'OutcomeProcessor will not be able to match capital lifecycle',
        )
        return None

    if outcome.decision is None or outcome.decision.reservation is None:
        _log.debug(
            'bridge_to_capital skipped: command_id=%s has no reservation '
            '(EXIT/MODIFY paths bypass capital reservation)',
            outcome.command_id,
        )
        return None

    return controller.send_order(
        reservation_id=outcome.decision.reservation.reservation_id,
        order_id=outcome.command_id,
    )


def _submit_abort(
    action: Action,
    config: InstanceConfig,
    praxis_outbound: PraxisOutbound,
    now: datetime,
) -> SubmissionOutcome:
    if action.command_id is None:
        return SubmissionOutcome(
            status=SubmissionStatus.INVALID,
            error='abort missing command_id',
        )

    try:
        praxis_outbound.send_abort(
            command_id=action.command_id,
            account_id=config.account_id,
            reason=_RUNTIME_ABORT_REASON,
            created_at=now,
        )
    except Exception as e:  # noqa: BLE001 - per-action submit failure is local
        _log.exception(
            'send_abort failed',
            extra={'command_id': action.command_id},
        )
        return SubmissionOutcome(
            status=SubmissionStatus.SUBMIT_FAILED,
            error=str(e),
        )

    _log.info(
        'abort submitted',
        extra={'command_id': action.command_id},
    )
    return SubmissionOutcome(
        status=SubmissionStatus.SUBMITTED,
        command_id=action.command_id,
    )
