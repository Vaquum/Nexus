'''Timer-based predict loop for signal generation.

Runs per-sensor timers that call produce_signal and dispatch
the resulting Signal to the bound strategy. Captures the
list[Action] returned from each dispatch and forwards it to an
injected `action_submit` callback (typically `submit_actions`
from `nexus.strategy.action_submit`, curried with validator,
config, state, and PraxisOutbound by the launcher).

Logs every signal at INFO immediately after `produce_signal()`
returns, BEFORE the strategy's dispatch handler is called. This
gives operators full visibility into predictor output regardless
of whether the strategy chose to emit an action — without this,
a HOLD-only strategy (every prediction maps to no-action) is
indistinguishable from a broken predict path because the
strategy itself logs nothing on HOLD.
'''

from __future__ import annotations

import logging
import multiprocessing
import os
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ProcessPoolExecutor
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from shutil import rmtree
from typing import Any

import numpy as np
import polars as pl

from limen.experiment.trainer.trainer import Trainer

from nexus.startup.sequencer import WiredSensor
from nexus.strategy.action import Action
from nexus.strategy.context import StrategyContext
from nexus.strategy.params import StrategyParams
from nexus.strategy.runner import StrategyRunner
from nexus.strategy.signal import Signal
from nexus.strategy.signal_producer import produce_signal

__all__ = ['ActionSubmitter', 'PredictLoop']

_log = logging.getLogger(__name__)

ActionSubmitter = Callable[[list[Action], str], None]

_MAX_LOGGED_SEQUENCE_LEN = 16

_SCHEDULER_POLL_SECONDS = 0.25
_PREDICT_MAX_WORKERS_ENV = 'NEXUS_PREDICT_MAX_WORKERS'
_DEFAULT_PREDICT_MAX_WORKERS = 16
_POLARS_MAX_THREADS_ENV = 'NEXUS_PREDICT_POLARS_MAX_THREADS'
_DEFAULT_POLARS_MAX_THREADS = '1'
_WORKER_MARKET_DATA_CACHE_MAX = 4

_WORKER_MANIFESTS: dict[str, Any] = {}
_WORKER_MARKET_DATA: dict[str, pl.DataFrame] = {}


@dataclass(frozen=True)
class PredictTask:
    '''Picklable description of one sensor's predict work for a worker.

    Carries everything a worker process needs to rebuild the wired sensor
    and run `produce_signal` without sharing the parent's in-memory Limen
    manifest. The heavy manifest is reconstructed worker-side from
    `experiment_dir` (cached per worker); only the small fitted `sensor`
    and `round_params` travel through the submit pickle.
    '''

    sensor_id: str
    sensor: Any
    round_params: dict[str, Any]
    strategy_id: str
    interval_seconds: int
    experiment_dir: str


def _resolve_predict_max_workers() -> int:
    '''Return the bounded predict worker count from the environment.

    Reads `NEXUS_PREDICT_MAX_WORKERS` (default 16). A non-positive or
    unparseable value falls back to the default so a misconfiguration
    cannot disable bounding and recreate the per-sensor blowup.

    Returns:
        The maximum number of concurrent predict worker processes.
    '''

    raw = os.environ.get(_PREDICT_MAX_WORKERS_ENV)
    if raw is None:
        return _DEFAULT_PREDICT_MAX_WORKERS

    try:
        parsed = int(raw)
    except ValueError:
        return _DEFAULT_PREDICT_MAX_WORKERS

    return parsed if parsed >= 1 else _DEFAULT_PREDICT_MAX_WORKERS


def _predict_worker_init() -> None:
    '''Initializer for predict worker processes.

    Worker-side caches (`_WORKER_MANIFESTS`, `_WORKER_MARKET_DATA`) are
    populated lazily on first use, so no eager work is needed here. The
    function exists so the pool has a stable initializer hook and so
    `POLARS_MAX_THREADS` (set in the parent before pool creation) is
    inherited by the spawned interpreter.
    '''


