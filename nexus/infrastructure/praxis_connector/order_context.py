'''OrderContext dataclass for outcome processing metadata.

Provides the metadata needed to process TradeOutcomes, since outcomes
only carry command_id. Stored when order is dispatched, retrieved when
outcome arrives.
'''

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from nexus.core.domain.enums import OrderSide

__all__ = ['OrderContext']

_ZERO = Decimal(0)


@dataclass(frozen=True)
class OrderContext:
    '''Metadata for processing TradeOutcomes.

    Args:
        command_id: Links to TradeCommand and TradeOutcome.
        strategy_id: Which strategy owns this order.
        trade_id: Position reference; None for new entries until assigned.
        side: Order direction at the venue (BUY/SELL). Independent of
            entry-vs-exit semantics: a short-position EXIT is a BUY at
            the venue, and a short-position ENTER is a SELL.
        order_size: Original order size in base asset.
        order_notional: Original order notional in quote asset.
        estimated_fees: Estimated fees at reservation time for reconciliation.
        is_entry: True when this order grows a position (ENTER), False
            when it reduces or closes one (EXIT). The caller knows
            this at construction time from the strategy `Action` (or
            the shutdown EXIT path); it must not be inferred from
            `side`, since BUY-to-cover on a short is an EXIT and
            SELL-to-open on a short is an ENTER. `OutcomeProcessor`
            uses this flag exclusively to route fills between the
            entry path (capital `order_fill` + `_grow_position`) and
            the exit path (`_reduce_position`).
    '''

    command_id: str
    strategy_id: str
    trade_id: str | None
    side: OrderSide
    order_size: Decimal
    order_notional: Decimal
    estimated_fees: Decimal
    is_entry: bool

    def __post_init__(self) -> None:
        '''Validate invariants at construction time.'''

        if not isinstance(self.command_id, str) or not self.command_id.strip():
            msg = 'OrderContext.command_id must be a non-empty string'
            raise ValueError(msg)

        if not isinstance(self.strategy_id, str) or not self.strategy_id.strip():
            msg = 'OrderContext.strategy_id must be a non-empty string'
            raise ValueError(msg)

        if self.trade_id is not None and (
            not isinstance(self.trade_id, str) or not self.trade_id.strip()
        ):
            msg = 'OrderContext.trade_id must be a non-empty string if provided'
            raise ValueError(msg)

        if not isinstance(self.side, OrderSide):
            msg = 'OrderContext.side must be an OrderSide member'
            raise ValueError(msg)

        if not isinstance(self.order_size, Decimal) or not self.order_size.is_finite():
            msg = 'OrderContext.order_size must be a finite Decimal'
            raise ValueError(msg)

        if self.order_size <= _ZERO:
            msg = 'OrderContext.order_size must be positive'
            raise ValueError(msg)

        if (
            not isinstance(self.order_notional, Decimal)
            or not self.order_notional.is_finite()
        ):
            msg = 'OrderContext.order_notional must be a finite Decimal'
            raise ValueError(msg)

        if self.order_notional <= _ZERO:
            msg = 'OrderContext.order_notional must be positive'
            raise ValueError(msg)

        if (
            not isinstance(self.estimated_fees, Decimal)
            or not self.estimated_fees.is_finite()
        ):
            msg = 'OrderContext.estimated_fees must be a finite Decimal'
            raise ValueError(msg)

        if self.estimated_fees < _ZERO:
            msg = 'OrderContext.estimated_fees must be non-negative'
            raise ValueError(msg)

        if not isinstance(self.is_entry, bool):
            msg = 'OrderContext.is_entry must be a bool'
            raise ValueError(msg)

    @property
    def is_exit(self) -> bool:
        '''Return True if this order reduces a position.'''

        return not self.is_entry
