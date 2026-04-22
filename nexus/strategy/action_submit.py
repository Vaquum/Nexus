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

__all__ = ['ContextBuilder', 'SubmissionOutcome', 'SubmissionStatus', 'submit_actions']

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

        ctx = build_context(action, strategy_id)
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

        cmd = translate_to_trade_command(action, ctx, decision, config, now())

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
