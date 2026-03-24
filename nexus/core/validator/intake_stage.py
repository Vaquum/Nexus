'''Intake-stage validation skeleton with request-shape and hook checks.'''

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from decimal import Decimal

from nexus.core.domain.enums import OrderSide
from nexus.instance_config import InstanceConfig
from nexus.core.validator.pipeline_models import (
    ValidationAction,
    ValidationDecision,
    ValidationRequestContext,
    ValidationStage,
)

__all__ = [
    'IntakeValidationHook',
    'build_default_intake_hooks',
    'make_duplicate_order_hook',
    'make_order_rate_hook',
    'make_reference_integrity_hook',
    'validate_intake_stage',
]

_ZERO = Decimal(0)

IntakeValidationHook = Callable[[ValidationRequestContext], ValidationDecision | None]


def build_default_intake_hooks(
    config: InstanceConfig,
    *,
    active_command_ids: set[str] | None = None,
    max_order_rate: int | None = None,
    now_fn: Callable[[], datetime] | None = None,
) -> tuple[IntakeValidationHook, ...]:
    '''Build default intake hooks from operator configuration.

    Args:
        config: Instance config carrying duplicate window settings.
        active_command_ids: Active command id set for MODIFY/ABORT checks.
        max_order_rate: Optional max ENTER actions per second.
        now_fn: Optional clock override for deterministic tests.

    Returns:
        Ordered tuple of default intake hooks.
    '''

    hooks: list[IntakeValidationHook] = [
        make_duplicate_order_hook(config.duplicate_window_ms, now_fn=now_fn),
        make_reference_integrity_hook(
            active_command_ids=(
                active_command_ids if active_command_ids is not None else set()
            )
        ),
    ]

    if max_order_rate is not None:
        hooks.insert(0, make_order_rate_hook(max_order_rate, now_fn=now_fn))

    return tuple(hooks)


def make_order_rate_hook(
    max_order_rate: int,
    now_fn: Callable[[], datetime] | None = None,
) -> IntakeValidationHook:
    '''Create a per-process intake hook enforcing max enters per second.'''

    if isinstance(max_order_rate, bool) or not isinstance(max_order_rate, int):
        msg = f'max_order_rate must be an integer: {max_order_rate}'
        raise ValueError(msg)

    if max_order_rate <= 0:
        msg = f'max_order_rate must be positive: {max_order_rate}'
        raise ValueError(msg)

    clock = now_fn or (lambda: datetime.now(tz=timezone.utc))
    event_times: deque[float] = deque()

    def hook(context: ValidationRequestContext) -> ValidationDecision | None:
        if context.action != ValidationAction.ENTER:
            return None

        now = clock().timestamp()
        cutoff = now - 1.0
        while event_times and event_times[0] <= cutoff:
            event_times.popleft()

        if len(event_times) >= max_order_rate:
            return ValidationDecision(
                allowed=False,
                failed_stage=ValidationStage.INTAKE,
                reason_code='INTAKE_MAX_ORDER_RATE_EXCEEDED',
                message=f'max_order_rate exceeded: limit={max_order_rate}/s',
            )

        event_times.append(now)
        return None

    return hook


def make_duplicate_order_hook(
    duplicate_window_ms: int,
    now_fn: Callable[[], datetime] | None = None,
) -> IntakeValidationHook:
    '''Create intake hook rejecting duplicate ENTER command IDs within a window.'''

    if isinstance(duplicate_window_ms, bool) or not isinstance(
        duplicate_window_ms,
        int,
    ):
        msg = f'duplicate_window_ms must be an integer: {duplicate_window_ms}'
        raise ValueError(msg)

    if duplicate_window_ms <= 0:
        msg = f'duplicate_window_ms must be positive: {duplicate_window_ms}'
        raise ValueError(msg)

    clock = now_fn or (lambda: datetime.now(tz=timezone.utc))
    duplicate_window_seconds = duplicate_window_ms / 1000
    seen: dict[tuple[str, ...], float] = {}

    def hook(context: ValidationRequestContext) -> ValidationDecision | None:
        if context.action != ValidationAction.ENTER:
            return None

        if context.order_side is None:
            return ValidationDecision(
                allowed=False,
                failed_stage=ValidationStage.INTAKE,
                reason_code='INTAKE_ORDER_SIDE_REQUIRED',
                message='order_side is required for ENTER actions',
            )

        command_id = context.command_id
        if command_id is None:
            return ValidationDecision(
                allowed=False,
                failed_stage=ValidationStage.INTAKE,
                reason_code='INTAKE_COMMAND_ID_REQUIRED',
                message='command_id is required for ENTER actions',
            )

        key: tuple[str, ...] = (
            'command_id',
            context.strategy_id,
            command_id,
        )
        now = clock().timestamp()
        cutoff = now - duplicate_window_seconds

        stale_keys = [k for k, ts in seen.items() if ts <= cutoff]
        for stale_key in stale_keys:
            seen.pop(stale_key, None)

        prior = seen.get(key)
        if prior is not None and (now - prior) < duplicate_window_seconds:
            return ValidationDecision(
                allowed=False,
                failed_stage=ValidationStage.INTAKE,
                reason_code='INTAKE_DUPLICATE_ORDER_WINDOW',
                message=(
                    'duplicate command_id detected within '
                    f'{duplicate_window_ms}ms window'
                ),
            )

        seen[key] = now
        return None

    return hook


