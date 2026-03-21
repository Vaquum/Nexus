'''Runtime configuration for a Manager instance.

Frozen dataclass holding identity and capital ceiling for a single
Manager instance. Additional fields (risk limits, health policy, etc.)
are added as their respective phases land.
'''

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

__all__ = ['InstanceConfig']

_ZERO = Decimal('0')
_ONE_HUNDRED = Decimal('100')


@dataclass(frozen=True)
class InstanceConfig:
    '''Immutable configuration for one Manager instance.

    Args:
        account_id: Unique identifier for this instance's trading account.
        venue: Which venue to trade on (e.g. ``binance_spot``).
        allocated_capital: Hard ceiling on capital this instance can use,
            denominated in quote asset. The manifest's ``capital_pool``
            must not exceed this value.
        capital_pct: Strategy capital-allocation percentages keyed by
            strategy_id.
    '''

    account_id: str
    venue: str
    allocated_capital: Decimal
    capital_pct: dict[str, Decimal] = field(default_factory=dict)

    def __post_init__(self) -> None:
        '''Validate configuration invariants.'''

        if not self.account_id or not self.account_id.strip():
            msg = 'InstanceConfig.account_id must be a non-empty string'
            raise ValueError(msg)

        if not self.venue or not self.venue.strip():
            msg = 'InstanceConfig.venue must be a non-empty string'
            raise ValueError(msg)

        if not self.allocated_capital.is_finite() or self.allocated_capital <= 0:
            msg = 'InstanceConfig.allocated_capital must be a finite positive value'
            raise ValueError(msg)

        total_pct = _ZERO
        for strategy_id, pct in self.capital_pct.items():
            if not strategy_id or not strategy_id.strip():
                msg = 'InstanceConfig.capital_pct keys must be non-empty strings'
                raise ValueError(msg)
            if not isinstance(pct, Decimal) or not pct.is_finite():
                msg = 'InstanceConfig.capital_pct values must be finite Decimals'
                raise ValueError(msg)
            if pct <= _ZERO or pct > _ONE_HUNDRED:
                msg = 'InstanceConfig.capital_pct values must be in (0, 100]'
                raise ValueError(msg)
            total_pct += pct

        if total_pct > _ONE_HUNDRED:
            msg = 'InstanceConfig.capital_pct total must be <= 100'
            raise ValueError(msg)