def _predict_in_process(task: PredictTask, market_data_path: str) -> Signal:
    '''Run one sensor's `produce_signal` inside a worker process.

    Reads the shared market-data Arrow IPC file (cached per path, bounded
    to `_WORKER_MARKET_DATA_CACHE_MAX` frames) and rebuilds the Limen
    manifest from `task.experiment_dir` (cached per worker). The fitted
    `sensor` and `round_params` arrive on the task itself.

    Args:
        task: The picklable predict description for one sensor.
        market_data_path: Filesystem path to the Arrow IPC file holding
            the aggregated market-data frame for this sensor's kline size.

    Returns:
        The produced Signal, returned to the parent for dispatch.
    '''

    market_data = _WORKER_MARKET_DATA.get(market_data_path)

    if market_data is None:
        market_data = pl.read_ipc(market_data_path)

        if len(_WORKER_MARKET_DATA) >= _WORKER_MARKET_DATA_CACHE_MAX:
            oldest = next(iter(_WORKER_MARKET_DATA))
            del _WORKER_MARKET_DATA[oldest]

        _WORKER_MARKET_DATA[market_data_path] = market_data

    manifest = _WORKER_MANIFESTS.get(task.experiment_dir)

    if manifest is None:
        manifest = Trainer(Path(task.experiment_dir))._manifest
        _WORKER_MANIFESTS[task.experiment_dir] = manifest

    wired = WiredSensor(
        sensor_id=task.sensor_id,
        sensor=task.sensor,
        limen_manifest=manifest,
        round_params=task.round_params,
        strategy_id=task.strategy_id,
        interval_seconds=task.interval_seconds,
        experiment_dir=Path(task.experiment_dir),
    )

    return produce_signal(wired, market_data)


def _market_data_signature(market_data: pl.DataFrame) -> tuple[int, Any]:
    '''Return a cheap equality key for an aggregated market-data frame.

    Pairs row count with the latest `datetime` so the key changes when a
    new bar arrives but stays stable as the rolling window slides without
    new data. Falls back to row count alone when no `datetime` column is
    present (synthetic test frames), which is sufficient for static data.
    '''

    height = market_data.height

    if height and 'datetime' in market_data.columns:
        return (height, market_data['datetime'].max())

    return (height, None)


def _values_for_log(values: Mapping[str, Any]) -> dict[str, Any]:
    '''Render a Signal.values mapping safe for structured logging.

    Coerces every entry to a JSON-serializable primitive (or short
    summary string) so the configured orjson renderer
    (`observability.configure_logging` → `JSONRenderer(serializer=orjson.dumps)`
    with no `default=` callback and no `OPT_SERIALIZE_NUMPY` flag)
    cannot crash on a non-native type and drop the log line.

    Type-based normalization (the LENGTH check is a secondary guard
    after the type coercion — short sequences still get coerced to
    primitives, not passed through as ndarrays/Series):

    * `str` → unchanged
    * `Decimal` → `str` (preserves precision; orjson rejects Decimal
      by default and `Signal.values` post-init explicitly allows it)
    * `numpy.generic` (np.float64 / np.int64 / etc.) → `.item()` to
      native Python type, then recursively coerced. The recursion
      matters because `.item()` of `np.complex128` returns a Python
      `complex` and `.item()` of `np.bytes_` returns `bytes`; both
      are JSON-unsafe and would otherwise reach the renderer and
      drop the log line. The recursion sends them through the final
      JSON-native gate and onto `repr(val)`.
    * `numpy.ndarray` / `polars.Series` → recursively coerce each
      element if size ≤ `_MAX_LOGGED_SEQUENCE_LEN`, else a summary
      string `<sequence type=X size=N>`
    * `list` / `tuple` → recursively coerce each element if length
      ≤ threshold (so a nested `np.float64` / `Decimal` / `pl.Series`
      inside the container is still made safe), else summary
    * `dict` → recursively coerce each value AND stringify non-string
      keys (orjson rejects dicts with non-string keys) if length ≤
      threshold, else summary. If stringification produces a key
      collision (e.g. `{1: 'a', '1': 'b'}` both become `'1'`), the
      whole dict is replaced with a `dict-key-collision` summary
      rather than silently dropping one entry
    * Any other object with `__len__` longer than the threshold →
      summary string
    * Any remaining value that is NOT a JSON-native scalar
      (`int` / `float` / `bool` / `None` / `str`) → `repr(val)`.
      This makes the orjson safety contract true: the helper
      guarantees the renderer cannot crash on a returned value,
      because every leaf is one of the types orjson accepts AND
      every container has been recursively coerced.

    Args:
        values: A `Signal.values` mapping (typically `{key: scalar}`
            for binary classifiers, but tolerant of any predictor
            output shape).

    Returns:
        A plain dict suitable for `extra=` on a `logging` call.
    '''

    out: dict[str, Any] = {}
    for key, val in values.items():
        out[key] = _coerce_value(val)
    return out