def make_reference_integrity_hook(
    active_command_ids: set[str],
) -> IntakeValidationHook:
    '''Create intake hook for trade/command references and spot-direction rules.'''

    def hook(context: ValidationRequestContext) -> ValidationDecision | None:
        decision: ValidationDecision | None = None

        if context.action == ValidationAction.ENTER:
            if context.order_side != OrderSide.BUY:
                decision = ValidationDecision(
                    allowed=False,
                    failed_stage=ValidationStage.INTAKE,
                    reason_code='INTAKE_SPOT_DIRECTION_INVALID',
                    message='ENTER actions must use BUY side for spot direction rules',
                )
            elif context.order_size is None or context.order_size <= _ZERO:
                decision = ValidationDecision(
                    allowed=False,
                    failed_stage=ValidationStage.INTAKE,
                    reason_code='INTAKE_ENTER_SIZE_INVALID',
                    message='ENTER requires order_size greater than zero',
                )

        elif context.action == ValidationAction.EXIT:
            if context.order_side != OrderSide.SELL:
                decision = ValidationDecision(
                    allowed=False,
                    failed_stage=ValidationStage.INTAKE,
                    reason_code='INTAKE_SPOT_DIRECTION_INVALID',
                    message='EXIT actions must use SELL side for spot direction rules',
                )
            elif (
                context.trade_id is None
                or context.trade_id not in context.state.positions
            ):
                decision = ValidationDecision(
                    allowed=False,
                    failed_stage=ValidationStage.INTAKE,
                    reason_code='INTAKE_TRADE_REFERENCE_INVALID',
                    message='EXIT requires trade_id of an open position',
                )
            elif context.order_size is None or context.order_size <= _ZERO:
                decision = ValidationDecision(
                    allowed=False,
                    failed_stage=ValidationStage.INTAKE,
                    reason_code='INTAKE_EXIT_SIZE_INVALID',
                    message='EXIT requires order_size greater than zero',
                )
            else:
                position = context.state.positions[context.trade_id]
                remaining = position.size - position.pending_exit
                if context.order_size > remaining:
                    decision = ValidationDecision(
                        allowed=False,
                        failed_stage=ValidationStage.INTAKE,
                        reason_code='INTAKE_EXIT_SIZE_EXCEEDS_REMAINING',
                        message='EXIT size exceeds remaining position after pending exits',
                    )

        elif context.action == ValidationAction.MODIFY:
            if (
                context.command_id is None
                or context.command_id not in active_command_ids
            ):
                decision = ValidationDecision(
                    allowed=False,
                    failed_stage=ValidationStage.INTAKE,
                    reason_code='INTAKE_COMMAND_REFERENCE_INVALID',
                    message='MODIFY requires valid active command_id',
                )
            elif context.order_size is None or context.order_size <= _ZERO:
                decision = ValidationDecision(
                    allowed=False,
                    failed_stage=ValidationStage.INTAKE,
                    reason_code='INTAKE_MODIFY_SIZE_INVALID',
                    message='MODIFY requires order_size greater than zero',
                )

        elif context.action == ValidationAction.ABORT and (
            context.command_id is None or context.command_id not in active_command_ids
        ):
            decision = ValidationDecision(
                allowed=False,
                failed_stage=ValidationStage.INTAKE,
                reason_code='INTAKE_COMMAND_REFERENCE_INVALID',
                message='ABORT requires valid active command_id',
            )

        return decision

    return hook


def validate_intake_stage(
    context: ValidationRequestContext,
    hooks: Sequence[IntakeValidationHook] = (),
) -> ValidationDecision:
    '''Validate request shape and execute optional intake hooks.

    Args:
        context: Validator request context for this action.
        hooks: Optional hook callables for additional intake checks.

    Returns:
        ValidationDecision for the intake stage.
    '''

    if context.strategy_id != context.strategy_id.strip():
        return ValidationDecision(
            allowed=False,
            failed_stage=ValidationStage.INTAKE,
            reason_code='INTAKE_STRATEGY_ID_WHITESPACE',
            message='strategy_id must not contain leading or trailing whitespace',
        )

    if context.order_notional == 0:
        return ValidationDecision(
            allowed=False,
            failed_stage=ValidationStage.INTAKE,
            reason_code='INTAKE_ORDER_NOTIONAL_ZERO',
            message='order_notional must be greater than zero',
        )

    if (
        context.action
        not in (
            ValidationAction.EXIT,
            ValidationAction.ABORT,
            ValidationAction.CANCEL,
        )
        and context.strategy_budget == 0
    ):
        return ValidationDecision(
            allowed=False,
            failed_stage=ValidationStage.INTAKE,
            reason_code='INTAKE_STRATEGY_BUDGET_ZERO',
            message='strategy_budget must be greater than zero',
        )

    for hook in hooks:
        decision = hook(context)
        if decision is None:
            continue

        if not isinstance(decision, ValidationDecision):
            msg = 'Intake hook must return ValidationDecision or None'
            raise ValueError(msg)

        if decision.allowed:
            if decision.reservation is not None:
                msg = 'Intake hook must not attach reservation on allowed decision'
                raise ValueError(msg)
            continue

        if decision.failed_stage != ValidationStage.INTAKE:
            msg = 'Intake hook denied decision must set failed_stage=INTAKE'
            raise ValueError(msg)

        return decision

    return ValidationDecision(allowed=True)
