'''Timer-based predict loop for signal generation.

Runs per-sensor timers that call produce_signal and dispatch
the resulting Signal to the bound strategy.
'''

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

import polars as pl

from nexus.startup.sequencer import WiredSensor
from nexus.strategy.context import StrategyContext
from nexus.strategy.params import StrategyParams
from nexus.strategy.runner import StrategyRunner
from nexus.strategy.signal_producer import produce_signal

__all__ = ['PredictLoop']

_log = logging.getLogger(__name__)


class PredictLoop:
    '''Timer-based predict loop for wired Sensors.

    Args:
        runner: StrategyRunner for signal dispatch.
        wired_sensors: Sensors to run predict on.
        market_data_provider: Callable that returns rolling DataFrame
            for a given kline_size. Signature: (kline_size: int) -> pl.DataFrame.
        context_provider: Callable that returns current StrategyContext
            for a given strategy_id.
    '''

    def __init__(
        self,
        runner: StrategyRunner,
        wired_sensors: list[WiredSensor],
        market_data_provider: Callable[[int], pl.DataFrame],
        context_provider: Callable[[str], StrategyContext],
    ) -> None:
        self._runner = runner
        self._wired_sensors = list(wired_sensors)
        self._market_data_provider = market_data_provider
        self._context_provider = context_provider
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
        self._active_timers[wired.sensor_id] = timer
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
            context = self._context_provider(wired.strategy_id)

            self._runner.dispatch_signal(
                wired.strategy_id,
                signal,
                StrategyParams(raw={}),
                context,
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
