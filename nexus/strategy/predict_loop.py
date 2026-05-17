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
import threading
from collections.abc import Callable, Mapping
from typing import Any

import polars as pl

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


def _values_for_log(values: Mapping[str, Any]) -> dict[str, Any]:
    '''Render a Signal.values mapping safe for structured logging.

    Scalars pass through unchanged. Sequences (lists, tuples, ndarrays)
    longer than `_MAX_LOGGED_SEQUENCE_LEN` are replaced with a length
    summary so a predictor that returns a 10k-element vector doesn't
    blow up the log line.

    Args:
        values: A `Signal.values` mapping (typically `{key: scalar}`
            for binary classifiers, but tolerant of any predictor
            output shape).

    Returns:
        A plain dict suitable for `extra=` on a `logging` call.
    '''

    out: dict[str, Any] = {}
    for key, val in values.items():
        if isinstance(val, str):
            out[key] = val
            continue
        try:
            length = len(val)
        except TypeError:
            out[key] = val
            continue
        if length > _MAX_LOGGED_SEQUENCE_LEN:
            out[key] = f'<sequence type={type(val).__name__} len={length}>'
        else:
            out[key] = val
    return out


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
    '''Timer-based predict loop for wired Sensors.

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
        self._active_timers: dict[str, threading.Timer] = {}
        self._running = False
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        '''Whether the predict loop is currently running.'''

        return self._running

    def start(self) -> None:
        '''Start predict timers for all wired Sensors.'''

        with self._lock:
            if self._running:
                return

            self._running = True

            for wired in self._wired_sensors:
                self._schedule_locked(wired)

    def stop(self) -> None:
        '''Stop all predict timers.'''

        with self._lock:
            self._running = False

            for timer in self._active_timers.values():
                timer.cancel()

            self._active_timers.clear()

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

    def _schedule_locked(self, wired: WiredSensor) -> None:
        '''Schedule next timer for a sensor. Must be called with lock held.'''

        if not self._running:
            return

        timer = threading.Timer(
            wired.interval_seconds,
            self._tick,
            args=(wired,),
        )
        timer.daemon = True
        timer_key = f'{wired.strategy_id}:{wired.sensor_id}'
        self._active_timers[timer_key] = timer
        timer.start()

    def _tick(self, wired: WiredSensor) -> None:
        with self._lock:
            if not self._running:
                return

        try:
            kline_size = _extract_kline_size(wired)
            market_data = self._market_data_provider(kline_size)

            if market_data.is_empty():
                _log.warning(
                    'no market data for sensor %s, skipping',
                    wired.sensor_id,
                )
                with self._lock:
                    self._schedule_locked(wired)
                return

            with self._lock:
                if not self._running:
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

        with self._lock:
            self._schedule_locked(wired)


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
