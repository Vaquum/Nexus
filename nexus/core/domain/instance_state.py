'''Composite runtime state for a Manager instance.

Composes capital, risk, positions, and operational mode into a
single top-level container. Created from InstanceConfig at startup.
'''

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from nexus.core.domain.capital_state import CapitalState
from nexus.core.domain.operational_mode import ModeState, StrategyModeState
from nexus.core.domain.position import Position
from nexus.core.domain.risk_state import RiskState
from nexus.instance_config import InstanceConfig

__all__ = ['InstanceState']


@dataclass
class InstanceState:
    '''Top-level runtime state for one Manager instance.

    Args:
        capital: Capital tracking state.
        risk: Instance-level and per-strategy risk metrics.
        positions: Open positions keyed by trade_id.
        mode: Instance-level operational mode.
        strategy_modes: Per-strategy operational modes keyed by strategy_id.
    '''

    capital: CapitalState
    risk: RiskState = field(default_factory=RiskState)
    positions: dict[str, Position] = field(default_factory=dict)
    mode: ModeState = field(default_factory=ModeState)
    strategy_modes: dict[str, StrategyModeState] = field(default_factory=dict)

    @classmethod
    def from_config(cls, config: InstanceConfig) -> InstanceState:
        '''Create initial state from instance configuration.

        Args:
            config: Identity and capital ceiling for this instance.

        Returns:
            Fresh InstanceState with capital pool set and everything else zeroed.
        '''

        return cls(
            capital=CapitalState(capital_pool=Decimal(config.allocated_capital)),
        )
