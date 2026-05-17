'''Tests for `PredictLoop.tick_once` synchronous single-shot entry point.'''

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

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

    def test_chain_markers_present_and_ordered_in_both_paths(self) -> None:
        '''Static-source drift sentinel: both `_tick` and `tick_once` must
        invoke the same chain steps in the same order.

        `tick_once` deliberately duplicates the chain `_tick` runs (rather
        than extracting a shared helper) to keep `_tick` byte-identical for
        production Timer callers. Duplication invites silent drift — this
        test inspects the source of both methods and asserts the chain
        markers appear in the canonical order in each. A future edit that
        adds, removes, or reorders a step in only one path fails CI here.
        '''

        canonical_order = [
            '_extract_kline_size',
            '_market_data_provider',
            'produce_signal',
            '_log_signal',
            '_context_provider',
            'dispatch_signal',
            '_action_submit',
        ]

        for method in (PredictLoop._tick, PredictLoop.tick_once):
            source = inspect.getsource(method)
            positions: list[tuple[str, int]] = []

            for marker in canonical_order:
                idx = source.find(marker)
                assert idx != -1, (
                    f'{method.__name__} does not invoke {marker!r}; '
                    f'chain duplication has drifted'
                )
                positions.append((marker, idx))

            ordered_markers = [m for m, _ in sorted(positions, key=lambda mp: mp[1])]
            assert ordered_markers == canonical_order, (
                f'{method.__name__} chain order is {ordered_markers}; '
                f'expected {canonical_order}'
            )

    def test_chain_invoked_in_same_order_as_tick(self) -> None:
        '''Real runtime parity: drive both `_tick` and `tick_once` with
        identical setup and assert the recorded sequence of chain side
        effects is identical.

        `_tick` is driven directly with `_running` set to True (bypassing
        `start()`) and `_schedule_locked` no-op'd so the call does not
        spawn a follow-up Timer. The recorded event list must match the
        one from `tick_once` byte-for-byte. A future edit that changes
        the chain in only one of the two methods produces divergent
        event lists and fails this test.
        '''

        action = Action(
            action_type=ActionType.ENTER,
            direction=OrderSide.BUY,
            size=Decimal('0.01'),
            execution_mode=ExecutionMode.SINGLE_SHOT,
            order_type=OrderType.MARKET,
            deadline=300,
        )

        def make_loop() -> tuple[PredictLoop, WiredSensor, list[str]]:
            events: list[str] = []
            runner = MagicMock(spec=StrategyRunner)

            def market_provider(kline_size: int) -> pl.DataFrame:
                events.append(f'market_data:{kline_size}')
                return _mock_market_data_provider(kline_size)

            def context_provider(strategy_id: str) -> StrategyContext:
                events.append(f'context:{strategy_id}')
                return _mock_context_provider(strategy_id)

            def dispatch(*_args: object, **_kwargs: object) -> list[Action]:
                events.append('dispatch')
                return [action]

            runner.dispatch_signal.side_effect = dispatch

            def submitter(actions: list[Action], strategy_id: str) -> None:
                events.append(f'submit:{strategy_id}:{len(actions)}')

            wired = _make_wired(strategy_id='strat_a')
            loop = PredictLoop(
                runner=runner,
                wired_sensors=[wired],
                market_data_provider=market_provider,
                context_provider=context_provider,
                action_submit=submitter,
            )
            return loop, wired, events

        loop_a, wired_a, events_a = make_loop()
        loop_a.tick_once(wired_a)

        loop_b, wired_b, events_b = make_loop()
        loop_b._running = True
        with patch.object(loop_b, '_schedule_locked', lambda *_args, **_kwargs: None):
            try:
                loop_b._tick(wired_b)
            finally:
                loop_b._running = False

        assert events_a == events_b

    def test_chain_invoked_in_order_at_runtime(self) -> None:
        '''Runtime call-order check for `tick_once`: the four chain steps
        with observable side effects (market_data_provider, context_provider,
        runner.dispatch_signal, action_submit) fire exactly once each in the
        canonical order for a single tick that produces actions.
        '''

        action = Action(
            action_type=ActionType.ENTER,
            direction=OrderSide.BUY,
            size=Decimal('0.01'),
            execution_mode=ExecutionMode.SINGLE_SHOT,
            order_type=OrderType.MARKET,
            deadline=300,
        )
        runner = MagicMock(spec=StrategyRunner)
        events: list[str] = []

        def tracking_market_data_provider(kline_size: int) -> pl.DataFrame:
            events.append(f'market_data:{kline_size}')
            return _mock_market_data_provider(kline_size)

        def tracking_context_provider(strategy_id: str) -> StrategyContext:
            events.append(f'context:{strategy_id}')
            return _mock_context_provider(strategy_id)

        def tracking_dispatch(*_args: object, **_kwargs: object) -> list[Action]:
            events.append('dispatch')
            return [action]

        runner.dispatch_signal.side_effect = tracking_dispatch

        def tracking_submitter(actions: list[Action], strategy_id: str) -> None:
            events.append(f'submit:{strategy_id}:{len(actions)}')

        wired = _make_wired(strategy_id='strat_a')
        loop = PredictLoop(
            runner=runner,
            wired_sensors=[wired],
            market_data_provider=tracking_market_data_provider,
            context_provider=tracking_context_provider,
            action_submit=tracking_submitter,
        )

        loop.tick_once(wired)

        assert events == [
            'market_data:3600',
            'context:strat_a',
            'dispatch',
            'submit:strat_a:1',
        ]
        dispatch_args = runner.dispatch_signal.call_args
        assert dispatch_args[0][0] == 'strat_a'
        assert isinstance(dispatch_args[0][1], Signal)


