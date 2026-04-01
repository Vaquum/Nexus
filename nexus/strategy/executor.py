'''Strategy executor for serialized callback execution.'''

from __future__ import annotations

import threading

from nexus.infrastructure.praxis_connector.trade_outcome import TradeOutcome
from nexus.strategy.action import Action
from nexus.strategy.base import Strategy
from nexus.strategy.context import StrategyContext
from nexus.strategy.params import StrategyParams
from nexus.strategy.signal import Signal


class StrategyExecutor:
    '''Wraps a Strategy instance to guarantee serialized callback execution.

    All dispatch methods acquire a lock before delegating to the underlying
    Strategy callback. This ensures only one callback executes at a time
    per strategy instance.

    Args:
        strategy: Strategy instance to wrap.
    '''

    def __init__(self, strategy: Strategy) -> None:
        if not isinstance(strategy, Strategy):
            msg = 'strategy must be a Strategy instance'
            raise ValueError(msg)

        self._strategy = strategy
        self._lock = threading.Lock()

    @property
    def strategy_id(self) -> str:
        '''Unique identifier of the wrapped strategy.'''

        return self._strategy.strategy_id

    def dispatch_startup(
        self,
        params: StrategyParams,
        context: StrategyContext,
    ) -> list[Action]:
        '''Dispatch startup event to strategy under lock.

        Args:
            params: Strategy parameters from manifest.
            context: Current strategy context.

        Returns:
            List of actions from strategy.
        '''

        with self._lock:
            return self._strategy.on_startup(params, context)

    def dispatch_signal(
        self,
        signal: Signal,
        params: StrategyParams,
        context: StrategyContext,
    ) -> list[Action]:
        '''Dispatch signal event to strategy under lock.

        Args:
            signal: Signal from predictor function.
            params: Strategy parameters from manifest.
            context: Current strategy context.

        Returns:
            List of actions from strategy.
        '''

        with self._lock:
            return self._strategy.on_signal(signal, params, context)

    def dispatch_outcome(
        self,
        outcome: TradeOutcome,
        params: StrategyParams,
        context: StrategyContext,
    ) -> list[Action]:
        '''Dispatch outcome event to strategy under lock.

        Args:
            outcome: Trade execution result.
            params: Strategy parameters from manifest.
            context: Current strategy context.

        Returns:
            List of actions from strategy.
        '''

        with self._lock:
            return self._strategy.on_outcome(outcome, params, context)

    def dispatch_timer(
        self,
        timer_id: str,
        params: StrategyParams,
        context: StrategyContext,
    ) -> list[Action]:
        '''Dispatch timer event to strategy under lock.

        Args:
            timer_id: Identifier of the timer that fired.
            params: Strategy parameters from manifest.
            context: Current strategy context.

        Returns:
            List of actions from strategy.
        '''

        with self._lock:
            return self._strategy.on_timer(timer_id, params, context)

    def dispatch_shutdown(
        self,
        params: StrategyParams,
        context: StrategyContext,
    ) -> list[Action]:
        '''Dispatch shutdown event to strategy under lock.

        Args:
            params: Strategy parameters from manifest.
            context: Current strategy context.

        Returns:
            List of actions from strategy.
        '''

        with self._lock:
            return self._strategy.on_shutdown(params, context)
