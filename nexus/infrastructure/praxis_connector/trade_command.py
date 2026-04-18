'''TradeCommand dataclass for Praxis Connector outbound translation.'''

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from types import MappingProxyType

from nexus.core.domain.enums import OrderSide
from nexus.core.domain.order_types import ExecutionMode, MakerPreference, OrderType
from nexus.core.stp_mode import STPMode
from nexus.infrastructure.praxis_connector.trade_command_type import TradeCommandType

__all__ = ['TradeCommand']

_ZERO = Decimal(0)


@dataclass(frozen=True)
class TradeCommand:
    '''Immutable command for Trading sub-system dispatch.

    Args:
        command_id: Unique command reference.
        command_type: NEW_ORDER, AMEND_ORDER, or CANCEL_ORDER.
        account_id: Trading account identity.
        venue: Target venue.
        symbol: Trading pair.
        notional: Quote asset amount.
        created_at: Command creation timestamp.
        side: BUY/SELL direction; None for AMEND_ORDER/CANCEL_ORDER.
        size: Base asset quantity; None for AMEND_ORDER/CANCEL_ORDER.
        stp_mode: Self-trade prevention; required for NEW_ORDER, None otherwise.
        trade_id: Position reference for EXIT actions.
        reservation_id: Capital lock reference for ENTER and size-increasing MODIFY.
        execution_mode: How to execute; populated for NEW_ORDER from
            Action.execution_mode when provided. For EXIT actions, the Action may
            omit this value and translation fills a safe default before submission.
            None for AMEND/CANCEL.
        order_type: Order type; populated for NEW_ORDER from Action.order_type when
            provided. For EXIT actions, the Action may omit this value and
            translation fills a safe default before submission. None for AMEND/CANCEL.
        execution_params: Mode-specific parameters; populated for NEW_ORDER from
            Action.execution_params, None for AMEND/CANCEL.
        deadline: Timeout in seconds; populated for NEW_ORDER from Action.deadline
            when provided. For EXIT actions, the Action may omit this value and
            translation fills a safe default before submission. None for AMEND/CANCEL.
        maker_preference: Maker/taker preference; populated for NEW_ORDER from
            Action.maker_preference, None for AMEND/CANCEL.
        reference_price: Strategy reference price; populated for NEW_ORDER from
            Action.reference_price, None for AMEND/CANCEL.
    '''

    command_id: str
    command_type: TradeCommandType
    account_id: str
    venue: str
    symbol: str
    notional: Decimal
    created_at: datetime
    side: OrderSide | None = None
    size: Decimal | None = None
    stp_mode: STPMode | None = None
    trade_id: str | None = None
    reservation_id: str | None = None
    execution_mode: ExecutionMode | None = None
    order_type: OrderType | None = None
    execution_params: Mapping[str, object] | None = None
    deadline: int | None = None
    maker_preference: MakerPreference | None = None
    reference_price: Decimal | None = None

    def __post_init__(self) -> None:
        '''Validate command invariants at construction time.'''

        if not isinstance(self.command_id, str) or not self.command_id.strip():
            msg = 'TradeCommand.command_id must be a non-empty string'
            raise ValueError(msg)

        if not isinstance(self.command_type, TradeCommandType):
            msg = 'TradeCommand.command_type must be a TradeCommandType member'
            raise ValueError(msg)

        if not isinstance(self.account_id, str) or not self.account_id.strip():
            msg = 'TradeCommand.account_id must be a non-empty string'
            raise ValueError(msg)

        if not isinstance(self.venue, str) or not self.venue.strip():
            msg = 'TradeCommand.venue must be a non-empty string'
            raise ValueError(msg)

        if not isinstance(self.symbol, str) or not self.symbol.strip():
            msg = 'TradeCommand.symbol must be a non-empty string'
            raise ValueError(msg)

        if (
            not isinstance(self.notional, Decimal)
            or not self.notional.is_finite()
            or self.notional < _ZERO
        ):
            msg = 'TradeCommand.notional must be a finite non-negative Decimal'
            raise ValueError(msg)

        if not isinstance(self.created_at, datetime):
            msg = 'TradeCommand.created_at must be a datetime'
            raise ValueError(msg)

        if self.created_at.tzinfo is not timezone.utc:
            msg = 'TradeCommand.created_at must be UTC'
            raise ValueError(msg)

        if self.side is not None and not isinstance(self.side, OrderSide):
            msg = 'TradeCommand.side must be an OrderSide member or None'
            raise ValueError(msg)

        if self.size is not None and (
            not isinstance(self.size, Decimal)
            or not self.size.is_finite()
            or self.size <= _ZERO
        ):
            msg = 'TradeCommand.size must be a finite positive Decimal or None'
            raise ValueError(msg)

        if self.stp_mode is not None and not isinstance(self.stp_mode, STPMode):
            msg = 'TradeCommand.stp_mode must be an STPMode member or None'
            raise ValueError(msg)

        if self.trade_id is not None and (
            not isinstance(self.trade_id, str) or not self.trade_id.strip()
        ):
            msg = 'TradeCommand.trade_id must be a non-empty string or None'
            raise ValueError(msg)

        if self.reservation_id is not None and (
            not isinstance(self.reservation_id, str) or not self.reservation_id.strip()
        ):
            msg = 'TradeCommand.reservation_id must be a non-empty string or None'
            raise ValueError(msg)

        if self.execution_mode is not None and not isinstance(
            self.execution_mode, ExecutionMode
        ):
            msg = 'TradeCommand.execution_mode must be an ExecutionMode member or None'
            raise ValueError(msg)

        if self.order_type is not None and not isinstance(self.order_type, OrderType):
            msg = 'TradeCommand.order_type must be an OrderType member or None'
            raise ValueError(msg)

        if self.execution_params is not None:
            if not isinstance(self.execution_params, Mapping):
                msg = 'TradeCommand.execution_params must be a Mapping or None'
                raise ValueError(msg)
            object.__setattr__(
                self,
                'execution_params',
                MappingProxyType(dict(self.execution_params)),
            )

        if self.deadline is not None and (
            isinstance(self.deadline, bool)
            or not isinstance(self.deadline, int)
            or self.deadline <= 0
        ):
            msg = 'TradeCommand.deadline must be a positive int or None'
            raise ValueError(msg)

        if self.maker_preference is not None and not isinstance(
            self.maker_preference, MakerPreference
        ):
            msg = 'TradeCommand.maker_preference must be a MakerPreference member or None'
            raise ValueError(msg)

        if self.reference_price is not None and (
            not isinstance(self.reference_price, Decimal)
            or not self.reference_price.is_finite()
            or self.reference_price <= _ZERO
        ):
            msg = 'TradeCommand.reference_price must be a finite positive Decimal or None'
            raise ValueError(msg)

        self._validate_command_type_invariants()

    def _validate_command_type_invariants(self) -> None:
        '''Validate fields based on command_type.'''

        if self.command_type == TradeCommandType.NEW_ORDER:
            if self.side is None:
                msg = 'TradeCommand: NEW_ORDER requires side'
                raise ValueError(msg)
            if self.size is None:
                msg = 'TradeCommand: NEW_ORDER requires size'
                raise ValueError(msg)
            if self.stp_mode is None:
                msg = 'TradeCommand: NEW_ORDER requires stp_mode'
                raise ValueError(msg)

        elif self.command_type == TradeCommandType.AMEND_ORDER:
            if self.side is not None:
                msg = 'TradeCommand: AMEND_ORDER must not have side'
                raise ValueError(msg)
            if self.size is not None:
                msg = 'TradeCommand: AMEND_ORDER must not have size'
                raise ValueError(msg)
            if self.stp_mode is not None:
                msg = 'TradeCommand: AMEND_ORDER must not have stp_mode'
                raise ValueError(msg)
            if self.execution_mode is not None:
                msg = 'TradeCommand: AMEND_ORDER must not have execution_mode'
                raise ValueError(msg)
            if self.order_type is not None:
                msg = 'TradeCommand: AMEND_ORDER must not have order_type'
                raise ValueError(msg)
            if self.execution_params is not None:
                msg = 'TradeCommand: AMEND_ORDER must not have execution_params'
                raise ValueError(msg)
            if self.deadline is not None:
                msg = 'TradeCommand: AMEND_ORDER must not have deadline'
                raise ValueError(msg)
            if self.maker_preference is not None:
                msg = 'TradeCommand: AMEND_ORDER must not have maker_preference'
                raise ValueError(msg)
            if self.reference_price is not None:
                msg = 'TradeCommand: AMEND_ORDER must not have reference_price'
                raise ValueError(msg)

        elif self.command_type == TradeCommandType.CANCEL_ORDER:
            if self.side is not None:
                msg = 'TradeCommand: CANCEL_ORDER must not have side'
                raise ValueError(msg)
            if self.size is not None:
                msg = 'TradeCommand: CANCEL_ORDER must not have size'
                raise ValueError(msg)
            if self.stp_mode is not None:
                msg = 'TradeCommand: CANCEL_ORDER must not have stp_mode'
                raise ValueError(msg)
            if self.execution_mode is not None:
                msg = 'TradeCommand: CANCEL_ORDER must not have execution_mode'
                raise ValueError(msg)
            if self.order_type is not None:
                msg = 'TradeCommand: CANCEL_ORDER must not have order_type'
                raise ValueError(msg)
            if self.execution_params is not None:
                msg = 'TradeCommand: CANCEL_ORDER must not have execution_params'
                raise ValueError(msg)
            if self.deadline is not None:
                msg = 'TradeCommand: CANCEL_ORDER must not have deadline'
                raise ValueError(msg)
            if self.maker_preference is not None:
                msg = 'TradeCommand: CANCEL_ORDER must not have maker_preference'
                raise ValueError(msg)
            if self.reference_price is not None:
                msg = 'TradeCommand: CANCEL_ORDER must not have reference_price'
                raise ValueError(msg)
