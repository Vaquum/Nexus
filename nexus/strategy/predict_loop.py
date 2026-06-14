'''Single-process Conduit poller for signal generation.

One daemon scheduler thread polls each `SignalBinding` on its
`interval_seconds`. Per tick the loop re-reads the Conduit serving
manifest (the liveness authority) and the series' prediction Arrow
frame, joins the matching raw `close` from the OHLCV Arrow frame,
builds a `Signal`, and dispatches it to the strategy in this same
thread. No process pool, no Limen, no shared IPC.

Shared `ts` contract: the prediction frame (`/opt/conduit/<series>/
latest.arrow`) and the OHLCV frame (`/opt/arrow/<series>/latest.arrow`)
are produced independently upstream (Furnace and the control plane) but
share the `ts` column as UTC epoch nanoseconds anchored on the closed
bar. The price join is exact equality on `ts`, so a drift in unit
(ns/ms/s) or anchor (bar-open vs bar-close) on either producer makes the
join match nothing and the strategy never trades — surfaced per tick by
the diagnostic `no OHLCV close for prediction ts` warning, which carries
the prediction `ts` and the OHLCV `ts` range.

Every produced Signal is logged at INFO immediately before strategy
dispatch so a silent HOLD path stays distinguishable from a broken
predict path: a strategy that maps every prediction to no-action
logs nothing on its own, so without this line a HOLD-only strategy
looks identical to a dead signal source.

`tick_once` runs the same per-binding tick synchronously and
propagates exceptions, for deterministic replay.
'''

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nexus.startup.sequencer import SignalBinding
from nexus.strategy.action import Action
from nexus.strategy.context import StrategyContext
from nexus.strategy.params import StrategyParams
from nexus.strategy.runner import StrategyRunner
from nexus.strategy.signal import Signal

__all__ = ['ActionSubmitter', 'PredictLoop']

_log = logging.getLogger(__name__)

ActionSubmitter = Callable[[list[Action], str], None]

_MAX_LOGGED_SEQUENCE_LEN = 16

_SCHEDULER_POLL_SECONDS = 0.25
_MANIFEST_NAME = 'serving_manifest.json'
_LATEST_ARROW = 'latest.arrow'
_USABLE_REASON_CODE = 0
_MAX_MANIFEST_AGE_SECONDS = 120.0

_DEFAULT_CONDUIT_DIR = Path('/opt/conduit')
_DEFAULT_ARROW_DIR = Path('/opt/arrow')


def _utc_now() -> datetime:
    '''Return the current UTC time.'''

    return datetime.now(tz=timezone.utc)


def _values_for_log(values: Mapping[str, Any]) -> dict[str, Any]:
    '''Render a Signal.values mapping safe for structured logging.

    Coerces every entry to a JSON-serializable primitive (or short
    summary string) so the configured orjson renderer cannot crash on
    a non-native type and drop the log line.

    Args:
        values: A `Signal.values` mapping.

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
    `__len__` is free to raise anything, and the "logging never
    raises" contract requires every path in `_coerce_value` to absorb
    those failures rather than let them break the tick.
    '''

    try:
        return len(val)
    except Exception:  # noqa: BLE001 - logging must not raise from a misbehaving __len__
        return None


