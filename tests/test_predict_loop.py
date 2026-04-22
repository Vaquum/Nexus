'''Tests for PredictLoop timer-based signal generation.'''

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import polars as pl

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


def _mock_context_provider(_strategy_id: str) -> StrategyContext:
    return StrategyContext(
        positions=(),
        capital_available=Decimal('10000'),
        operational_mode=OperationalMode.ACTIVE,
    )


class TestPredictLoop:

    def test_start_and_stop(self) -> None:
        '''PredictLoop starts and stops without error.'''

        runner = MagicMock(spec=StrategyRunner)
        wired = _make_wired(interval_seconds=10)

        loop = PredictLoop(
            runner=runner,
            wired_sensors=[wired],
            market_data_provider=_mock_market_data_provider,
            context_provider=_mock_context_provider,
        )

        loop.start()
        assert loop.running is True

        loop.stop()
        assert loop.running is False

    def test_dispatches_signal(self) -> None:
        '''PredictLoop dispatches Signal to runner after timer fires.'''

        runner = MagicMock(spec=StrategyRunner)
        dispatched = threading.Event()

        def track_dispatch(*_args: Any, **_kwargs: Any) -> list:
            dispatched.set()
            return []

        runner.dispatch_signal.side_effect = track_dispatch
        wired = _make_wired(interval_seconds=1)

        loop = PredictLoop(
            runner=runner,
            wired_sensors=[wired],
            market_data_provider=_mock_market_data_provider,
            context_provider=_mock_context_provider,
        )

        loop.start()
        dispatched.wait(timeout=3)
        loop.stop()

        assert runner.dispatch_signal.called
        call_args = runner.dispatch_signal.call_args
        assert call_args[0][0] == 'strat_a'
        assert isinstance(call_args[0][1], Signal)

    def test_empty_market_data_skips(self) -> None:
        '''Empty market data logs warning and reschedules.'''

        runner = MagicMock(spec=StrategyRunner)

        def empty_provider(_kline_size: int) -> pl.DataFrame:
            return pl.DataFrame()

        wired = _make_wired(interval_seconds=1)

        loop = PredictLoop(
            runner=runner,
            wired_sensors=[wired],
            market_data_provider=empty_provider,
            context_provider=_mock_context_provider,
        )

        loop.start()
        time.sleep(1.5)
        loop.stop()

        assert not runner.dispatch_signal.called

    def test_predict_error_does_not_crash_loop(self) -> None:
        '''Exception in predict cycle is caught, loop continues.'''

        runner = MagicMock(spec=StrategyRunner)
        call_count = 0
        dispatched_second = threading.Event()

        def failing_then_ok(kline_size: int) -> pl.DataFrame:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                msg = 'transient error'
                raise RuntimeError(msg)
            dispatched_second.set()
            return _mock_market_data_provider(kline_size)

        wired = _make_wired(interval_seconds=1)

        loop = PredictLoop(
            runner=runner,
            wired_sensors=[wired],
            market_data_provider=failing_then_ok,
            context_provider=_mock_context_provider,
        )

        loop.start()
        dispatched_second.wait(timeout=4)
        loop.stop()

        assert call_count >= 2

    def test_stop_prevents_further_dispatch(self) -> None:
        '''After stop, no more signals are dispatched.'''

        runner = MagicMock(spec=StrategyRunner)
        wired = _make_wired(interval_seconds=1)

        loop = PredictLoop(
            runner=runner,
            wired_sensors=[wired],
            market_data_provider=_mock_market_data_provider,
            context_provider=_mock_context_provider,
        )

        loop.start()
        loop.stop()

        runner.dispatch_signal.reset_mock()
        time.sleep(1.5)

        assert not runner.dispatch_signal.called

    def test_action_submit_called_with_returned_actions(self) -> None:
        '''Actions returned from dispatch_signal are forwarded to action_submit.'''

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

        submitted = threading.Event()
        captured: list[tuple[list[Action], str]] = []

        def submitter(actions: list[Action], strategy_id: str) -> None:
            captured.append((actions, strategy_id))
            submitted.set()

        wired = _make_wired(strategy_id='strat_a', interval_seconds=1)
        loop = PredictLoop(
            runner=runner,
            wired_sensors=[wired],
            market_data_provider=_mock_market_data_provider,
            context_provider=_mock_context_provider,
            action_submit=submitter,
        )

        loop.start()
        submitted.wait(timeout=3)
        loop.stop()

        assert captured
        actions_arg, strategy_id_arg = captured[0]
        assert strategy_id_arg == 'strat_a'
        assert actions_arg == [action]

    def test_action_submit_not_called_for_empty_actions(self) -> None:
        '''dispatch_signal returning [] does not invoke action_submit.'''

        runner = MagicMock(spec=StrategyRunner)
        runner.dispatch_signal.return_value = []

        dispatched = threading.Event()

        def track(*_args: Any, **_kwargs: Any) -> list:
            dispatched.set()
            return []

        runner.dispatch_signal.side_effect = track
        submitter = MagicMock()

        wired = _make_wired(interval_seconds=1)
        loop = PredictLoop(
            runner=runner,
            wired_sensors=[wired],
            market_data_provider=_mock_market_data_provider,
            context_provider=_mock_context_provider,
            action_submit=submitter,
        )

        loop.start()
        did_dispatch = dispatched.wait(timeout=3)
        loop.stop()

        assert did_dispatch is True
        assert submitter.call_count == 0

    def test_action_submit_exception_does_not_kill_loop(self) -> None:
        '''Submitter raising leaves the loop running and reschedules.'''

        runner = MagicMock(spec=StrategyRunner)
        runner.dispatch_signal.return_value = [
            Action(action_type=ActionType.ABORT, command_id='cmd_x'),
        ]

        call_count = threading.Event()
        calls = {'n': 0}

        def submitter(_actions: list[Action], _strategy_id: str) -> None:
            calls['n'] += 1
            if calls['n'] >= 2:
                call_count.set()
            raise RuntimeError('submitter blew up')

        wired = _make_wired(interval_seconds=1)
        loop = PredictLoop(
            runner=runner,
            wired_sensors=[wired],
            market_data_provider=_mock_market_data_provider,
            context_provider=_mock_context_provider,
            action_submit=submitter,
        )

        loop.start()
        call_count.wait(timeout=5)
        loop.stop()

        assert calls['n'] >= 2

    def test_multiple_sensors(self) -> None:
        '''PredictLoop handles multiple sensors dispatching to different strategies.'''

        runner = MagicMock(spec=StrategyRunner)
        dispatched = threading.Event()
        strategies_seen: list[str] = []

        def track_dispatch(*args: Any, **_kwargs: Any) -> list:
            strategies_seen.append(args[0])
            if len(strategies_seen) >= 2:
                dispatched.set()
            return []

        runner.dispatch_signal.side_effect = track_dispatch

        wired_a = _make_wired(sensor_id='exp:1', strategy_id='strat_a', interval_seconds=1)
        wired_b = _make_wired(sensor_id='exp:2', strategy_id='strat_b', interval_seconds=1)

        loop = PredictLoop(
            runner=runner,
            wired_sensors=[wired_a, wired_b],
            market_data_provider=_mock_market_data_provider,
            context_provider=_mock_context_provider,
        )

        loop.start()
        dispatched.wait(timeout=3)
        loop.stop()

        assert 'strat_a' in strategies_seen
        assert 'strat_b' in strategies_seen
