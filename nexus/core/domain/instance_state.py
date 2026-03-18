'''Composite runtime state for a Manager instance.

Composes capital, risk, positions, and operational mode into a
single top-level container. Created from InstanceConfig at startup.
'''

from __future__ import annotations

from dataclasses import dataclass, field

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

    def __post_init__(self) -> None:
        '''Validate that dict keys match their value identifiers.'''

        for key, pos in self.positions.items():
            if not isinstance(pos, Position):
                msg = f'InstanceState.positions value for key {key!r} must be a Position'
                raise ValueError(msg)
            if key != pos.trade_id:
                msg = f'InstanceState.positions key {key!r} does not match trade_id {pos.trade_id!r}'
                raise ValueError(msg)

        for key, sms in self.strategy_modes.items():
            if not isinstance(sms, StrategyModeState):
                msg = f'InstanceState.strategy_modes value for key {key!r} must be a StrategyModeState'
                raise ValueError(msg)
            if key != sms.strategy_id:
                msg = f'InstanceState.strategy_modes key {key!r} does not match strategy_id {sms.strategy_id!r}'
                raise ValueError(msg)

    @classmethod
    def from_config(cls, config: InstanceConfig) -> InstanceState:
        '''Create initial state from instance configuration.

        Args:
            config: Identity and capital ceiling for this instance.

        Returns:
            Fresh InstanceState with capital pool set and everything else zeroed.
        '''

        return cls(
            capital=CapitalState(capital_pool=config.allocated_capital),
        )