def _coerce_value(val: Any) -> Any:  # noqa: PLR0911 - one branch per coerced type, keeping flat
    '''Coerce a single value to a JSON-serializable form for logging.

    See `_values_for_log` for the normalization contract.
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


def _log_signal(strategy_id: str, series: str, signal: Signal) -> None:
    '''Log a produced Signal at INFO before strategy dispatch.

    Captures the predictor's actual output on every tick so a silent
    HOLD path is distinguishable from a broken predict path (see
    module docstring).
    '''

    _log.info(
        'signal produced',
        extra={
            'strategy_id': strategy_id,
            'series': series,
            'predictor_fn_id': signal.predictor_fn_id,
            'values': _values_for_log(signal.values),
        },
    )


class PredictLoop:
    '''Single-process Conduit poller for strategy signal bindings.

    Args:
        runner: StrategyRunner for signal dispatch.
        signal_bindings: Bindings naming the Conduit series to poll.
        context_provider: Callable returning the current
            StrategyContext for a given strategy_id.
        action_submit: Optional callback invoked with `(actions,
            strategy_id)` after each dispatch_signal. When None,
            returned actions are discarded.
        conduit_dir: Read-only mount holding the serving manifest and
            per-series prediction Arrow frames.
        arrow_dir: Read-only mount holding per-series OHLCV Arrow frames.
        clock: Callable returning the current UTC time, for tests.
    '''

    def __init__(
        self,
        runner: StrategyRunner,
        signal_bindings: list[SignalBinding],
        context_provider: Callable[[str], StrategyContext],
        action_submit: ActionSubmitter | None = None,
        conduit_dir: Path = _DEFAULT_CONDUIT_DIR,
        arrow_dir: Path = _DEFAULT_ARROW_DIR,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._runner = runner
        self._signal_bindings = list(signal_bindings)
        self._context_provider = context_provider
        self._action_submit = action_submit
        self._conduit_dir = conduit_dir
        self._arrow_dir = arrow_dir
        self._clock = clock
        self._running = False
        self._lock = threading.Lock()
        self._scheduler_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._next_due: dict[str, float] = {}
        self._last_ts: dict[tuple[str, str], int] = {}
        self._last_cohort: dict[tuple[str, str], str] = {}

    @property
    def running(self) -> bool:
        '''Whether the predict loop is currently running.'''

        return self._running

    def start(self) -> None:
        '''Start the single-thread Conduit scheduler for all bindings.

        One daemon thread polls per-binding due-times and executes due
        ticks inline. Every binding is scheduled `interval_seconds`
        after start.
        '''

        with self._lock:
            if self._running:
                return

            self._running = True
            self._stop_event.clear()

            now = time.monotonic()
            self._next_due = {
                self._key(binding): now + binding.interval_seconds
                for binding in self._signal_bindings
            }

            self._scheduler_thread = threading.Thread(
                target=self._scheduler_loop,
                name='nexus-predict-scheduler',
                daemon=True,
            )
            self._scheduler_thread.start()

    def stop(self) -> None:
        '''Stop the scheduler thread.'''

        with self._lock:
            self._running = False
            self._stop_event.set()
            self._next_due.clear()

    def tick_once(self, binding: SignalBinding) -> None:
        '''Run one synchronous Conduit tick for a binding.

        Single-shot entry point for deterministic replay. Does not
        require `start()`, does not schedule a follow-up, and
        PROPAGATES exceptions instead of swallowing them as the
        scheduled tick does.

        Raises:
            RuntimeError: When the scheduler loop is running. The check
                takes `_lock` so it is atomic with `start()`/`stop()`,
                but it is a fail-fast guard against deliberate misuse,
                not a hard interleave barrier: callers must not invoke
                `start()` while a `tick_once` is in flight.
        '''

        with self._lock:
            if self._running:
                msg = 'tick_once must not be called while the scheduler loop is running'
                raise RuntimeError(msg)

        self._tick(binding)

    def _scheduler_loop(self) -> None:
        '''Execute due binding ticks inline until stopped.

        Wakes every `_SCHEDULER_POLL_SECONDS`, collects the bindings
        whose next-due time has passed, runs each tick inline, and arms
        the next due-time. Each tick is wrapped so a single bad read
        never kills the thread.
        '''

        while not self._stop_event.wait(timeout=_SCHEDULER_POLL_SECONDS):
            now = time.monotonic()
            due: list[SignalBinding] = []

            with self._lock:
                if not self._running:
                    return

                for binding in self._signal_bindings:
                    key = self._key(binding)
                    if self._next_due.get(key, 0.0) <= now:
                        due.append(binding)

            for binding in due:
                try:
                    self._tick(binding)
                except Exception:  # noqa: BLE001 - one bad read must not kill the scheduler
                    _log.exception(
                        'predict tick failed',
                        extra={
                            'strategy_id': binding.strategy_id,
                            'series': binding.series,
                        },
                    )
                finally:
                    with self._lock:
                        if self._running:
                            self._next_due[self._key(binding)] = (
                                time.monotonic() + binding.interval_seconds
                            )

    def _tick(self, binding: SignalBinding) -> None:
        '''Poll Conduit once for a binding and dispatch any new Signal.'''

        resolved = self._resolve_signal(binding)
        if resolved is None:
            return

        signal, ts = resolved
        key = (binding.strategy_id, binding.series)

        _log_signal(binding.strategy_id, binding.series, signal)

        context = self._context_provider(binding.strategy_id)
        actions = self._runner.dispatch_signal(
            binding.strategy_id,
            signal,
            StrategyParams(raw={}),
            context,
        )

        self._last_ts[key] = ts

        if self._action_submit is not None and actions:
            self._action_submit(actions, binding.strategy_id)

    def _resolve_signal(  # noqa: PLR0911 - one return per Conduit skip condition
        self,
        binding: SignalBinding,
    ) -> tuple[Signal, int] | None:
        '''Build the Signal for this tick, or None when no fresh data.'''

        manifest = self._read_manifest()
        if manifest is None:
            return None

        generated_at = self._parse_generated_at(manifest)
        if generated_at is None:
            return None

        if (self._clock() - generated_at).total_seconds() > _MAX_MANIFEST_AGE_SECONDS:
            _log.warning(
                'stale serving manifest, skipping tick',
                extra={
                    'series': binding.series,
                    'generated_at': generated_at.isoformat(),
                },
            )
            return None

        series = binding.series
        entry = manifest.get('series', {}).get(series)
        if entry is None:
            _log.warning(
                'series missing from serving manifest, skipping tick',
                extra={'series': series},
            )
            return None

        rel_path = entry.get('path')
        if not isinstance(rel_path, str) or not rel_path:
            _log.warning(
                'serving manifest entry missing path, skipping tick',
                extra={'series': series},
            )
            return None

        row = self._latest_usable_row(rel_path)
        if row is None:
            return None

        ts = int(row['ts'])
        key = (binding.strategy_id, series)
        if ts <= self._last_ts.get(key, -1):
            return None

        prediction = int(row['prediction'])
        if prediction not in (0, 1):
            _log.warning(
                'non-binary prediction, skipping tick',
                extra={'series': series, 'ts': ts, 'prediction': prediction},
            )
            return None

        self._log_cohort(key, series, entry)

        close = self._close_for_ts(series, ts)
        if close is None:
            return None

        signal = Signal(
            predictor_fn_id=f'{binding.strategy_id}:{series}',
            values={
                '_preds': prediction,
                '_probs': float(row['probability']),
                'close': float(close),
            },
            timestamp=self._clock(),
        )

        return signal, ts

    def _read_manifest(self) -> dict[str, Any] | None:
        '''Read and parse the Conduit serving manifest, or None on miss.'''

        path = self._conduit_dir / _MANIFEST_NAME
        if not path.is_file():
            _log.warning('serving manifest not found', extra={'path': str(path)})
            return None

        with path.open(encoding='utf-8') as handle:
            data = json.load(handle)

        if not isinstance(data, dict):
            _log.warning('serving manifest is not a mapping', extra={'path': str(path)})
            return None

        return data

    def _parse_generated_at(self, manifest: dict[str, Any]) -> datetime | None:
        '''Parse the manifest `generated_at` ISO8601 timestamp as UTC.'''

        raw = manifest.get('generated_at')
        if not isinstance(raw, str):
            _log.warning('serving manifest missing generated_at')
            return None

        normalized = f'{raw[:-1]}+00:00' if raw.endswith('Z') else raw
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return parsed.astimezone(timezone.utc)

    def _latest_usable_row(self, rel_path: str) -> dict[str, Any] | None:
        '''Return the max-ts usable prediction row for a series, or None.

        Reads the manifest-declared `rel_path` under the conduit mount.
        Rows with a non-zero `reason_code` are dropped; the surviving
        row with the greatest `ts` is returned.
        '''

        path = self._conduit_dir / rel_path
        if not path.is_file():
            _log.warning(
                'conduit prediction frame not found, skipping tick',
                extra={'path': str(path)},
            )
            return None

        df = pl.read_ipc(path, memory_map=True)
        usable = df.filter(pl.col('reason_code') == _USABLE_REASON_CODE)

        if usable.is_empty():
            return None

        return usable.sort('ts').tail(1).to_dicts()[0]

    def _close_for_ts(self, series: str, ts: int) -> float | None:
        '''Return the OHLCV `close` matching `ts` for a series, or None.

        Joins the prediction `ts` against the OHLCV frame on exact
        equality (the shared `ts` contract — see the module docstring).
        A transiently-absent frame (mid atomic-swap) and an unmatched
        `ts` (a `ts`-convention drift on the producing side) both
        warn-skip; the unmatched-`ts` warning carries the prediction
        `ts` and the OHLCV `ts` range so a drift is diagnosable from the
        log rather than a silent never-trade.
        '''

        path = self._arrow_dir / series / _LATEST_ARROW
        if not path.is_file():
            _log.warning(
                'ohlcv frame not found, skipping tick',
                extra={'series': series, 'path': str(path)},
            )
            return None

        df = pl.read_ipc(path, memory_map=True)
        matched = df.filter(pl.col('ts') == ts).select('close')

        if matched.is_empty():
            ts_col = df['ts'] if 'ts' in df.columns and not df.is_empty() else None
            _log.warning(
                'no OHLCV close for prediction ts, skipping tick',
                extra={
                    'series': series,
                    'prediction_ts': ts,
                    'ohlcv_ts_min': ts_col.min() if ts_col is not None else None,
                    'ohlcv_ts_max': ts_col.max() if ts_col is not None else None,
                },
            )
            return None

        return float(matched.to_series()[0])

    def _log_cohort(
        self,
        key: tuple[str, str],
        series: str,
        entry: dict[str, Any],
    ) -> None:
        '''Log the active cohort and emit a distinct event on change.'''

        cohort_id = entry.get('cohort_id')
        cohort_name = entry.get('name')

        _log.info(
            'cohort active',
            extra={
                'series': series,
                'cohort_id': cohort_id,
                'cohort_name': cohort_name,
            },
        )

        if not isinstance(cohort_id, str):
            return

        previous = self._last_cohort.get(key)
        if previous is not None and previous != cohort_id:
            _log.info(
                'cohort changed',
                extra={
                    'series': series,
                    'previous_cohort_id': previous,
                    'cohort_id': cohort_id,
                    'cohort_name': cohort_name,
                },
            )

        self._last_cohort[key] = cohort_id

    @staticmethod
    def _key(binding: SignalBinding) -> str:
        '''Return the scheduler key for a binding.'''

        return f'{binding.strategy_id}:{binding.series}'
