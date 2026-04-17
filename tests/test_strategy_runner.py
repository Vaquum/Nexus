'''Tests for StrategyRunner.'''

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from nexus.core.domain.enums import OperationalMode, OrderSide
from nexus.infrastructure.praxis_connector.trade_outcome import TradeOutcome
from nexus.infrastructure.praxis_connector.trade_outcome_type import TradeOutcomeType
from nexus.infrastructure.strategy_event import StrategyEvent
from nexus.strategy import Action, ActionType, Strategy, StrategyContext, StrategyParams
from nexus.strategy.executor import StrategyExecutor
from nexus.strategy.runner import StrategyRunner
from nexus.strategy.signal import Signal


class StubStrategy(Strategy):

    def __init__(self, strategy_id: str) -> None:
        super().__init__(strategy_id)
        self.calls: list[str] = []

    def on_save(self) -> bytes:
        self.calls.append('on_save')
        return b'saved_state'

    def on_load(self, data: bytes) -> None:
        self.calls.append('on_load')
        self.loaded_data = data

    def on_startup(
        self,
        _params: StrategyParams,
        _context: StrategyContext,
    ) -> list[Action]:
        self.calls.append('on_startup')
        return [Action(ActionType.ENTER, direction=OrderSide.BUY, size=Decimal('1'), execution_mode='SingleShot', order_type='Market', deadline=300)]

    def on_signal(
        self,
        _signal: Signal,
        _params: StrategyParams,
        _context: StrategyContext,
    ) -> list[Action]:
        self.calls.append('on_signal')
        return [Action(ActionType.ENTER, direction=OrderSide.BUY, size=Decimal('1'), execution_mode='SingleShot', order_type='Market', deadline=300)]

    def on_outcome(
        self,
        _outcome: TradeOutcome,
        _params: StrategyParams,
        _context: StrategyContext,
    ) -> list[Action]:
        self.calls.append('on_outcome')
        return [Action(ActionType.EXIT, trade_id='t-1', size=Decimal('1'))]

    def on_timer(
        self,
        _timer_id: str,
        _params: StrategyParams,
        _context: StrategyContext,
    ) -> list[Action]:
        self.calls.append('on_timer')
        return []

    def on_shutdown(
        self,
        _params: StrategyParams,
        _context: StrategyContext,
    ) -> list[Action]:
        self.calls.append('on_shutdown')
        return [Action(ActionType.ABORT, trade_id='t-1')]

    def on_event_replay(self, event: StrategyEvent) -> None:
        self.calls.append('on_event_replay')
        self.replayed_event = event


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


def _make_event(strategy_id: str = 's1') -> StrategyEvent:
    return StrategyEvent(
        strategy_id=strategy_id,
        event_type='trade_outcome',
        realized_pnl=Decimal('-50'),
        timestamp=datetime.now(tz=timezone.utc),
    )


def _make_runner_with_strategies(*strategy_ids: str) -> tuple[StrategyRunner, dict[str, StubStrategy]]:
    strategies = {sid: StubStrategy(sid) for sid in strategy_ids}
    executors = {sid: StrategyExecutor(strat) for sid, strat in strategies.items()}
    runner = StrategyRunner(executors)
    return runner, strategies


class TestStrategyRunnerConstruction:

    def test_valid_construction(self) -> None:
        runner, _ = _make_runner_with_strategies('s1', 's2')

        assert runner is not None

    def test_empty_executors_allowed(self) -> None:
        runner = StrategyRunner({})

        assert runner is not None

    def test_non_dict_executors_rejected(self) -> None:
        with pytest.raises(ValueError, match='must be a dict'):
            StrategyRunner([])  # type: ignore[arg-type]

    def test_empty_strategy_id_key_rejected(self) -> None:
        strategy = StubStrategy('s1')
        executor = StrategyExecutor(strategy)

        with pytest.raises(ValueError, match='non-empty strings'):
            StrategyRunner({'': executor})

    def test_whitespace_strategy_id_key_rejected(self) -> None:
        strategy = StubStrategy('s1')
        executor = StrategyExecutor(strategy)

        with pytest.raises(ValueError, match='non-empty strings'):
            StrategyRunner({'  ': executor})

    def test_non_executor_value_rejected(self) -> None:
        with pytest.raises(ValueError, match='must be a StrategyExecutor'):
            StrategyRunner({'s1': 'not an executor'})  # type: ignore[dict-item]

    def test_mismatched_strategy_id_rejected(self) -> None:
        strategy = StubStrategy('actual_id')
        executor = StrategyExecutor(strategy)

        with pytest.raises(ValueError, match='does not match key'):
            StrategyRunner({'wrong_key': executor})

    def test_duplicate_key_after_normalization_rejected(self) -> None:
        strategy = StubStrategy('s1')
        executor = StrategyExecutor(strategy)

        with pytest.raises(ValueError, match='duplicate strategy_id'):
            StrategyRunner({'s1': executor, ' s1 ': executor})


