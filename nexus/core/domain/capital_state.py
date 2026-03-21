'''Capital state tracked by the Capital Controller.

Mutable dataclass holding all capital-related fields for a Manager
instance. The ``available`` property is derived — never stored directly.
'''

from __future__ import annotations

from dataclasses import dataclass, field
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
        per_strategy_deployed: Deployed capital by strategy_id.
    '''

    capital_pool: Decimal
    position_notional: Decimal = _ZERO
    working_order_notional: Decimal = _ZERO
    in_flight_order_notional: Decimal = _ZERO
    fee_reserve: Decimal = _ZERO
    reservation_notional: Decimal = _ZERO
    per_strategy_deployed: dict[str, Decimal] = field(default_factory=dict)

    def __post_init__(self) -> None:
        '''Validate invariants at construction time.'''

        if not self.capital_pool.is_finite() or self.capital_pool <= _ZERO:
            msg = 'CapitalState.capital_pool must be a finite positive value'
            raise ValueError(msg)

        for field_name in (
            'position_notional',
            'working_order_notional',
            'in_flight_order_notional',
            'fee_reserve',
            'reservation_notional',
        ):
            val = getattr(self, field_name)
            if not val.is_finite() or val < _ZERO:
                msg = f'CapitalState.{field_name} must be a finite non-negative value'
                raise ValueError(msg)

        for strategy_key, deployed in self.per_strategy_deployed.items():
            if not isinstance(strategy_key, str):
                msg = (
                    'CapitalState.per_strategy_deployed keys must be non-empty strings'
                )
                raise ValueError(msg)

            strategy_id = strategy_key.strip()
            if not strategy_id:
                msg = (
                    'CapitalState.per_strategy_deployed keys must be non-empty strings'
                )
                raise ValueError(msg)
            if strategy_key != strategy_id:
                msg = (
                    'CapitalState.per_strategy_deployed keys must not contain leading '
                    'or trailing whitespace'
                )
                raise ValueError(msg)
            if not isinstance(deployed, Decimal):
                msg = (
                    'CapitalState.per_strategy_deployed values must be finite '
                    'non-negative Decimal values'
                )
                raise ValueError(msg)
            if not deployed.is_finite() or deployed < _ZERO:
                msg = (
                    'CapitalState.per_strategy_deployed values must be finite '
                    'non-negative Decimal values'
                )
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
