'''Tests for the single-process Conduit PredictLoop.'''

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import polars as pl
import pytest

from nexus.core.domain.enums import OperationalMode, OrderSide
from nexus.core.domain.order_types import ExecutionMode, OrderType
from nexus.startup.sequencer import SignalBinding
from nexus.strategy.action import Action, ActionType
from nexus.strategy.context import StrategyContext
from nexus.strategy.predict_loop import PredictLoop
from nexus.strategy.runner import StrategyRunner
from nexus.strategy.signal import Signal

_SERIES = 'time_15m'
_TS = 1_700_000_000_000_000_000
_CLOSE = 70500.0


def _fixed_clock(moment: datetime) -> Callable[[], datetime]:
    return lambda: moment


def _context_provider(_strategy_id: str) -> StrategyContext:
    return StrategyContext(
        positions=(),
        capital_available=Decimal('10000'),
        operational_mode=OperationalMode.ACTIVE,
    )


def _write_manifest(
    conduit_dir: Path,
    generated_at: datetime,
    *,
    series: str = _SERIES,
    cohort_id: str = 'cohort_a',
    name: str = 'alpha',
    include_series: bool = True,
) -> None:
    entries: dict[str, Any] = {}
    if include_series:
        entries[series] = {
            'cohort_id': cohort_id,
            'name': name,
            'path': f'{series}/latest.arrow',
            'rows': 1,
            'max_ts': _TS,
        }

    manifest = {
        'version': 1,
        'generated_at': generated_at.isoformat(),
        'series': entries,
    }
    (conduit_dir / 'serving_manifest.json').write_text(json.dumps(manifest))


def _write_conduit_frame(
    conduit_dir: Path,
    *,
    series: str = _SERIES,
    rows: list[dict[str, Any]] | None = None,
) -> None:
    if rows is None:
        rows = [
            {
                'ts': _TS,
                'prediction': 1,
                'probability': 0.85,
                'reason_code': 0,
            },
        ]

    frame = pl.DataFrame(
        rows,
        schema={
            'ts': pl.Int64,
            'prediction': pl.Int8,
            'probability': pl.Float64,
            'reason_code': pl.Int8,
        },
    )
    series_dir = conduit_dir / series
    series_dir.mkdir(parents=True, exist_ok=True)
    frame.write_ipc(series_dir / 'latest.arrow')


def _write_arrow_frame(
    arrow_dir: Path,
    *,
    series: str = _SERIES,
    rows: list[dict[str, Any]] | None = None,
) -> None:
    if rows is None:
        rows = [{'ts': _TS, 'close': _CLOSE}]

    frame = pl.DataFrame(rows, schema={'ts': pl.Int64, 'close': pl.Float64})
    series_dir = arrow_dir / series
    series_dir.mkdir(parents=True, exist_ok=True)
    frame.write_ipc(series_dir / 'latest.arrow')


def _build_fixture(
    tmp_path: Path,
    generated_at: datetime,
    **manifest_kwargs: Any,
) -> tuple[Path, Path]:
    conduit_dir = tmp_path / 'conduit'
    arrow_dir = tmp_path / 'arrow'
    conduit_dir.mkdir()
    arrow_dir.mkdir()
    _write_manifest(conduit_dir, generated_at, **manifest_kwargs)
    _write_conduit_frame(conduit_dir)
    _write_arrow_frame(arrow_dir)
    return conduit_dir, arrow_dir


def _make_loop(
    conduit_dir: Path,
    arrow_dir: Path,
    runner: StrategyRunner,
    *,
    clock: Callable[[], datetime],
    action_submit: Callable[[list[Action], str], None] | None = None,
    binding: SignalBinding | None = None,
) -> PredictLoop:
    return PredictLoop(
        runner=runner,
        signal_bindings=[binding or _binding()],
        context_provider=_context_provider,
        action_submit=action_submit,
        conduit_dir=conduit_dir,
        arrow_dir=arrow_dir,
        clock=clock,
    )


def _binding(
    strategy_id: str = 'strat_a',
    series: str = _SERIES,
    interval_seconds: int = 1,
) -> SignalBinding:
    return SignalBinding(
        strategy_id=strategy_id,
        series=series,
        interval_seconds=interval_seconds,
    )