def _safe_len(val: Any) -> int | None:
    '''Return `len(val)` if it succeeds, else `None`.

    Catches `Exception` rather than just `TypeError`: a custom
    `__len__` is free to raise `ValueError` or anything else, and
    the "logging never raises" contract requires every code path
    in `_coerce_value` to absorb those failures rather than let
    them bubble out and break the calling sensor tick.
    '''

    try:
        return len(val)
    except Exception:  # noqa: BLE001 - logging must not raise from a misbehaving __len__
        return None


def _coerce_value(val: Any) -> Any:  # noqa: PLR0911 - one branch per coerced type, keeping flat
    '''Coerce a single value to a JSON-serializable form for logging.

    See `_values_for_log` for the full normalization table.
    '''

    if isinstance(val, str):
        return val
    if isinstance(val, Decimal):
        return str(val)
    if isinstance(val, np.generic):
        return _coerce_value(val.item())
    if isinstance(val, np.ndarray):
        size = int(val.size)
        if size > _MAX_LOGGED_SEQUENCE_LEN:
            return f'<sequence type=ndarray size={size}>'
        return [_coerce_value(x) for x in val.tolist()]
    if isinstance(val, pl.Series):
        size = val.len()
        if size > _MAX_LOGGED_SEQUENCE_LEN:
            return f'<sequence type=Series size={size}>'
        return [_coerce_value(x) for x in val.to_list()]
    if isinstance(val, dict):
        dict_len = _safe_len(val)
        if dict_len is None:
            return repr(val)
        if dict_len > _MAX_LOGGED_SEQUENCE_LEN:
            return f'<sequence type=dict len={dict_len}>'
        coerced = {str(k): _coerce_value(v) for k, v in val.items()}
        if len(coerced) != dict_len:
            return f'<sequence type=dict-key-collision len={dict_len}>'
        return coerced
    if isinstance(val, (list, tuple)):
        seq_len = _safe_len(val)
        if seq_len is None:
            return repr(val)
        if seq_len > _MAX_LOGGED_SEQUENCE_LEN:
            return f'<sequence type={type(val).__name__} len={seq_len}>'
        return [_coerce_value(x) for x in val]
    length = _safe_len(val)
    if length is not None and length > _MAX_LOGGED_SEQUENCE_LEN:
        return f'<sequence type={type(val).__name__} len={length}>'
    if isinstance(val, (int, float, bool, type(None))):
        return val
    return repr(val)


def _log_signal(wired: WiredSensor, signal: Signal) -> None:
    '''Log a produced Signal at INFO before strategy dispatch.

    Captures the predictor's actual output on every tick so a
    silent HOLD path is distinguishable from a broken predict
    path (see module docstring).
    '''

    _log.info(
        'signal produced',
        extra={
            'strategy_id': wired.strategy_id,
            'sensor_id': wired.sensor_id,
            'predictor_fn_id': signal.predictor_fn_id,
            'values': _values_for_log(signal.values),
        },
    )


