'''Tests for `PredictLoop.tick_once` synchronous single-shot entry point.'''

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import numpy as np
import polars as pl
import pytest

from nexus.core.domain.enums import OperationalMode, OrderSide
from nexus.core.domain.order_types import ExecutionMode, OrderType
from nexus.startup.sequencer import WiredSensor
from nexus.strategy.action import Action, ActionType
from nexus.strategy.context import StrategyContext
from nexus.strategy.predict_loop import PredictLoop
from nexus.strategy.runner import StrategyRunner
from nexus.strategy.signal import Signal


def _mock_sensor() -> MagicMock:
    sensor = MagicMock()
    sensor.predict.return_value = {
        '_preds': np.array([1]),
        '_probs': np.array([0.85]),
    }
    return sensor


def _mock_limen_manifest() -> MagicMock:
    manifest = MagicMock()
    override = MagicMock()

    x_train = pl.DataFrame({'f1': [1.0, 2.0], 'f2': [3.0, 4.0]})
    override.prepare_data.return_value = {'x_train': x_train}
    manifest.with_params_override.return_value = override
    manifest.data_source_config.params = {'kline_size': 3600}

    return manifest


def _make_wired(
    sensor_id: str = 'exp:1',
    strategy_id: str = 'strat_a',
    interval_seconds: int = 1,
) -> WiredSensor:
    return WiredSensor(
        sensor_id=sensor_id,
        sensor=_mock_sensor(),
        limen_manifest=_mock_limen_manifest(),
        round_params={'random_weights': 0.5},
        strategy_id=strategy_id,
        interval_seconds=interval_seconds,
    )


def _mock_market_data_provider(_kline_size: int) -> pl.DataFrame:
    return pl.DataFrame({
        'datetime': [datetime(2026, 1, 1, tzinfo=timezone.utc)],
        'open': [70000.0],
        'high': [71000.0],
        'low': [69000.0],
        'close': [70500.0],
        'volume': [100.0],
    })


def _empty_market_data_provider(_kline_size: int) -> pl.DataFrame:
    return pl.DataFrame()


def _mock_context_provider(_strategy_id: str) -> StrategyContext:
    return StrategyContext(
        positions=(),
        capital_available=Decimal('10000'),
        operational_mode=OperationalMode.ACTIVE,
    )


