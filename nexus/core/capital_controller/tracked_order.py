'''Tracked order types for capital lifecycle state machine.

An order moves through IN_FLIGHT → WORKING states while consuming
capital from the corresponding CapitalState buckets.
'''

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

__all__ = ['OrderLifecycleState', 'TrackedOrder']

_ZERO = Decimal(0)


class OrderLifecycleState(Enum):
    '''Order state in the capital lifecycle.

    IN_FLIGHT means the order was sent but venue has not acknowledged.
    WORKING means the venue acknowledged and the order is resting.
    Terminal states (filled, rejected, canceled) remove the order
    from tracking entirely.
    '''

    IN_FLIGHT = 'IN_FLIGHT'
    WORKING = 'WORKING'


@dataclass(frozen=True)
class TrackedOrder:
    '''An immutable record of an order in the capital lifecycle.

    Args:
        order_id: Venue order identifier.
        reservation_id: Original reservation this order consumed.
        strategy_id: Which strategy placed the order.
        notional: Quote capital locked for this order.
        estimated_fees: Quote capital locked for estimated fees.
        remaining_notional: Quote capital not yet filled.
        state: Current lifecycle state (IN_FLIGHT or WORKING).
        created_at: When this order was sent.
    '''

    order_id: str
    reservation_id: str
    strategy_id: str
    notional: Decimal
    estimated_fees: Decimal
    remaining_notional: Decimal
    state: OrderLifecycleState
    created_at: datetime

    def __post_init__(self) -> None:
        '''Validate invariants at construction time.'''

        if not isinstance(self.order_id, str) or not self.order_id.strip():
            msg = 'TrackedOrder.order_id must be a non-empty string'
            raise ValueError(msg)

        if not isinstance(self.reservation_id, str) or not self.reservation_id.strip():
            msg = 'TrackedOrder.reservation_id must be a non-empty string'
            raise ValueError(msg)

        if not isinstance(self.strategy_id, str) or not self.strategy_id.strip():
            msg = 'TrackedOrder.strategy_id must be a non-empty string'
            raise ValueError(msg)

        if (
            not isinstance(self.notional, Decimal)
            or not self.notional.is_finite()
            or self.notional < _ZERO
        ):
            msg = 'TrackedOrder.notional must be a finite non-negative Decimal'
            raise ValueError(msg)

        if (
            not isinstance(self.estimated_fees, Decimal)
            or not self.estimated_fees.is_finite()
            or self.estimated_fees < _ZERO
        ):
            msg = 'TrackedOrder.estimated_fees must be a finite non-negative Decimal'
            raise ValueError(msg)

        if (
            not isinstance(self.remaining_notional, Decimal)
            or not self.remaining_notional.is_finite()
            or self.remaining_notional < _ZERO
        ):
            msg = (
                'TrackedOrder.remaining_notional must be a finite non-negative Decimal'
            )
            raise ValueError(msg)

        if self.remaining_notional > self.notional:
            msg = 'TrackedOrder.remaining_notional cannot exceed notional'
            raise ValueError(msg)

        if not isinstance(self.state, OrderLifecycleState):
            msg = 'TrackedOrder.state must be an OrderLifecycleState'
            raise ValueError(msg)

        if not isinstance(self.created_at, datetime):
            msg = 'TrackedOrder.created_at must be a datetime'
            raise ValueError(msg)

        if (
            self.created_at.tzinfo is None
            or self.created_at.tzinfo.utcoffset(self.created_at) is None
        ):
            msg = 'TrackedOrder.created_at must be timezone-aware'
            raise ValueError(msg)

    @property
    def total(self) -> Decimal:
        '''Total quote capital locked by this order.'''

        return self.notional + self.estimated_fees

    @property
    def remaining_total(self) -> Decimal:
        '''Remaining quote capital including proportional fees.'''

        if self.notional == _ZERO:
            return _ZERO

        fee_ratio = self.estimated_fees / self.notional
        return self.remaining_notional * (1 + fee_ratio)
