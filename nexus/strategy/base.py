'''Abstract base class for trading strategies.

Defines the contract that all concrete strategies must implement,
including state persistence lifecycle hooks and event callbacks.
'''

from __future__ import annotations

from abc import ABC, abstractmethod

from nexus.infrastructure.praxis_connector.trade_outcome import TradeOutcome
from nexus.strategy.action import Action
from nexus.strategy.context import StrategyContext
from nexus.strategy.params import StrategyParams
from nexus.strategy.signal import Signal


class Strategy(ABC):
    '''Abstract base class for trading strategies.

    Concrete strategies inherit from this class and implement
    the required lifecycle methods for state persistence and
    event callbacks for signal processing and trade management.

    Args:
        strategy_id: Unique identifier for this strategy instance.
    '''

    _strategy_id: str

    def __init__(self, strategy_id: str) -> None:
        if not isinstance(strategy_id, str):
            msg = 'strategy_id must be a non-empty string'
            raise ValueError(msg)

        normalized = strategy_id.strip()

        if not normalized:
            msg = 'strategy_id must be a non-empty string'
            raise ValueError(msg)

        self._strategy_id = normalized

    @property
    def strategy_id(self) -> str:
        '''Unique identifier for this strategy instance.'''

        return self._strategy_id

    @abstractmethod
    def on_save(self) -> bytes:
        '''Serialize strategy state for persistence.

        Called by Strategy Runner for periodic checkpoints and shutdown.
        The strategy author decides what state to persist.

        Returns:
            Serialized state as bytes.
        '''

    @abstractmethod
    def on_load(self, data: bytes) -> None:
        '''Restore strategy state from persisted bytes.

        Called by Strategy Runner on startup and crash recovery.
        The data parameter contains bytes previously returned by on_save().

        Args:
            data: Serialized state from a prior on_save() call.
        '''

    @abstractmethod
    def on_startup(
        self,
        params: StrategyParams,
        context: StrategyContext,
    ) -> list[Action]:
        '''Handle strategy startup event.

        Called once when the strategy is first activated. Use for
        initial position setup or recovery actions.

        Args:
            params: Strategy parameters from manifest.
            context: Current strategy context.

        Returns:
            List of actions to execute, or empty list for no action.
        '''

    @abstractmethod
    def on_signal(
        self,
        signal: Signal,
        params: StrategyParams,
        context: StrategyContext,
    ) -> list[Action]:
        '''Handle incoming signal from predictor function.

        Called when a predictor function produces a signal for this strategy.

        Args:
            signal: Signal from predictor function.
            params: Strategy parameters from manifest.
            context: Current strategy context.

        Returns:
            List of actions to execute, or empty list for no action.
        '''

    @abstractmethod
    def on_outcome(
        self,
        outcome: TradeOutcome,
        params: StrategyParams,
        context: StrategyContext,
    ) -> list[Action]:
        '''Handle trade execution outcome.

        Called when an execution result arrives for an action this strategy requested.

        Args:
            outcome: Trade execution result.
            params: Strategy parameters from manifest.
            context: Current strategy context.

        Returns:
            List of actions to execute, or empty list for no action.
        '''

    @abstractmethod
    def on_timer(
        self,
        timer_id: str,
        params: StrategyParams,
        context: StrategyContext,
    ) -> list[Action]:
        '''Handle timer expiration event.

        Called when a registered timer fires.

        Args:
            timer_id: Identifier of the timer that fired.
            params: Strategy parameters from manifest.
            context: Current strategy context.

        Returns:
            List of actions to execute, or empty list for no action.
        '''

    @abstractmethod
    def on_shutdown(
        self,
        params: StrategyParams,
        context: StrategyContext,
    ) -> list[Action]:
        '''Handle strategy shutdown event.

        Called before the strategy is deactivated. Use for cleanup
        or final position management.

        Args:
            params: Strategy parameters from manifest.
            context: Current strategy context.

        Returns:
            List of actions to execute, or empty list for no action.
        '''
