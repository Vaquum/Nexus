'''Tests for StrategyExecutor.'''

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from nexus.core.domain.enums import OperationalMode
from nexus.infrastructure.praxis_connector.trade_outcome import TradeOutcome
from nexus.infrastructure.praxis_connector.trade_outcome_type import TradeOutcomeType
from nexus.strategy import Action, ActionType, Strategy, StrategyContext, StrategyParams
from nexus.strategy.executor import StrategyExecutor
from nexus.strategy.signal import Signal


class StubStrategy(Strategy):

    def __init__(self, strategy_id: str) -> None:
        super().__init__(strategy_id)
        self.calls: list[str] = []
        self.delay: float = 0.0

    def on_save(self) -> bytes:
        return b''

    def on_load(self, data: bytes) -> None:
        pass

    def on_startup(
        self,
        _params: StrategyParams,
        _context: StrategyContext,
    ) -> list[Action]:
        if self.delay:
            time.sleep(self.delay)
        self.calls.append('on_startup')
        return [Action(ActionType.ENTER)]

    def on_signal(
        self,
        _signal: Signal,
        _params: StrategyParams,
        _context: StrategyContext,
    ) -> list[Action]:
        if self.delay:
            time.sleep(self.delay)
        self.calls.append('on_signal')
        return [Action(ActionType.ENTER)]

    def on_outcome(
        self,
        _outcome: TradeOutcome,
        _params: StrategyParams,
        _context: StrategyContext,
    ) -> list[Action]:
        if self.delay:
            time.sleep(self.delay)
        self.calls.append('on_outcome')
        return [Action(ActionType.EXIT)]

    def on_timer(
        self,
        _timer_id: str,
        _params: StrategyParams,
        _context: StrategyContext,
    ) -> list[Action]:
        if self.delay:
            time.sleep(self.delay)
        self.calls.append('on_timer')
        return []

    def on_shutdown(
        self,
        _params: StrategyParams,
        _context: StrategyContext,
    ) -> list[Action]:
        if self.delay:
            time.sleep(self.delay)
        self.calls.append('on_shutdown')
        return [Action(ActionType.ABORT)]


def _make_params() -> StrategyParams:
    return StrategyParams(raw={'key': 'value'})


def _make_context() -> StrategyContext:
    return StrategyContext(
        positions=(),
        capital_available=Decimal('1000'),
        operational_mode=OperationalMode.ACTIVE,
    )


def _make_signal() -> Signal:
    return Signal(
        predictor_fn_id='pred1',
        values={'score': 0.8},
        timestamp=datetime.now(tz=timezone.utc),
    )


def _make_outcome() -> TradeOutcome:
    return TradeOutcome(
        outcome_id='out1',
        command_id='cmd1',
        outcome_type=TradeOutcomeType.ACK,
        timestamp=datetime.now(tz=timezone.utc),
    )


class TestStrategyExecutorConstruction:

    def test_requires_strategy_instance(self) -> None:
        with pytest.raises(ValueError, match='must be a Strategy instance'):
            StrategyExecutor('not a strategy')  # type: ignore[arg-type]

    def test_strategy_id_exposed(self) -> None:
        strategy = StubStrategy('test_strat')
        executor = StrategyExecutor(strategy)

        assert executor.strategy_id == 'test_strat'


class TestStrategyExecutorDispatch:

    def test_dispatch_startup_delegates(self) -> None:
        strategy = StubStrategy('s1')
        executor = StrategyExecutor(strategy)

        actions = executor.dispatch_startup(_make_params(), _make_context())

        assert strategy.calls == ['on_startup']
        assert len(actions) == 1
        assert actions[0].action_type == ActionType.ENTER

    def test_dispatch_signal_delegates(self) -> None:
        strategy = StubStrategy('s1')
        executor = StrategyExecutor(strategy)

        actions = executor.dispatch_signal(_make_signal(), _make_params(), _make_context())

        assert strategy.calls == ['on_signal']
        assert len(actions) == 1
        assert actions[0].action_type == ActionType.ENTER

    def test_dispatch_outcome_delegates(self) -> None:
        strategy = StubStrategy('s1')
        executor = StrategyExecutor(strategy)

        actions = executor.dispatch_outcome(_make_outcome(), _make_params(), _make_context())

        assert strategy.calls == ['on_outcome']
        assert len(actions) == 1
        assert actions[0].action_type == ActionType.EXIT

    def test_dispatch_timer_delegates(self) -> None:
        strategy = StubStrategy('s1')
        executor = StrategyExecutor(strategy)

        actions = executor.dispatch_timer('timer1', _make_params(), _make_context())

        assert strategy.calls == ['on_timer']
        assert actions == []

    def test_dispatch_shutdown_delegates(self) -> None:
        strategy = StubStrategy('s1')
        executor = StrategyExecutor(strategy)

        actions = executor.dispatch_shutdown(_make_params(), _make_context())

        assert strategy.calls == ['on_shutdown']
        assert len(actions) == 1
        assert actions[0].action_type == ActionType.ABORT


class TestStrategyExecutorConcurrency:

    def test_concurrent_dispatch_serialized(self) -> None:
        strategy = StubStrategy('s1')
        strategy.delay = 0.05
        executor = StrategyExecutor(strategy)

        results: list[str] = []
        errors: list[Exception] = []

        def dispatch_startup() -> None:
            try:
                executor.dispatch_startup(_make_params(), _make_context())
                results.append('startup_done')
            except Exception as e:
                errors.append(e)

        def dispatch_signal() -> None:
            try:
                executor.dispatch_signal(_make_signal(), _make_params(), _make_context())
                results.append('signal_done')
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=dispatch_startup)
        t2 = threading.Thread(target=dispatch_signal)

        t1.start()
        time.sleep(0.01)
        t2.start()

        t1.join()
        t2.join()

        assert not errors
        assert len(results) == 2
        assert strategy.calls == ['on_startup', 'on_signal']

    def test_multiple_concurrent_dispatches_all_complete(self) -> None:
        strategy = StubStrategy('s1')
        strategy.delay = 0.01
        executor = StrategyExecutor(strategy)

        errors: list[Exception] = []

        def dispatch(method: str) -> None:
            try:
                if method == 'startup':
                    executor.dispatch_startup(_make_params(), _make_context())
                elif method == 'signal':
                    executor.dispatch_signal(_make_signal(), _make_params(), _make_context())
                elif method == 'timer':
                    executor.dispatch_timer('t1', _make_params(), _make_context())
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=dispatch, args=('startup',)),
            threading.Thread(target=dispatch, args=('signal',)),
            threading.Thread(target=dispatch, args=('timer',)),
        ]

        for t in threads:
            t.start()

        for t in threads:
            t.join()

        assert not errors
        assert len(strategy.calls) == 3
        assert set(strategy.calls) == {'on_startup', 'on_signal', 'on_timer'}