class TestStrategyRunnerDispatch:

    def test_dispatch_startup_routes_to_executor(self) -> None:
        runner, strategies = _make_runner_with_strategies('s1', 's2')

        actions = runner.dispatch_startup('s1', _make_params(), _make_context())

        assert strategies['s1'].calls == ['on_startup']
        assert strategies['s2'].calls == []
        assert len(actions) == 1
        assert actions[0].action_type == ActionType.ENTER

    def test_dispatch_signal_routes_to_executor(self) -> None:
        runner, strategies = _make_runner_with_strategies('s1', 's2')

        actions = runner.dispatch_signal('s2', _make_signal(), _make_params(), _make_context())

        assert strategies['s1'].calls == []
        assert strategies['s2'].calls == ['on_signal']
        assert len(actions) == 1

    def test_dispatch_outcome_routes_to_executor(self) -> None:
        runner, strategies = _make_runner_with_strategies('s1')

        actions = runner.dispatch_outcome('s1', _make_outcome(), _make_params(), _make_context())

        assert strategies['s1'].calls == ['on_outcome']
        assert actions[0].action_type == ActionType.EXIT

    def test_dispatch_timer_routes_to_executor(self) -> None:
        runner, strategies = _make_runner_with_strategies('s1')

        actions = runner.dispatch_timer('s1', 'timer1', _make_params(), _make_context())

        assert strategies['s1'].calls == ['on_timer']
        assert actions == []

    def test_dispatch_shutdown_routes_to_executor(self) -> None:
        runner, strategies = _make_runner_with_strategies('s1')

        actions = runner.dispatch_shutdown('s1', _make_params(), _make_context())

        assert strategies['s1'].calls == ['on_shutdown']
        assert actions[0].action_type == ActionType.ABORT

    def test_dispatch_save_routes_to_executor(self) -> None:
        runner, strategies = _make_runner_with_strategies('s1')

        blob = runner.dispatch_save('s1')

        assert strategies['s1'].calls == ['on_save']
        assert blob == b'saved_state'

    def test_dispatch_load_routes_to_executor(self) -> None:
        runner, strategies = _make_runner_with_strategies('s1')

        runner.dispatch_load('s1', b'restored_state')

        assert strategies['s1'].calls == ['on_load']
        assert strategies['s1'].loaded_data == b'restored_state'

    def test_dispatch_event_replay_routes_to_executor(self) -> None:
        runner, strategies = _make_runner_with_strategies('s1', 's2')
        event = _make_event('s1')

        runner.dispatch_event_replay('s1', event)

        assert strategies['s1'].calls == ['on_event_replay']
        assert strategies['s1'].replayed_event == event
        assert strategies['s2'].calls == []


class TestStrategyRunnerUnknownStrategy:

    def test_dispatch_startup_unknown_strategy_raises(self) -> None:
        runner, _ = _make_runner_with_strategies('s1')

        with pytest.raises(ValueError, match='unknown strategy_id'):
            runner.dispatch_startup('unknown', _make_params(), _make_context())

    def test_dispatch_with_whitespace_padded_id_normalizes(self) -> None:
        runner, strategies = _make_runner_with_strategies('s1')

        actions = runner.dispatch_startup(' s1 ', _make_params(), _make_context())

        assert strategies['s1'].calls == ['on_startup']
        assert len(actions) == 1

    def test_dispatch_signal_unknown_strategy_raises(self) -> None:
        runner, _ = _make_runner_with_strategies('s1')

        with pytest.raises(ValueError, match='unknown strategy_id'):
            runner.dispatch_signal('unknown', _make_signal(), _make_params(), _make_context())

    def test_dispatch_outcome_unknown_strategy_raises(self) -> None:
        runner, _ = _make_runner_with_strategies('s1')

        with pytest.raises(ValueError, match='unknown strategy_id'):
            runner.dispatch_outcome('unknown', _make_outcome(), _make_params(), _make_context())

    def test_dispatch_timer_unknown_strategy_raises(self) -> None:
        runner, _ = _make_runner_with_strategies('s1')

        with pytest.raises(ValueError, match='unknown strategy_id'):
            runner.dispatch_timer('unknown', 'timer1', _make_params(), _make_context())

    def test_dispatch_shutdown_unknown_strategy_raises(self) -> None:
        runner, _ = _make_runner_with_strategies('s1')

        with pytest.raises(ValueError, match='unknown strategy_id'):
            runner.dispatch_shutdown('unknown', _make_params(), _make_context())

    def test_dispatch_load_unknown_strategy_raises(self) -> None:
        runner, _ = _make_runner_with_strategies('s1')

        with pytest.raises(ValueError, match='unknown strategy_id'):
            runner.dispatch_load('unknown', b'data')

    def test_dispatch_event_replay_unknown_strategy_raises(self) -> None:
        runner, _ = _make_runner_with_strategies('s1')
        event = _make_event('s1')

        with pytest.raises(ValueError, match='unknown strategy_id'):
            runner.dispatch_event_replay('unknown', event)


class TestStrategyRunnerMultipleStrategies:

    def test_dispatch_to_multiple_strategies_independently(self) -> None:
        runner, strategies = _make_runner_with_strategies('s1', 's2', 's3')

        runner.dispatch_startup('s1', _make_params(), _make_context())
        runner.dispatch_signal('s2', _make_signal(), _make_params(), _make_context())
        runner.dispatch_shutdown('s3', _make_params(), _make_context())

        assert strategies['s1'].calls == ['on_startup']
        assert strategies['s2'].calls == ['on_signal']
        assert strategies['s3'].calls == ['on_shutdown']
