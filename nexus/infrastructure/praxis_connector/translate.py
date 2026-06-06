'''Translate validated actions to TradeCommands for Trading sub-system.'''

from __future__ import annotations

from datetime import datetime, timezone

from nexus.core.domain.order_types import ExecutionMode, OrderType
from nexus.core.validator.pipeline_models import (
    ValidationAction,
    ValidationDecision,
    ValidationRequestContext,
)
from nexus.infrastructure.praxis_connector.trade_command import TradeCommand
from nexus.infrastructure.praxis_connector.trade_command_type import TradeCommandType
from nexus.instance_config import InstanceConfig
from nexus.strategy.action import Action

__all__ = ['translate_to_trade_command']

_ACTION_TO_COMMAND_TYPE: dict[ValidationAction, TradeCommandType] = {
    ValidationAction.ENTER: TradeCommandType.NEW_ORDER,
    ValidationAction.EXIT: TradeCommandType.NEW_ORDER,
    ValidationAction.MODIFY: TradeCommandType.AMEND_ORDER,
    ValidationAction.ABORT: TradeCommandType.CANCEL_ORDER,
    ValidationAction.CANCEL: TradeCommandType.CANCEL_ORDER,
}

_EXIT_DEFAULT_EXECUTION_MODE = ExecutionMode.SINGLE_SHOT
_EXIT_DEFAULT_ORDER_TYPE = OrderType.MARKET
_EXIT_DEFAULT_DEADLINE_SECONDS = 60


def translate_to_trade_command(
    action: Action,
    context: ValidationRequestContext,
    decision: ValidationDecision,
    config: InstanceConfig,
    now: datetime,
) -> TradeCommand:
    '''Translate validated action to TradeCommand for Trading sub-system.

    Args:
        action: Strategy-layer action being translated. Execution-mode,
            order-type, maker-preference, deadline, execution-params, and
            reference-price flow from this source for ENTER. For EXIT,
            the translate layer honors any provided execution fields and
            fills missing values with `execution_mode=SINGLE_SHOT`,
            `order_type=MARKET`, and `deadline=60s` so submission to
            Praxis cannot pass `None` to required parameters.
        context: Validated action request context.
        decision: Validation pipeline decision (must be allowed).
        config: Instance configuration for account/venue/stp_mode.
        now: Timestamp for command creation.

    Returns:
        TradeCommand ready for Trading sub-system dispatch.

    Raises:
        ValueError: If decision is not allowed, command_id is missing,
            or now is not UTC.
    '''

    if not decision.allowed:
        msg = 'translate_to_trade_command: decision must be allowed'
        raise ValueError(msg)

    if now.tzinfo is not timezone.utc:
        msg = 'translate_to_trade_command requires UTC datetime'
        raise ValueError(msg)

    if not context.command_id:
        msg = 'translate_to_trade_command requires non-empty command_id'
        raise ValueError(msg)

    command_type = _ACTION_TO_COMMAND_TYPE[context.action]
    is_new_order = command_type == TradeCommandType.NEW_ORDER
    is_exit = context.action == ValidationAction.EXIT

    reservation_id = None
    if decision.reservation is not None:
        reservation_id = decision.reservation.reservation_id

    if not is_new_order:
        execution_mode = None
        order_type = None
        deadline = None
    else:
        execution_mode = action.execution_mode
        order_type = action.order_type
        deadline = action.deadline
        if is_exit:
            if execution_mode is None:
                execution_mode = _EXIT_DEFAULT_EXECUTION_MODE
            if order_type is None:
                order_type = _EXIT_DEFAULT_ORDER_TYPE
            if deadline is None:
                deadline = _EXIT_DEFAULT_DEADLINE_SECONDS

    if is_new_order:
        size = None if action.quote_qty is not None else context.order_size
        quote_qty = action.quote_qty
    else:
        size = None
        quote_qty = None

    return TradeCommand(
        command_id=context.command_id,
        command_type=command_type,
        account_id=config.account_id,
        venue=config.venue,
        symbol=context.symbol,
        notional=context.order_notional,
        created_at=now,
        side=context.order_side if is_new_order else None,
        size=size,
        quote_qty=quote_qty,
        stp_mode=config.stp_mode if is_new_order else None,
        trade_id=context.trade_id,
        reservation_id=reservation_id,
        execution_mode=execution_mode,
        order_type=order_type,
        execution_params=action.execution_params if is_new_order else None,
        deadline=deadline,
        maker_preference=action.maker_preference if is_new_order else None,
        reference_price=action.reference_price if is_new_order else None,
        strategy_id=context.strategy_id if is_new_order else None,
    )
