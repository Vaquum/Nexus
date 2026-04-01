'''Strategy runner for orchestrating multiple strategy executors.'''

from __future__ import annotations

from nexus.infrastructure.praxis_connector.trade_outcome import TradeOutcome
from nexus.strategy.action import Action
from nexus.strategy.context import StrategyContext
from nexus.strategy.executor import StrategyExecutor
from nexus.strategy.params import StrategyParams
from nexus.strategy.signal import Signal


class StrategyRunner:
    '''Orchestrates dispatch to multiple strategy executors.

    Routes events to the appropriate StrategyExecutor by strategy_id.
    Each executor guarantees serialized callback execution for its strategy.

    Args:
        executors: Mapping of strategy_id to StrategyExecutor.
    '''

    def __init__(self, executors: dict[str, StrategyExecutor]) -> None:
        if not isinstance(executors, dict):
            msg = 'executors must be a dict'
            raise ValueError(msg)

        for strategy_id, executor in executors.items():
            if not isinstance(strategy_id, str) or not strategy_id.strip():
                msg = 'executor keys must be non-empty strings'
                raise ValueError(msg)

            if not isinstance(executor, StrategyExecutor):
                msg = f'executor for {strategy_id!r} must be a StrategyExecutor'
                raise ValueError(msg)

            if executor.strategy_id != strategy_id:
                msg = f'executor strategy_id {executor.strategy_id!r} does not match key {strategy_id!r}'
                raise ValueError(msg)

        self._executors = dict(executors)

    def _get_executor(self, strategy_id: str) -> StrategyExecutor:
        '''Get executor for strategy_id or raise ValueError.'''

        executor = self._executors.get(strategy_id)

        if executor is None:
            msg = f'unknown strategy_id: {strategy_id!r}'
            raise ValueError(msg)

        return executor

    def dispatch_startup(
        self,
        strategy_id: str,
        params: StrategyParams,
        context: StrategyContext,
    ) -> list[Action]:
        '''Dispatch startup event to strategy.

        Args:
            strategy_id: Target strategy identifier.
            params: Strategy parameters from manifest.
            context: Current strategy context.

        Returns:
            List of actions from strategy.

        Raises:
            ValueError: If strategy_id is unknown.
        '''

        return self._get_executor(strategy_id).dispatch_startup(params, context)

    def dispatch_signal(
        self,
        strategy_id: str,
        signal: Signal,
        params: StrategyParams,
        context: StrategyContext,
    ) -> list[Action]:
        '''Dispatch signal event to strategy.

        Args:
            strategy_id: Target strategy identifier.
            signal: Signal from predictor function.
            params: Strategy parameters from manifest.
            context: Current strategy context.

        Returns:
            List of actions from strategy.

        Raises:
            ValueError: If strategy_id is unknown.
        '''

        return self._get_executor(strategy_id).dispatch_signal(signal, params, context)

    def dispatch_outcome(
        self,
        strategy_id: str,
        outcome: TradeOutcome,
        params: StrategyParams,
        context: StrategyContext,
    ) -> list[Action]:
        '''Dispatch outcome event to strategy.

        Args:
            strategy_id: Target strategy identifier.
            outcome: Trade execution result.
            params: Strategy parameters from manifest.
            context: Current strategy context.

        Returns:
            List of actions from strategy.

        Raises:
            ValueError: If strategy_id is unknown.
        '''

        return self._get_executor(strategy_id).dispatch_outcome(outcome, params, context)

    def dispatch_timer(
        self,
        strategy_id: str,
        timer_id: str,
        params: StrategyParams,
        context: StrategyContext,
    ) -> list[Action]:
        '''Dispatch timer event to strategy.

        Args:
            strategy_id: Target strategy identifier.
            timer_id: Identifier of the timer that fired.
            params: Strategy parameters from manifest.
            context: Current strategy context.

        Returns:
            List of actions from strategy.

        Raises:
            ValueError: If strategy_id is unknown.
        '''

        return self._get_executor(strategy_id).dispatch_timer(timer_id, params, context)

    def dispatch_shutdown(
        self,
        strategy_id: str,
        params: StrategyParams,
        context: StrategyContext,
    ) -> list[Action]:
        '''Dispatch shutdown event to strategy.

        Args:
            strategy_id: Target strategy identifier.
            params: Strategy parameters from manifest.
            context: Current strategy context.

        Returns:
            List of actions from strategy.

        Raises:
            ValueError: If strategy_id is unknown.
        '''

        return self._get_executor(strategy_id).dispatch_shutdown(params, context)