class TestPredictLoopTick:

    def test_emits_signal_and_dispatches(self, tmp_path: Path) -> None:
        '''A fresh manifest + usable row emits a Signal with
        `_preds`/`_probs`/`close` and dispatches it.'''

        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        conduit_dir, arrow_dir = _build_fixture(tmp_path, now)

        runner = MagicMock(spec=StrategyRunner)
        runner.dispatch_signal.return_value = []
        loop = _make_loop(conduit_dir, arrow_dir, runner, clock=_fixed_clock(now))

        loop.tick_once(_binding())

        assert runner.dispatch_signal.call_count == 1
        call = runner.dispatch_signal.call_args
        assert call[0][0] == 'strat_a'
        signal = call[0][1]
        assert isinstance(signal, Signal)
        assert signal.predictor_fn_id == f'strat_a:{_SERIES}'
        assert signal.get('_preds') == 1
        assert signal.get('_probs') == 0.85
        assert signal.get('close') == _CLOSE

    def test_action_submit_called_with_returned_actions(self, tmp_path: Path) -> None:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        conduit_dir, arrow_dir = _build_fixture(tmp_path, now)

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

        loop = _make_loop(
            conduit_dir,
            arrow_dir,
            runner,
            clock=_fixed_clock(now),
            action_submit=submitter,
        )

        loop.tick_once(_binding())

        assert captured == [([action], 'strat_a')]

    def test_action_submit_not_called_for_empty_actions(self, tmp_path: Path) -> None:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        conduit_dir, arrow_dir = _build_fixture(tmp_path, now)

        runner = MagicMock(spec=StrategyRunner)
        runner.dispatch_signal.return_value = []
        submitter = MagicMock()
        loop = _make_loop(
            conduit_dir,
            arrow_dir,
            runner,
            clock=_fixed_clock(now),
            action_submit=submitter,
        )

        loop.tick_once(_binding())

        assert submitter.call_count == 0

    def test_stale_manifest_skips_dispatch(self, tmp_path: Path) -> None:
        '''A manifest older than 120s yields no dispatch.'''

        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        generated_at = now - timedelta(seconds=121)
        conduit_dir, arrow_dir = _build_fixture(tmp_path, generated_at)

        runner = MagicMock(spec=StrategyRunner)
        loop = _make_loop(conduit_dir, arrow_dir, runner, clock=_fixed_clock(now))

        loop.tick_once(_binding())

        assert runner.dispatch_signal.call_count == 0

    def test_series_missing_from_manifest_skips_dispatch(self, tmp_path: Path) -> None:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        conduit_dir, arrow_dir = _build_fixture(tmp_path, now, include_series=False)

        runner = MagicMock(spec=StrategyRunner)
        loop = _make_loop(conduit_dir, arrow_dir, runner, clock=_fixed_clock(now))

        loop.tick_once(_binding())

        assert runner.dispatch_signal.call_count == 0

    def test_non_binary_prediction_skips_dispatch(self, tmp_path: Path) -> None:
        '''A prediction outside {0, 1} is rejected without dispatch.'''

        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        conduit_dir = tmp_path / 'conduit'
        arrow_dir = tmp_path / 'arrow'
        conduit_dir.mkdir()
        arrow_dir.mkdir()
        _write_manifest(conduit_dir, now)
        _write_conduit_frame(
            conduit_dir,
            rows=[
                {'ts': _TS, 'prediction': 2, 'probability': 0.85, 'reason_code': 0},
            ],
        )
        _write_arrow_frame(arrow_dir)

        runner = MagicMock(spec=StrategyRunner)
        loop = _make_loop(conduit_dir, arrow_dir, runner, clock=_fixed_clock(now))

        loop.tick_once(_binding())

        assert runner.dispatch_signal.call_count == 0

    def test_z_suffixed_generated_at_is_parsed(self, tmp_path: Path) -> None:
        '''A `Z`-suffixed manifest timestamp parses as UTC and dispatches.'''

        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        conduit_dir = tmp_path / 'conduit'
        arrow_dir = tmp_path / 'arrow'
        conduit_dir.mkdir()
        arrow_dir.mkdir()
        manifest = {
            'version': 1,
            'generated_at': '2026-01-01T00:00:00Z',
            'series': {
                _SERIES: {
                    'cohort_id': 'cohort_a',
                    'name': 'alpha',
                    'path': f'{_SERIES}/latest.arrow',
                    'rows': 1,
                    'max_ts': _TS,
                },
            },
        }
        (conduit_dir / 'serving_manifest.json').write_text(json.dumps(manifest))
        _write_conduit_frame(conduit_dir)
        _write_arrow_frame(arrow_dir)

        runner = MagicMock(spec=StrategyRunner)
        runner.dispatch_signal.return_value = []
        loop = _make_loop(conduit_dir, arrow_dir, runner, clock=_fixed_clock(now))

        loop.tick_once(_binding())

        assert runner.dispatch_signal.call_count == 1

    def test_manifest_entry_missing_path_skips_dispatch(self, tmp_path: Path) -> None:
        '''A manifest series entry without `path` yields no dispatch.'''

        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        conduit_dir = tmp_path / 'conduit'
        arrow_dir = tmp_path / 'arrow'
        conduit_dir.mkdir()
        arrow_dir.mkdir()
        manifest = {
            'version': 1,
            'generated_at': now.isoformat(),
            'series': {
                _SERIES: {
                    'cohort_id': 'cohort_a',
                    'name': 'alpha',
                    'rows': 1,
                    'max_ts': _TS,
                },
            },
        }
        (conduit_dir / 'serving_manifest.json').write_text(json.dumps(manifest))
        _write_conduit_frame(conduit_dir)
        _write_arrow_frame(arrow_dir)

        runner = MagicMock(spec=StrategyRunner)
        loop = _make_loop(conduit_dir, arrow_dir, runner, clock=_fixed_clock(now))

        loop.tick_once(_binding())

        assert runner.dispatch_signal.call_count == 0

    def test_only_usable_reason_code_rows_used(self, tmp_path: Path) -> None:
        '''Rows with reason_code != 0 are ignored; the latest rc==0 row
        is chosen even when a later-ts unusable row exists.'''

        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        conduit_dir = tmp_path / 'conduit'
        arrow_dir = tmp_path / 'arrow'
        conduit_dir.mkdir()
        arrow_dir.mkdir()
        _write_manifest(conduit_dir, now)
        _write_conduit_frame(
            conduit_dir,
            rows=[
                {'ts': _TS, 'prediction': 1, 'probability': 0.85, 'reason_code': 0},
                {'ts': _TS + 1, 'prediction': 0, 'probability': 0.10, 'reason_code': 3},
            ],
        )
        _write_arrow_frame(arrow_dir)

        runner = MagicMock(spec=StrategyRunner)
        runner.dispatch_signal.return_value = []
        loop = _make_loop(conduit_dir, arrow_dir, runner, clock=_fixed_clock(now))

        loop.tick_once(_binding())

        signal = runner.dispatch_signal.call_args[0][1]
        assert signal.get('_preds') == 1

    def test_dedupe_same_ts_not_reemitted(self, tmp_path: Path) -> None:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        conduit_dir, arrow_dir = _build_fixture(tmp_path, now)

        runner = MagicMock(spec=StrategyRunner)
        runner.dispatch_signal.return_value = []
        loop = _make_loop(conduit_dir, arrow_dir, runner, clock=_fixed_clock(now))
        binding = _binding()

        loop.tick_once(binding)
        loop.tick_once(binding)

        assert runner.dispatch_signal.call_count == 1

    def test_newer_ts_reemitted_after_dedupe(self, tmp_path: Path) -> None:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        conduit_dir, arrow_dir = _build_fixture(tmp_path, now)

        runner = MagicMock(spec=StrategyRunner)
        runner.dispatch_signal.return_value = []
        loop = _make_loop(conduit_dir, arrow_dir, runner, clock=_fixed_clock(now))
        binding = _binding()

        loop.tick_once(binding)

        _write_conduit_frame(
            conduit_dir,
            rows=[
                {'ts': _TS + 10, 'prediction': 0, 'probability': 0.2, 'reason_code': 0},
            ],
        )
        _write_arrow_frame(arrow_dir, rows=[{'ts': _TS + 10, 'close': 71000.0}])

        loop.tick_once(binding)

        assert runner.dispatch_signal.call_count == 2

    def test_missing_arrow_price_skips_dispatch(self, tmp_path: Path) -> None:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        conduit_dir = tmp_path / 'conduit'
        arrow_dir = tmp_path / 'arrow'
        conduit_dir.mkdir()
        arrow_dir.mkdir()
        _write_manifest(conduit_dir, now)
        _write_conduit_frame(conduit_dir)
        _write_arrow_frame(arrow_dir, rows=[{'ts': _TS + 999, 'close': _CLOSE}])

        runner = MagicMock(spec=StrategyRunner)
        loop = _make_loop(conduit_dir, arrow_dir, runner, clock=_fixed_clock(now))

        loop.tick_once(_binding())

        assert runner.dispatch_signal.call_count == 0

    def test_prediction_one_maps_to_enter_preds(self, tmp_path: Path) -> None:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        conduit_dir, arrow_dir = _build_fixture(tmp_path, now)

        runner = MagicMock(spec=StrategyRunner)
        runner.dispatch_signal.return_value = []
        loop = _make_loop(conduit_dir, arrow_dir, runner, clock=_fixed_clock(now))

        loop.tick_once(_binding())

        assert runner.dispatch_signal.call_args[0][1].get('_preds') == 1

    def test_prediction_zero_maps_to_exit_preds(self, tmp_path: Path) -> None:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        conduit_dir = tmp_path / 'conduit'
        arrow_dir = tmp_path / 'arrow'
        conduit_dir.mkdir()
        arrow_dir.mkdir()
        _write_manifest(conduit_dir, now)
        _write_conduit_frame(
            conduit_dir,
            rows=[
                {'ts': _TS, 'prediction': 0, 'probability': 0.10, 'reason_code': 0},
            ],
        )
        _write_arrow_frame(arrow_dir)

        runner = MagicMock(spec=StrategyRunner)
        runner.dispatch_signal.return_value = []
        loop = _make_loop(conduit_dir, arrow_dir, runner, clock=_fixed_clock(now))

        loop.tick_once(_binding())

        assert runner.dispatch_signal.call_args[0][1].get('_preds') == 0