class TestPredictLoopSignalLogging:

    def test_logs_signal_before_dispatch(self, caplog: pytest.LogCaptureFixture) -> None:
        '''Every tick logs the produced Signal at INFO with
        strategy_id, sensor_id, predictor_fn_id, and values BEFORE
        the strategy runner is dispatched. Ordering is pinned by
        capturing caplog state inside `dispatch_signal`'s side
        effect: if the log record is already present when dispatch
        runs, the log came first.

        Without this a HOLD-only strategy is indistinguishable from
        a broken predict path because the strategy itself logs
        nothing on HOLD.
        '''

        runner = MagicMock(spec=StrategyRunner)
        log_records_at_dispatch_time: list[str] = []

        def capture_dispatch(*_args: object, **_kwargs: object) -> list[Action]:
            log_records_at_dispatch_time.extend(
                r.message for r in caplog.records
            )
            return []

        runner.dispatch_signal.side_effect = capture_dispatch
        wired = _make_wired(strategy_id='strat_a', sensor_id='exp:1')

        loop = PredictLoop(
            runner=runner,
            wired_sensors=[wired],
            market_data_provider=_mock_market_data_provider,
            context_provider=_mock_context_provider,
        )

        with caplog.at_level('INFO', logger='nexus.strategy.predict_loop'):
            loop.tick_once(wired)

        signal_records = [r for r in caplog.records if r.message == 'signal produced']
        assert len(signal_records) == 1
        record = signal_records[0]
        assert record.strategy_id == 'strat_a'
        assert record.sensor_id == 'exp:1'
        assert record.predictor_fn_id
        assert isinstance(record.values, dict)
        assert 'signal produced' in log_records_at_dispatch_time, (
            'log record was emitted AFTER dispatch_signal — ordering broken'
        )

    def test_values_for_log_passes_native_primitives_through(self) -> None:
        '''Native JSON primitives (int / float / str / bool) pass
        through unchanged. `Decimal` does NOT pass through — it is
        coerced to `str` to preserve precision and survive the
        orjson renderer; that case is covered in
        `test_values_for_log_coerces_json_unsafe_types`.
        '''

        from nexus.strategy.predict_loop import _values_for_log

        result = _values_for_log({
            'a': 1,
            'b': 0.42,
            'd': 'hello',
            'e': True,
        })
        assert result == {
            'a': 1,
            'b': 0.42,
            'd': 'hello',
            'e': True,
        }

    def test_values_for_log_truncates_long_sequences(self) -> None:
        '''A long sequence value is replaced with a length-summary
        so structured logs stay bounded. Short sequences (<= the
        threshold) pass through. `signal_producer._extract_values`
        already collapses numpy arrays to scalars before they reach
        the log so this guard is defensive — for any future
        predictor that returns a long vector instead of a scalar.
        '''

        from nexus.strategy.predict_loop import (
            _MAX_LOGGED_SEQUENCE_LEN,
            _values_for_log,
        )

        short = list(range(_MAX_LOGGED_SEQUENCE_LEN))
        long_ = list(range(_MAX_LOGGED_SEQUENCE_LEN + 1))
        big_array = np.zeros(10000)

        result = _values_for_log({
            'short': short,
            'long': long_,
            'big': big_array,
            'scalar': 0.42,
        })
        assert result['short'] == short
        assert isinstance(result['long'], str)
        assert f'len={len(long_)}' in result['long']
        assert isinstance(result['big'], str)
        assert 'size=10000' in result['big']
        assert 'type=ndarray' in result['big']
        assert result['scalar'] == 0.42

    def test_values_for_log_coerces_json_unsafe_types(self) -> None:
        '''The configured orjson renderer in observability.py has no
        `default=` callback and no `OPT_SERIALIZE_NUMPY` flag, so any
        non-native value reaching the structured log raises and the
        record is dropped via `logging.handler.handleError`. The
        helper must coerce known JSON-unsafe types regardless of
        length:

        * `Decimal` -> `str` (preserves precision; allowed by
          `Signal.values` post_init)
        * `numpy` scalars (`np.float64`, `np.int64`) -> Python
          primitive via `.item()`
        * short `numpy.ndarray` -> list of primitives
        * short `polars.Series` -> list of primitives
        '''

        from nexus.strategy.predict_loop import _values_for_log

        short_ndarray = np.array([1, 2, 3])
        short_series = pl.Series('x', [4, 5, 6])

        result = _values_for_log({
            'decimal': Decimal('3.14'),
            'np_float': np.float64(0.42),
            'np_int': np.int64(7),
            'ndarray': short_ndarray,
            'series': short_series,
        })

        assert result['decimal'] == '3.14'
        assert isinstance(result['decimal'], str)
        assert result['np_float'] == 0.42
        assert isinstance(result['np_float'], float)
        assert result['np_int'] == 7
        assert isinstance(result['np_int'], int)
        assert result['ndarray'] == [1, 2, 3]
        assert all(isinstance(x, int) for x in result['ndarray'])
        assert result['series'] == [4, 5, 6]
        assert all(isinstance(x, int) for x in result['series'])

    def test_values_for_log_summarizes_long_series_and_ndarrays(self) -> None:
        '''Long sequence-typed values are summarized by SIZE (total
        element count via .size / .len()), not first-axis length.
        This catches multi-dim ndarrays that the round-1 len()-based
        guard missed (round-1 TD-087).
        '''

        from nexus.strategy.predict_loop import _values_for_log

        big_2d = np.zeros((100, 100))
        big_series = pl.Series('x', list(range(1000)))

        result = _values_for_log({
            'big_2d': big_2d,
            'big_series': big_series,
        })

        assert isinstance(result['big_2d'], str)
        assert 'size=10000' in result['big_2d']
        assert 'type=ndarray' in result['big_2d']
        assert isinstance(result['big_series'], str)
        assert 'size=1000' in result['big_series']
        assert 'type=Series' in result['big_series']

    def test_values_for_log_recursively_coerces_list_contents(self) -> None:
        '''Pin: a `list` short enough to pass the length threshold is
        NOT passed through unchanged — its elements are recursively
        coerced. A `list[np.float64]` or `list[Decimal]` would
        otherwise reach the orjson renderer with JSON-unsafe leaves
        and drop the log record.
        '''

        from nexus.strategy.predict_loop import _values_for_log

        result = _values_for_log({
            'nested_floats': [np.float64(1.0), np.float64(2.0)],
            'nested_decimals': [Decimal('0.1'), Decimal('0.2')],
            'mixed': [1, np.int64(2), Decimal('3'), 'hello'],
        })

        assert result['nested_floats'] == [1.0, 2.0]
        assert all(isinstance(x, float) for x in result['nested_floats'])
        assert result['nested_decimals'] == ['0.1', '0.2']
        assert all(isinstance(x, str) for x in result['nested_decimals'])
        assert result['mixed'] == [1, 2, '3', 'hello']

    def test_values_for_log_recursively_coerces_tuple_contents(self) -> None:
        '''Tuples coerce to lists (orjson serializes both as arrays);
        contents are recursively coerced like lists.
        '''

        from nexus.strategy.predict_loop import _values_for_log

        result = _values_for_log({
            'pair': (np.float64(1.0), Decimal('2.0')),
        })
        assert result['pair'] == [1.0, '2.0']
        assert isinstance(result['pair'], list)

    def test_values_for_log_recursively_coerces_dict_values(self) -> None:
        '''Pin: a `dict` short enough to pass the length threshold has
        its VALUES recursively coerced and its KEYS stringified
        (orjson rejects dicts with non-string keys).
        '''

        from nexus.strategy.predict_loop import _values_for_log

        result = _values_for_log({
            'config': {
                'lr': np.float64(0.001),
                'threshold': Decimal('0.5'),
                'name': 'logreg',
            },
            'int_keys': {1: 'one', 2: 'two'},
        })

        assert result['config'] == {
            'lr': 0.001,
            'threshold': '0.5',
            'name': 'logreg',
        }
        assert result['int_keys'] == {'1': 'one', '2': 'two'}

    def test_values_for_log_summarizes_long_list_and_dict(self) -> None:
        '''Long list/dict are summarized rather than walked, so
        recursion cost is bounded.
        '''

        from nexus.strategy.predict_loop import (
            _MAX_LOGGED_SEQUENCE_LEN,
            _values_for_log,
        )

        long_list = list(range(_MAX_LOGGED_SEQUENCE_LEN + 1))
        long_dict = {str(i): i for i in range(_MAX_LOGGED_SEQUENCE_LEN + 1)}

        result = _values_for_log({
            'long_list': long_list,
            'long_dict': long_dict,
        })

        assert isinstance(result['long_list'], str)
        assert 'type=list' in result['long_list']
        assert f'len={len(long_list)}' in result['long_list']
        assert isinstance(result['long_dict'], str)
        assert 'type=dict' in result['long_dict']
        assert f'len={len(long_dict)}' in result['long_dict']

    def test_values_for_log_repr_fallback_for_unknown_types(self) -> None:
        '''Final defensive branch: any object that is neither a
        JSON-native primitive (int / float / bool / None / list /
        dict / str) nor one of the explicitly-handled types
        (Decimal / numpy.generic / ndarray / pl.Series) falls
        through to `repr(val)`. This makes the orjson safety
        contract true — the helper guarantees the renderer cannot
        crash on a returned value.
        '''

        from nexus.strategy.predict_loop import _values_for_log

        class _UnknownNoLen:
            def __repr__(self) -> str:
                return 'UnknownNoLen()'

        result = _values_for_log({
            'unknown': _UnknownNoLen(),
        })
        assert result['unknown'] == 'UnknownNoLen()'
        assert isinstance(result['unknown'], str)

    def test_values_for_log_output_is_orjson_serializable(self) -> None:
        '''End-to-end safety: every coerced value MUST round-trip
        through orjson (the configured renderer) without raising.
        This is the actual contract the helper's name claims.
        '''

        import orjson

        from nexus.strategy.predict_loop import _values_for_log

        class _UnknownNoLen:
            def __repr__(self) -> str:
                return 'UnknownNoLen()'

        coerced = _values_for_log({
            'decimal': Decimal('3.14'),
            'np_float': np.float64(0.42),
            'np_int': np.int64(7),
            'ndarray_short': np.array([1.0, 2.0, 3.0]),
            'ndarray_long': np.zeros(100),
            'series_short': pl.Series('x', [1, 2, 3]),
            'series_long': pl.Series('x', list(range(100))),
            'plain_int': 1,
            'plain_float': 0.42,
            'plain_str': 'hello',
            'plain_bool': True,
            'unknown': _UnknownNoLen(),
            'nested_list': [np.float64(1.0), Decimal('2.0')],
            'nested_dict': {1: np.int64(2), 'k': Decimal('3.0')},
        })

        encoded = orjson.dumps(coerced)
        decoded = orjson.loads(encoded)
        assert decoded['decimal'] == '3.14'
        assert decoded['np_float'] == 0.42
        assert decoded['np_int'] == 7
        assert decoded['ndarray_short'] == [1.0, 2.0, 3.0]
        assert 'size=100' in decoded['ndarray_long']
        assert decoded['series_short'] == [1, 2, 3]
        assert 'size=100' in decoded['series_long']
        assert decoded['unknown'] == 'UnknownNoLen()'
        assert decoded['nested_list'] == [1.0, '2.0']
        assert decoded['nested_dict'] == {'1': 2, 'k': '3.0'}