class PredictLoop:
    '''Process-backed predict loop for wired Sensors.

    Args:
        runner: StrategyRunner for signal dispatch.
        wired_sensors: Sensors to run predict on.
        market_data_provider: Callable that returns rolling DataFrame
            for a given kline_size. Signature: (kline_size: int) -> pl.DataFrame.
        context_provider: Callable that returns current StrategyContext
            for a given strategy_id.
        action_submit: Optional callback invoked with `(actions, strategy_id)`
            after each dispatch_signal. When None, returned actions are
            discarded (back-compat for tests that do not exercise the
            submission path).
    '''

    def __init__(
        self,
        runner: StrategyRunner,
        wired_sensors: list[WiredSensor],
        market_data_provider: Callable[[int], pl.DataFrame],
        context_provider: Callable[[str], StrategyContext],
        action_submit: ActionSubmitter | None = None,
    ) -> None:
        self._runner = runner
        self._wired_sensors = list(wired_sensors)
        self._market_data_provider = market_data_provider
        self._context_provider = context_provider
        self._action_submit = action_submit
        self._running = False
        self._lock = threading.Lock()
        self._max_workers = _resolve_predict_max_workers()
        self._executor: ProcessPoolExecutor | None = None
        self._scheduler_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._by_key: dict[str, WiredSensor] = {}
        self._tasks: dict[str, PredictTask] = {}
        self._next_due: dict[str, float] = {}
        self._in_flight: set[str] = set()
        self._ipc_dir: Path | None = None
        self._ipc_seq = 0
        self._ipc_current: dict[int, tuple[tuple[int, Any], Path]] = {}
        self._ipc_refs: dict[Path, int] = {}

    @property
    def running(self) -> bool:
        '''Whether the predict loop is currently running.'''

        return self._running

    def start(self) -> None:
        '''Start the process-backed predict scheduler for all wired Sensors.

        One scheduler thread polls per-sensor due-times and submits ready
        ticks to a shared spawn `ProcessPoolExecutor` capped at
        `NEXUS_PREDICT_MAX_WORKERS` (default 16). Each tick's heavy
        `prepare_data` runs in a worker process so the GIL-bound Limen
        feature pipeline parallelizes across cores; the parent aggregates
        the market-data frame once per cycle and shares it with workers
        through an Arrow IPC file. Strategy dispatch stays in the parent.
        '''

        with self._lock:
            if self._running:
                return

            self._running = True
            self._stop_event.clear()

            now = time.monotonic()
            self._by_key = {
                f'{wired.strategy_id}:{wired.sensor_id}': wired
                for wired in self._wired_sensors
            }
            self._tasks = {
                key: PredictTask(
                    sensor_id=wired.sensor_id,
                    sensor=wired.sensor,
                    round_params=wired.round_params,
                    strategy_id=wired.strategy_id,
                    interval_seconds=wired.interval_seconds,
                    experiment_dir=str(wired.experiment_dir),
                )
                for key, wired in self._by_key.items()
            }
            self._next_due = {
                key: now + wired.interval_seconds
                for key, wired in self._by_key.items()
            }
            self._in_flight = set()
            self._ipc_dir = Path(tempfile.mkdtemp(prefix='nexus-predict-md-'))
            self._ipc_seq = 0
            self._ipc_current = {}
            self._ipc_refs = {}

            os.environ.setdefault(
                'POLARS_MAX_THREADS',
                os.environ.get(_POLARS_MAX_THREADS_ENV, _DEFAULT_POLARS_MAX_THREADS),
            )

            self._executor = ProcessPoolExecutor(
                max_workers=self._max_workers,
                mp_context=multiprocessing.get_context('spawn'),
                initializer=_predict_worker_init,
            )
            self._scheduler_thread = threading.Thread(
                target=self._scheduler_loop,
                name='nexus-predict-scheduler',
                daemon=True,
            )
            self._scheduler_thread.start()

    def stop(self) -> None:
        '''Stop the scheduler thread, drain the pool, and clear IPC files.'''

        with self._lock:
            self._running = False
            self._stop_event.set()
            executor = self._executor
            self._executor = None
            ipc_dir = self._ipc_dir
            self._ipc_dir = None
            self._next_due.clear()
            self._in_flight.clear()
            self._ipc_current.clear()
            self._ipc_refs.clear()

        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

        if ipc_dir is not None:
            rmtree(ipc_dir, ignore_errors=True)

    def tick_once(self, wired: WiredSensor) -> None:
        '''Run one synchronous predict cycle for a wired sensor.

        Single-shot entry point for schedule-driven callers (e.g. a
        deterministic backtest replay). Does not require `start()`,
        does not schedule a follow-up `threading.Timer`, and propagates
        exceptions instead of swallowing them as `_tick` does.

        Args:
            wired: The wired sensor to fire one predict tick on.

        Raises:
            RuntimeError: When `_running` is `True` at the moment of
                entry. The check takes `_lock` so it is atomic with
                `start()`/`stop()`, but it is a fail-fast guard against
                deliberate misuse, not a hard interleave barrier:

                - The chain body runs without the lock, so callers
                  must not invoke `start()` while a `tick_once` chain
                  is in flight.
                - `stop()` flips `_running` to `False` under the lock
                  but does not wait for an in-flight `_tick` to finish
                  (`threading.Timer.cancel` only prevents future
                  fires), so callers must ensure any in-flight `_tick`
                  has returned before invoking `tick_once`.

                Schedule-driven callers (the intended use case) call
                `tick_once` from a single thread that never touches the
                Timer-driven loop, so neither concern applies in
                practice.
        '''

        with self._lock:
            if self._running:
                msg = 'tick_once must not be called while the Timer-driven loop is running'
                raise RuntimeError(msg)

        kline_size = _extract_kline_size(wired)
        market_data = self._market_data_provider(kline_size)

        if market_data.is_empty():
            _log.warning(
                'no market data for sensor %s, skipping',
                wired.sensor_id,
            )
            return

        signal = produce_signal(wired, market_data)
        _log_signal(wired, signal)
        context = self._context_provider(wired.strategy_id)

        actions = self._runner.dispatch_signal(
            wired.strategy_id,
            signal,
            StrategyParams(raw={}),
            context,
        )

        if self._action_submit is not None and actions:
            self._action_submit(actions, wired.strategy_id)

    def _scheduler_loop(self) -> None:
        '''Submit due sensor ticks to the worker pool until stopped.

        Wakes every `_SCHEDULER_POLL_SECONDS`, collects the sensors whose
        next-due time has passed and are not already in flight, marks them
        in flight, and hands them to `_submit_due`. Concurrency is capped
        at `max_workers` by the pool regardless of how many sensors come
        due at once.
        '''

        while not self._stop_event.wait(timeout=_SCHEDULER_POLL_SECONDS):
            now = time.monotonic()
            due: list[str] = []

            with self._lock:
                if not self._running or self._executor is None:
                    return

                for key, due_at in self._next_due.items():
                    if due_at <= now and key not in self._in_flight:
                        self._in_flight.add(key)
                        due.append(key)

            if due:
                self._submit_due(due)

    def _submit_due(self, keys: list[str]) -> None:
        '''Aggregate market data per kline_size and submit each due sensor.

        Sensors sharing a kline_size pay a single parent aggregation and
        share one Arrow IPC file. A kline_size whose market data is empty
        or whose aggregation raises reschedules its sensors without
        submitting, so a transient provider failure cannot kill the
        scheduler thread.
        '''

        by_kline: dict[int, list[str]] = {}

        for key in keys:
            try:
                kline_size = _extract_kline_size(self._by_key[key])
            except Exception:  # noqa: BLE001 - a bad sensor must not kill the scheduler
                _log.exception('kline_size extraction failed for %s', key)
                self._reschedule(key)
                continue

            by_kline.setdefault(kline_size, []).append(key)

        for kline_size, group in by_kline.items():
            try:
                market_data = self._market_data_provider(kline_size)
                empty = market_data.is_empty()

                if not empty:
                    path = self._write_market_data_ipc(kline_size, market_data)
            except Exception:  # noqa: BLE001 - provider failure must not kill the scheduler
                _log.exception(
                    'market data preparation failed for kline_size %s',
                    kline_size,
                )

                for key in group:
                    self._reschedule(key)

                continue

            if empty:
                _log.warning(
                    'no market data for kline_size %s, skipping %d sensors',
                    kline_size,
                    len(group),
                )

                for key in group:
                    self._reschedule(key)

                continue

            for key in group:
                self._submit_one(key, path)

    def _submit_one(self, key: str, path: Path) -> None:
        '''Submit one sensor's predict task and track its IPC reference.'''

        with self._lock:
            executor = self._executor

            if not self._running or executor is None:
                self._in_flight.discard(key)
                return

            task = self._tasks[key]
            self._ipc_refs[path] = self._ipc_refs.get(path, 0) + 1

        wired = self._by_key[key]

        def _on_done(future: Future[Signal]) -> None:
            self._handle_predict_result(wired, path, future)

        executor.submit(_predict_in_process, task, str(path)).add_done_callback(_on_done)

    def _write_market_data_ipc(
        self,
        kline_size: int,
        market_data: pl.DataFrame,
    ) -> Path:
        '''Return a shared Arrow IPC path for a kline_size's market data.

        Reuses the existing file while the frame signature is unchanged so
        every sensor on a kline_size shares one parent aggregation per
        cycle. On a new signature, writes a fresh file and drops the prior
        one once no in-flight task still references it.
        '''

        signature = _market_data_signature(market_data)

        with self._lock:
            current = self._ipc_current.get(kline_size)

            if current is not None and current[0] == signature:
                return current[1]

            ipc_dir = self._ipc_dir

            if ipc_dir is None:
                msg = 'predict loop IPC dir is not initialised'
                raise RuntimeError(msg)

            self._ipc_seq += 1
            path = ipc_dir / f'md_{kline_size}_{self._ipc_seq}.arrow'
            previous = current[1] if current is not None else None
            self._ipc_current[kline_size] = (signature, path)
            self._ipc_refs.setdefault(path, 0)

        market_data.write_ipc(path)

        if previous is not None:
            self._maybe_unlink_ipc(previous)

        return path

    def _maybe_unlink_ipc(self, path: Path) -> None:
        '''Delete an IPC file once it is non-current and unreferenced.'''

        with self._lock:
            if self._ipc_refs.get(path, 0) > 0:
                return

            if any(current == path for _sig, current in self._ipc_current.values()):
                return

            self._ipc_refs.pop(path, None)

        path.unlink(missing_ok=True)

    def _reschedule(self, key: str) -> None:
        '''Release the in-flight mark and arm the next due-time.'''

        with self._lock:
            self._in_flight.discard(key)

            if self._running:
                wired = self._by_key.get(key)

                if wired is not None:
                    self._next_due[key] = time.monotonic() + wired.interval_seconds

    def _handle_predict_result(
        self,
        wired: WiredSensor,
        path: Path,
        future: Future[Signal],
    ) -> None:
        '''Dispatch a worker's Signal in the parent, then reschedule.

        Runs in the pool's result-handling thread, which invokes done
        callbacks serially, so strategy dispatch, context lookup, and
        action submission never run concurrently and never touch worker
        processes. Always releases the in-flight mark, drops the IPC
        reference, and arms the next due-time in a `finally` block, so a
        predict failure and a clean tick reschedule identically.
        '''

        key = f'{wired.strategy_id}:{wired.sensor_id}'

        try:
            signal = future.result()
            _log_signal(wired, signal)
            context = self._context_provider(wired.strategy_id)

            actions = self._runner.dispatch_signal(
                wired.strategy_id,
                signal,
                StrategyParams(raw={}),
                context,
            )

            if self._action_submit is not None and actions:
                try:
                    self._action_submit(actions, wired.strategy_id)
                except Exception:  # noqa: BLE001 - submitter failure must not kill the loop
                    _log.exception(
                        'action_submit raised for sensor %s',
                        wired.sensor_id,
                    )
        except Exception:  # noqa: BLE001 - intentional catch-all for predict cycle
            _log.exception(
                'predict failed for sensor %s',
                wired.sensor_id,
            )
        finally:
            with self._lock:
                self._in_flight.discard(key)
                self._ipc_refs[path] = max(0, self._ipc_refs.get(path, 0) - 1)

                if self._running:
                    self._next_due[key] = time.monotonic() + wired.interval_seconds

            self._maybe_unlink_ipc(path)


def _extract_kline_size(wired: WiredSensor) -> int:
    '''Extract kline_size from Limen manifest data source config.'''

    config = getattr(wired.limen_manifest, 'data_source_config', None)

    if config is None:
        msg = f'sensor {wired.sensor_id} has no data_source_config'
        raise ValueError(msg)

    kline_size = config.params.get('kline_size')

    if kline_size is None:
        msg = f'sensor {wired.sensor_id} data_source_config missing kline_size'
        raise ValueError(msg)

    return int(kline_size)
