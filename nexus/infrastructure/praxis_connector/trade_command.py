'''TradeCommand dataclass for Praxis Connector outbound translation.'''

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from nexus.core.domain.enums import OrderSide
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
        side: BUY/SELL direction; None for CANCEL_ORDER.
        size: Base asset quantity; None for CANCEL_ORDER.
        stp_mode: Self-trade prevention; required for NEW_ORDER, None otherwise.
        trade_id: Position reference for EXIT actions.
        reservation_id: Capital lock reference for ENTER actions.
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

        if (
            self.created_at.tzinfo is None
            or self.created_at.tzinfo.utcoffset(self.created_at) is None
        ):
            msg = 'TradeCommand.created_at must be timezone-aware'
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
