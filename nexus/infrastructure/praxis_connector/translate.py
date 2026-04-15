'''Translate validated actions to TradeCommands for Trading sub-system.'''

from __future__ import annotations

from datetime import datetime, timezone

from nexus.core.validator.pipeline_models import (
    ValidationAction,
    ValidationDecision,
    ValidationRequestContext,
)
from nexus.infrastructure.praxis_connector.trade_command import TradeCommand
from nexus.infrastructure.praxis_connector.trade_command_type import TradeCommandType
from nexus.instance_config import InstanceConfig

__all__ = ['translate_to_trade_command']

_ACTION_TO_COMMAND_TYPE: dict[ValidationAction, TradeCommandType] = {
    ValidationAction.ENTER: TradeCommandType.NEW_ORDER,
    ValidationAction.EXIT: TradeCommandType.NEW_ORDER,
    ValidationAction.MODIFY: TradeCommandType.AMEND_ORDER,
    ValidationAction.ABORT: TradeCommandType.CANCEL_ORDER,
    ValidationAction.CANCEL: TradeCommandType.CANCEL_ORDER,
}


def translate_to_trade_command(
    context: ValidationRequestContext,
    decision: ValidationDecision,
    config: InstanceConfig,
    now: datetime,
) -> TradeCommand:
    '''Translate validated action to TradeCommand for Trading sub-system.

    Args:
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

    reservation_id = None
    if decision.reservation is not None:
        reservation_id = decision.reservation.reservation_id

    return TradeCommand(
        command_id=context.command_id,
        command_type=command_type,
        account_id=config.account_id,
        venue=config.venue,
        symbol=context.symbol,
        notional=context.order_notional,
        created_at=now,
        side=context.order_side if is_new_order else None,
        size=context.order_size if is_new_order else None,
        stp_mode=config.stp_mode if is_new_order else None,
        trade_id=context.trade_id,
        reservation_id=reservation_id,
    )
