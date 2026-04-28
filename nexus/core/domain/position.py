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
        entry_price: Volume-weighted average entry price in quote asset
            (excludes fees — used for gross realized P&L calculation).
        unrealized_pnl: Mark-to-market P&L in quote asset.
        pending_exit: Size of exit orders (opposite side) in-flight or resting for this trade.
        avg_cost_basis: Volume-weighted average cost per unit INCLUDING entry
            fees, in quote asset. Differs from `entry_price` because fees are
            baked in. Used by `OutcomeProcessor._handle_fill` on the EXIT
            branch to compute `cost_basis_released = avg_cost_basis * fill_size`,
            which is then passed to `CapitalController.order_exit` so
            `position_notional` and `per_strategy_deployed` decrement by the
            same amount that was added on the matching entry FILL via
            `order_fill` (which adds `fill_notional + actual_fees` per fill).
            Defaults to `_ZERO` so size=0 placeholders (PT-FIX-20) and pre-
            existing snapshots remain valid; `_grow_position` populates it on
            the first real fill.
    '''

    trade_id: str
    strategy_id: str
    symbol: str
    side: OrderSide
    size: Decimal
    entry_price: Decimal
    unrealized_pnl: Decimal = _ZERO
    pending_exit: Decimal = _ZERO
    avg_cost_basis: Decimal = _ZERO

    def __post_init__(self) -> None:
        '''Validate invariants at construction time.'''

        for field_name in ('trade_id', 'strategy_id', 'symbol'):
            val = getattr(self, field_name)
            if not isinstance(val, str) or not val.strip():
                msg = f'Position.{field_name} must be a non-empty string'
                raise ValueError(msg)

        if not isinstance(self.side, OrderSide):
            msg = 'Position.side must be an OrderSide member'
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

        if not self.avg_cost_basis.is_finite() or self.avg_cost_basis < _ZERO:
            msg = 'Position.avg_cost_basis must be a finite non-negative value'
            raise ValueError(msg)

    @property
    def is_closed(self) -> bool:
        '''Return True if position size has reached zero.'''

        return self.size == _ZERO
