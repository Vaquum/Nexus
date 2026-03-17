'''Position dataclass representing an open trade within a Manager instance.

Positions are mutable: size and unrealized_pnl change as fills arrive
and market price moves. Mutation logic belongs in Capital Controller
and Praxis Connector, not here.
'''

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from nexus.core.domain.enums import OrderSide

__all__ = ['Position']

_ZERO = Decimal(0)


@dataclass
class Position:
    '''An open position tracked per trade_id within a Manager instance.

    Args:
        trade_id: Manager-assigned trade lifecycle identifier.
        strategy_id: Which strategy owns this trade.
        symbol: Trading pair symbol.
        side: Position direction.
        size: Current position size in base asset, must be non-negative.
        entry_price: Volume-weighted average entry price in quote asset.
        unrealized_pnl: Mark-to-market P&L in quote asset.
        pending_exit: Size of SELL orders in-flight or resting for this trade.
    '''

    trade_id: str
    strategy_id: str
    symbol: str
    side: OrderSide
    size: Decimal
    entry_price: Decimal
    unrealized_pnl: Decimal = _ZERO
    pending_exit: Decimal = _ZERO

    def __post_init__(self) -> None:
        '''Validate invariants at construction time.'''

        for field_name in ('trade_id', 'strategy_id', 'symbol'):
            val = getattr(self, field_name)
            if not isinstance(val, str) or not val.strip():
                msg = f'Position.{field_name} must be a non-empty string'
                raise ValueError(msg)

        if not self.size.is_finite() or self.size < _ZERO:
            msg = 'Position.size must be a finite non-negative value'
            raise ValueError(msg)

        if not self.entry_price.is_finite() or self.entry_price <= _ZERO:
            msg = 'Position.entry_price must be a finite positive value'
            raise ValueError(msg)

        if not self.pending_exit.is_finite() or self.pending_exit < _ZERO:
            msg = 'Position.pending_exit must be a finite non-negative value'
            raise ValueError(msg)

        if not self.unrealized_pnl.is_finite():
            msg = 'Position.unrealized_pnl must be finite'
            raise ValueError(msg)

    @property
    def is_closed(self) -> bool:
        '''Return True if position size has reached zero.'''

        return self.size == _ZERO
