'''Runtime configuration for a Manager instance.

Frozen dataclass holding identity and capital ceiling for a single
Manager instance. Additional fields (risk limits, health policy, etc.)
are added as their respective phases land.
'''

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

__all__ = ['InstanceConfig']


@dataclass(frozen=True)
class InstanceConfig:
    '''Immutable configuration for one Manager instance.

    Args:
        account_id: Unique identifier for this instance's trading account.
        venue: Which venue to trade on (e.g. ``binance_spot``).
        allocated_capital: Hard ceiling on capital this instance can use,
            denominated in quote asset. The manifest's ``capital_pool``
            must not exceed this value.
    '''

    account_id: str
    venue: str
    allocated_capital: Decimal

    def __post_init__(self) -> None:
        '''Validate configuration invariants.'''

        if not self.account_id or not self.account_id.strip():
            msg = 'InstanceConfig.account_id must be a non-empty string'
            raise ValueError(msg)

        if not self.venue or not self.venue.strip():
            msg = 'InstanceConfig.venue must be a non-empty string'
            raise ValueError(msg)

        if self.allocated_capital <= 0:
            msg = 'InstanceConfig.allocated_capital must be positive'
            raise ValueError(msg)
