'''Operational mode state for instance and per-strategy tracking.

Mutable dataclasses holding current mode and what triggered
the most recent transition.
'''

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from nexus.core.domain.enums import OperationalMode

__all__ = ['HaltHold', 'ModeState', 'OperationalHolds', 'StrategyModeState']


@dataclass
class HaltHold:
    '''A single reason an instance is held in HALTED, sticky until cleared.

    Args:
        active: Whether this reason currently holds the instance halted.
        reason: Short description of why the hold was placed.
        since: When the hold was placed, or `None` when no timestamp was
            recorded. The ModeController sets it on every hold it places;
            it is not required to be set when `active` is True (a hold
            decoded or constructed without a timestamp keeps `None`).
    '''

    active: bool = False
    reason: str = ''
    since: datetime | None = None

    def __post_init__(self) -> None:
        '''Validate invariants at construction time.'''

        if not isinstance(self.active, bool):
            msg = 'HaltHold.active must be a bool'
            raise ValueError(msg)

        if not isinstance(self.reason, str):
            msg = 'HaltHold.reason must be a string'
            raise ValueError(msg)

        if self.since is not None and not isinstance(self.since, datetime):
            msg = 'HaltHold.since must be a datetime or None'
            raise ValueError(msg)


@dataclass
class OperationalHolds:
    '''The non-health reasons that hold an instance in HALTED.

    Each hold is independent: a health-derived recovery cannot lift a
    hold, and clearing one hold does not clear the others.

    Args:
        manual_hold: Hold placed by a manual halt.
        risk_daily_loss: Hold placed when the daily-loss breaker trips.
        risk_drawdown: Hold placed when the drawdown breaker trips.
    '''

    manual_hold: HaltHold = field(default_factory=HaltHold)
    risk_daily_loss: HaltHold = field(default_factory=HaltHold)
    risk_drawdown: HaltHold = field(default_factory=HaltHold)

    def any_active(self) -> bool:
        '''Return whether any hold currently forces HALTED.'''

        return (
            self.manual_hold.active
            or self.risk_daily_loss.active
            or self.risk_drawdown.active
        )


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
