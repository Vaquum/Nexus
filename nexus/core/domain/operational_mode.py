'''Operational mode state for instance and per-strategy tracking.

Mutable dataclasses holding current mode and what triggered
the most recent transition.
'''

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from nexus.core.domain.enums import OperationalMode

__all__ = ['ModeState', 'StrategyModeState']


@dataclass
class ModeState:
    '''Instance-level operational mode with transition tracking.

    Args:
        mode: Current operational mode.
        trigger: What caused the most recent mode transition.
        transitioned_at: When the most recent transition occurred.
    '''

    mode: OperationalMode = OperationalMode.ACTIVE
    trigger: str = 'init'
    transitioned_at: datetime = datetime.min

    def __post_init__(self) -> None:
        '''Validate invariants at construction time.'''

        if not isinstance(self.mode, OperationalMode):
            msg = 'ModeState.mode must be an OperationalMode member'
            raise ValueError(msg)

        if not isinstance(self.trigger, str) or not self.trigger.strip():
            msg = 'ModeState.trigger must be a non-empty string'
            raise ValueError(msg)

        if not isinstance(self.transitioned_at, datetime):
            msg = 'ModeState.transitioned_at must be a datetime'
            raise ValueError(msg)


@dataclass
class StrategyModeState:
    '''Per-strategy operational mode with transition tracking.

    Args:
        strategy_id: Which strategy this mode belongs to.
        state: Operational mode state for this strategy.
    '''

    strategy_id: str
    state: ModeState = field(default_factory=ModeState)

    def __post_init__(self) -> None:
        '''Validate invariants at construction time.'''

        if not isinstance(self.strategy_id, str) or not self.strategy_id.strip():
            msg = 'StrategyModeState.strategy_id must be a non-empty string'
            raise ValueError(msg)

        if not isinstance(self.state, ModeState):
            msg = 'StrategyModeState.state must be a ModeState instance'
            raise ValueError(msg)
