'''Capital state tracked by the Capital Controller.

Mutable dataclass holding all capital-related fields for a Manager
instance. The ``available`` property is derived — never stored directly.
'''

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

__all__ = ['CapitalState']

_ZERO = Decimal(0)


@dataclass
class CapitalState:
    '''Runtime capital state for one Manager instance.

    Args:
        capital_pool: Static budget ceiling in quote asset from manifest.
        position_notional: Sum of open positions at cost.
        working_order_notional: Sum of resting BUY orders on venue.
        in_flight_order_notional: Sum of BUY orders sent but not acked.
        fee_reserve: Reserved for transaction costs.
        reservation_notional: Sum of active TOCTOU locks (BUY-side).
    '''

    capital_pool: Decimal
    position_notional: Decimal = _ZERO
    working_order_notional: Decimal = _ZERO
    in_flight_order_notional: Decimal = _ZERO
    fee_reserve: Decimal = _ZERO
    reservation_notional: Decimal = _ZERO

    def __post_init__(self) -> None:
        '''Validate invariants at construction time.'''

        if self.capital_pool <= _ZERO:
            msg = 'CapitalState.capital_pool must be positive'
            raise ValueError(msg)

        for field_name in (
            'position_notional',
            'working_order_notional',
            'in_flight_order_notional',
            'fee_reserve',
            'reservation_notional',
        ):
            val = getattr(self, field_name)
            if val < _ZERO:
                msg = f'CapitalState.{field_name} must be non-negative'
                raise ValueError(msg)

    @property
    def available(self) -> Decimal:
        '''Compute available capital for new BUY orders.

        Returns:
            Quote capital not committed to positions, orders, or reserves.
        '''

        return (
            self.capital_pool
            - self.position_notional
            - self.working_order_notional
            - self.in_flight_order_notional
            - self.fee_reserve
            - self.reservation_notional
        )
