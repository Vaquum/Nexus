'''Timer loop for strategy on_timer callbacks.

Runs per-strategy timers that dispatch on_timer events
to the bound strategy via StrategyRunner.
'''

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from nexus.infrastructure.manifest import TimerSpec
from nexus.strategy.context import StrategyContext
from nexus.strategy.params import StrategyParams
from nexus.strategy.runner import StrategyRunner

__all__ = ['TimerLoop']

_log = logging.getLogger(__name__)


class TimerLoop:
    '''Timer loop for strategy-authored timers.

    Args:
        runner: StrategyRunner for timer dispatch.
        strategy_timers: Mapping of strategy_id to its TimerSpecs.
        context_provider: Callable that returns current StrategyContext
            for a given strategy_id.
    '''

    def __init__(
        self,
        runner: StrategyRunner,
        strategy_timers: dict[str, tuple[TimerSpec, ...]],
        context_provider: Callable[[str], StrategyContext],
    ) -> None:
        self._runner = runner
        self._strategy_timers = dict(strategy_timers)
        self._context_provider = context_provider
        self._active_timers: dict[str, threading.Timer] = {}
        self._running = False
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        '''Whether the timer loop is currently running.'''

        return self._running

    def start(self) -> None:
        '''Start all strategy timers.'''

        with self._lock:
            if self._running:
                return

            self._running = True

            for strategy_id, timer_specs in self._strategy_timers.items():
                for spec in timer_specs:
                    self._schedule_locked(strategy_id, spec)

    def stop(self) -> None:
        '''Stop all strategy timers.'''

        with self._lock:
            self._running = False

            for timer in self._active_timers.values():
                timer.cancel()

            self._active_timers.clear()

    def _schedule_locked(self, strategy_id: str, spec: TimerSpec) -> None:
        '''Schedule next timer fire. Must be called with lock held.'''

        if not self._running:
            return

        timer = threading.Timer(
            spec.interval_seconds,
            self._tick,
            args=(strategy_id, spec),
        )
        timer.daemon = True
        timer_key = f'{strategy_id}:{spec.timer_id}'
        self._active_timers[timer_key] = timer
        timer.start()

    def _tick(self, strategy_id: str, spec: TimerSpec) -> None:
        with self._lock:
            if not self._running:
                return

        try:
            context = self._context_provider(strategy_id)

            self._runner.dispatch_timer(
                strategy_id,
                spec.timer_id,
                StrategyParams(raw={}),
                context,
            )
        except Exception:  # noqa: BLE001 - intentional catch-all for timer callback
            _log.exception(
                'timer callback failed',
                extra={
                    'strategy_id': strategy_id,
                    'timer_id': spec.timer_id,
                },
            )

        with self._lock:
            self._schedule_locked(strategy_id, spec)