class TestPredictLoopCohortLogging:

    def test_cohort_change_logged(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        conduit_dir, arrow_dir = _build_fixture(tmp_path, now, cohort_id='cohort_a')

        runner = MagicMock(spec=StrategyRunner)
        runner.dispatch_signal.return_value = []
        loop = _make_loop(conduit_dir, arrow_dir, runner, clock=_fixed_clock(now))
        binding = _binding()

        with caplog.at_level('INFO', logger='nexus.strategy.predict_loop'):
            loop.tick_once(binding)

            _write_manifest(conduit_dir, now, cohort_id='cohort_b')
            _write_conduit_frame(
                conduit_dir,
                rows=[
                    {
                        'ts': _TS + 5,
                        'prediction': 1,
                        'probability': 0.9,
                        'reason_code': 0,
                    },
                ],
            )
            _write_arrow_frame(arrow_dir, rows=[{'ts': _TS + 5, 'close': 72000.0}])

            loop.tick_once(binding)

        changed = [r for r in caplog.records if r.message == 'cohort changed']
        assert len(changed) == 1
        assert changed[0].previous_cohort_id == 'cohort_a'
        assert changed[0].cohort_id == 'cohort_b'

    def test_cohort_does_not_gate_dispatch(self, tmp_path: Path) -> None:
        '''A cohort change is observational only: dispatch still fires.'''

        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        conduit_dir, arrow_dir = _build_fixture(tmp_path, now, cohort_id='cohort_b')

        runner = MagicMock(spec=StrategyRunner)
        runner.dispatch_signal.return_value = []
        loop = _make_loop(conduit_dir, arrow_dir, runner, clock=_fixed_clock(now))
        loop._last_cohort[('strat_a', _SERIES)] = 'cohort_a'

        loop.tick_once(_binding())

        assert runner.dispatch_signal.call_count == 1


class TestPredictLoopLogging:

    def test_logs_signal_before_dispatch(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        conduit_dir, arrow_dir = _build_fixture(tmp_path, now)

        runner = MagicMock(spec=StrategyRunner)
        seen_at_dispatch: list[str] = []

        def capture(*_args: object, **_kwargs: object) -> list[Action]:
            seen_at_dispatch.extend(r.message for r in caplog.records)
            return []

        runner.dispatch_signal.side_effect = capture
        loop = _make_loop(conduit_dir, arrow_dir, runner, clock=_fixed_clock(now))

        with caplog.at_level('INFO', logger='nexus.strategy.predict_loop'):
            loop.tick_once(_binding())

        produced = [r for r in caplog.records if r.message == 'signal produced']
        assert len(produced) == 1
        assert produced[0].strategy_id == 'strat_a'
        assert produced[0].predictor_fn_id == f'strat_a:{_SERIES}'
        assert isinstance(produced[0].values, dict)
        assert 'signal produced' in seen_at_dispatch


class TestPredictLoopTickOncePropagation:

    def test_dispatch_exception_propagates(self, tmp_path: Path) -> None:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        conduit_dir, arrow_dir = _build_fixture(tmp_path, now)

        runner = MagicMock(spec=StrategyRunner)
        runner.dispatch_signal.side_effect = RuntimeError('dispatch broke')
        loop = _make_loop(conduit_dir, arrow_dir, runner, clock=_fixed_clock(now))

        with pytest.raises(RuntimeError, match='dispatch broke'):
            loop.tick_once(_binding())

    def test_action_submit_exception_propagates(self, tmp_path: Path) -> None:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        conduit_dir, arrow_dir = _build_fixture(tmp_path, now)

        runner = MagicMock(spec=StrategyRunner)
        runner.dispatch_signal.return_value = [
            Action(action_type=ActionType.ABORT, command_id='cmd_x'),
        ]

        def submitter(_actions: list[Action], _strategy_id: str) -> None:
            msg = 'submitter blew up'
            raise RuntimeError(msg)

        loop = _make_loop(
            conduit_dir,
            arrow_dir,
            runner,
            clock=_fixed_clock(now),
            action_submit=submitter,
        )

        with pytest.raises(RuntimeError, match='submitter blew up'):
            loop.tick_once(_binding())

    def test_raises_when_scheduler_running(self, tmp_path: Path) -> None:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        conduit_dir, arrow_dir = _build_fixture(tmp_path, now)

        runner = MagicMock(spec=StrategyRunner)
        runner.dispatch_signal.return_value = []
        binding = _binding(interval_seconds=3600)
        loop = _make_loop(
            conduit_dir, arrow_dir, runner, clock=_fixed_clock(now), binding=binding,
        )

        loop.start()
        try:
            with pytest.raises(RuntimeError, match='scheduler loop is running'):
                loop.tick_once(binding)
        finally:
            loop.stop()


class TestPredictLoopScheduler:

    def test_start_and_stop(self, tmp_path: Path) -> None:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        conduit_dir, arrow_dir = _build_fixture(tmp_path, now)

        runner = MagicMock(spec=StrategyRunner)
        runner.dispatch_signal.return_value = []
        binding = _binding(interval_seconds=10)
        loop = _make_loop(
            conduit_dir, arrow_dir, runner, clock=_fixed_clock(now), binding=binding,
        )

        loop.start()
        assert loop.running is True

        loop.stop()
        assert loop.running is False

    def test_scheduler_dispatches(self, tmp_path: Path) -> None:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        conduit_dir, arrow_dir = _build_fixture(tmp_path, now)

        runner = MagicMock(spec=StrategyRunner)
        dispatched = threading.Event()

        def track(*_args: Any, **_kwargs: Any) -> list[Action]:
            dispatched.set()
            return []

        runner.dispatch_signal.side_effect = track
        loop = _make_loop(conduit_dir, arrow_dir, runner, clock=_fixed_clock(now))

        loop.start()
        try:
            assert dispatched.wait(timeout=3)
        finally:
            loop.stop()

        assert runner.dispatch_signal.call_args[0][0] == 'strat_a'

    def test_read_error_in_one_tick_does_not_stop_loop(self, tmp_path: Path) -> None:
        '''A first-tick read failure is caught; once the data appears the
        scheduler keeps polling and eventually dispatches.'''

        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        conduit_dir = tmp_path / 'conduit'
        arrow_dir = tmp_path / 'arrow'
        conduit_dir.mkdir()
        arrow_dir.mkdir()
        (conduit_dir / 'serving_manifest.json').write_text('{ not valid json')

        runner = MagicMock(spec=StrategyRunner)
        dispatched = threading.Event()

        def track(*_args: Any, **_kwargs: Any) -> list[Action]:
            dispatched.set()
            return []

        runner.dispatch_signal.side_effect = track
        loop = _make_loop(conduit_dir, arrow_dir, runner, clock=_fixed_clock(now))

        loop.start()
        try:
            time.sleep(0.5)
            assert loop.running is True

            _write_manifest(conduit_dir, now)
            _write_conduit_frame(conduit_dir)
            _write_arrow_frame(arrow_dir)

            assert dispatched.wait(timeout=3)
        finally:
            loop.stop()