class TestPredictLoopTickOnce:

    def test_dispatches_signal_synchronously(self) -> None:
        '''tick_once dispatches a Signal to the runner without start().'''

        runner = MagicMock(spec=StrategyRunner)
        runner.dispatch_signal.return_value = []
        wired = _make_wired()

        loop = PredictLoop(
            runner=runner,
            wired_sensors=[wired],
            market_data_provider=_mock_market_data_provider,
            context_provider=_mock_context_provider,
        )

        loop.tick_once(wired)

        assert runner.dispatch_signal.call_count == 1
        call_args = runner.dispatch_signal.call_args
        assert call_args[0][0] == 'strat_a'
        assert isinstance(call_args[0][1], Signal)

    def test_does_not_require_start(self) -> None:
        '''tick_once works on a freshly-constructed loop with running=False.'''

        runner = MagicMock(spec=StrategyRunner)
        runner.dispatch_signal.return_value = []
        wired = _make_wired()

        loop = PredictLoop(
            runner=runner,
            wired_sensors=[wired],
            market_data_provider=_mock_market_data_provider,
            context_provider=_mock_context_provider,
        )

        assert loop.running is False
        loop.tick_once(wired)
        assert loop.running is False
        assert runner.dispatch_signal.called

    def test_does_not_schedule_follow_up_timer(self) -> None:
        '''tick_once does not populate _active_timers; caller owns cadence.'''

        runner = MagicMock(spec=StrategyRunner)
        runner.dispatch_signal.return_value = []
        wired = _make_wired()

        loop = PredictLoop(
            runner=runner,
            wired_sensors=[wired],
            market_data_provider=_mock_market_data_provider,
            context_provider=_mock_context_provider,
        )

        loop.tick_once(wired)

        assert loop._active_timers == {}

    def test_raises_when_timer_loop_is_running(self) -> None:
        '''tick_once raises RuntimeError when the Timer-driven loop is active.'''

        runner = MagicMock(spec=StrategyRunner)
        wired = _make_wired(interval_seconds=3600)

        loop = PredictLoop(
            runner=runner,
            wired_sensors=[wired],
            market_data_provider=_mock_market_data_provider,
            context_provider=_mock_context_provider,
        )

        loop.start()

        try:
            with pytest.raises(RuntimeError, match='must not be called while the Timer-driven loop is running'):
                loop.tick_once(wired)
        finally:
            loop.stop()

    def test_empty_market_data_skips_dispatch(self) -> None:
        '''Empty market data returns early without calling produce_signal.'''

        runner = MagicMock(spec=StrategyRunner)
        wired = _make_wired()

        loop = PredictLoop(
            runner=runner,
            wired_sensors=[wired],
            market_data_provider=_empty_market_data_provider,
            context_provider=_mock_context_provider,
        )

        loop.tick_once(wired)

        assert runner.dispatch_signal.call_count == 0

    def test_action_submit_called_with_returned_actions(self) -> None:
        '''Actions returned from dispatch_signal flow to action_submit.'''

        action = Action(
            action_type=ActionType.ENTER,
            direction=OrderSide.BUY,
            size=Decimal('0.01'),
            execution_mode=ExecutionMode.SINGLE_SHOT,
            order_type=OrderType.MARKET,
            deadline=300,
        )

        runner = MagicMock(spec=StrategyRunner)
        runner.dispatch_signal.return_value = [action]

        captured: list[tuple[list[Action], str]] = []

        def submitter(actions: list[Action], strategy_id: str) -> None:
            captured.append((actions, strategy_id))

        wired = _make_wired(strategy_id='strat_a')
        loop = PredictLoop(
            runner=runner,
            wired_sensors=[wired],
            market_data_provider=_mock_market_data_provider,
            context_provider=_mock_context_provider,
            action_submit=submitter,
        )

        loop.tick_once(wired)

        assert len(captured) == 1
        actions_arg, strategy_id_arg = captured[0]
        assert strategy_id_arg == 'strat_a'
        assert actions_arg == [action]

    def test_action_submit_not_called_for_empty_actions(self) -> None:
        '''Empty actions list does not invoke action_submit.'''

        runner = MagicMock(spec=StrategyRunner)
        runner.dispatch_signal.return_value = []

        submitter = MagicMock()
        wired = _make_wired()

        loop = PredictLoop(
            runner=runner,
            wired_sensors=[wired],
            market_data_provider=_mock_market_data_provider,
            context_provider=_mock_context_provider,
            action_submit=submitter,
        )

        loop.tick_once(wired)

        assert submitter.call_count == 0

    def test_action_submit_exception_propagates(self) -> None:
        '''Submitter raising propagates to the caller (no swallow).'''

        runner = MagicMock(spec=StrategyRunner)
        runner.dispatch_signal.return_value = [
            Action(action_type=ActionType.ABORT, command_id='cmd_x'),
        ]

        def submitter(_actions: list[Action], _strategy_id: str) -> None:
            msg = 'submitter blew up'
            raise RuntimeError(msg)

        wired = _make_wired()
        loop = PredictLoop(
            runner=runner,
            wired_sensors=[wired],
            market_data_provider=_mock_market_data_provider,
            context_provider=_mock_context_provider,
            action_submit=submitter,
        )

        with pytest.raises(RuntimeError, match='submitter blew up'):
            loop.tick_once(wired)

    def test_market_data_provider_exception_propagates(self) -> None:
        '''Market-data fetch raising propagates to the caller (no swallow).'''

        runner = MagicMock(spec=StrategyRunner)

        def failing_provider(_kline_size: int) -> pl.DataFrame:
            msg = 'transient fetch failure'
            raise RuntimeError(msg)

        wired = _make_wired()
        loop = PredictLoop(
            runner=runner,
            wired_sensors=[wired],
            market_data_provider=failing_provider,
            context_provider=_mock_context_provider,
        )

        with pytest.raises(RuntimeError, match='transient fetch failure'):
            loop.tick_once(wired)

    def test_dispatch_signal_exception_propagates(self) -> None:
        '''Runner dispatch raising propagates to the caller (no swallow).'''

        runner = MagicMock(spec=StrategyRunner)
        runner.dispatch_signal.side_effect = RuntimeError('dispatch broke')

        wired = _make_wired()
        loop = PredictLoop(
            runner=runner,
            wired_sensors=[wired],
            market_data_provider=_mock_market_data_provider,
            context_provider=_mock_context_provider,
        )

        with pytest.raises(RuntimeError, match='dispatch broke'):
            loop.tick_once(wired)

    def test_repeated_calls_independent(self) -> None:
        '''Two successive tick_once calls each dispatch independently.'''

        runner = MagicMock(spec=StrategyRunner)
        runner.dispatch_signal.return_value = []

        wired = _make_wired()
        loop = PredictLoop(
            runner=runner,
            wired_sensors=[wired],
            market_data_provider=_mock_market_data_provider,
            context_provider=_mock_context_provider,
        )

        loop.tick_once(wired)
        loop.tick_once(wired)

        assert runner.dispatch_signal.call_count == 2
        assert loop._active_timers == {}

    def test_chain_call_order_matches_timer_tick(self) -> None:
        '''Parity guard: tick_once invokes the same chain in the same order
        as Timer-driven `_tick`. Catches drift if `_tick` is edited later
        without updating the duplicated chain in `tick_once`.
        '''

        runner = MagicMock(spec=StrategyRunner)
        runner.dispatch_signal.return_value = []

        market_data_calls: list[int] = []
        context_calls: list[str] = []

        def tracking_market_data_provider(kline_size: int) -> pl.DataFrame:
            market_data_calls.append(kline_size)
            return _mock_market_data_provider(kline_size)

        def tracking_context_provider(strategy_id: str) -> StrategyContext:
            context_calls.append(strategy_id)
            return _mock_context_provider(strategy_id)

        wired = _make_wired(strategy_id='strat_a')
        loop = PredictLoop(
            runner=runner,
            wired_sensors=[wired],
            market_data_provider=tracking_market_data_provider,
            context_provider=tracking_context_provider,
        )

        loop.tick_once(wired)

        assert market_data_calls == [3600]
        assert context_calls == ['strat_a']
        assert runner.dispatch_signal.call_count == 1
        dispatch_args = runner.dispatch_signal.call_args
        assert dispatch_args[0][0] == 'strat_a'
        assert isinstance(dispatch_args[0][1], Signal)
