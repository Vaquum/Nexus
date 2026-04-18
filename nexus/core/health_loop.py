'''Periodic health re-evaluation loop.

Pulls a HealthSnapshot via the configured source on each tick, evaluates
it through HealthEvaluator, and updates instance_state.mode on transition.

Note on `rate_limit_headroom` semantics: the field carries utilisation
semantics (higher is worse) for parity with the Praxis-side HealthSnapshot.
The misnomer is intentional and predates this loop; HealthEvaluator
already evaluates it as higher-is-worse.
'''

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime, timezone

from nexus.core.domain.instance_state import InstanceState
from nexus.core.domain.operational_mode import ModeState
from nexus.core.health_evaluator import HealthEvaluator, HealthSnapshot

__all__ = ['HealthLoop']

_log = logging.getLogger(__name__)
_HEALTH_TRIGGER = 'health'


class HealthLoop:
    '''Periodically pull a HealthSnapshot and update operational mode.

    Args:
        snapshot_provider: Callable returning a HealthSnapshot. Invoked once
            per tick from a daemon timer thread.
        evaluator: HealthEvaluator that maps snapshot to OperationalMode.
        state: InstanceState whose mode is mutated on transition.
        interval_seconds: Seconds between ticks. Must be positive.
    '''

    def __init__(
        self,
        snapshot_provider: Callable[[], HealthSnapshot],
        evaluator: HealthEvaluator,
        state: InstanceState,
        interval_seconds: float = 5.0,
    ) -> None:
        if (
            isinstance(interval_seconds, bool)
            or not isinstance(interval_seconds, (int, float))
            or interval_seconds <= 0
        ):
            msg = 'HealthLoop.interval_seconds must be a positive number'
            raise ValueError(msg)

        self._snapshot_provider = snapshot_provider
        self._evaluator = evaluator
        self._state = state
        self._interval_seconds = float(interval_seconds)
        self._timer: threading.Timer | None = None
        self._running = False
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        '''Whether the loop is currently scheduling ticks.'''

        return self._running

    def start(self) -> None:
        '''Start the periodic re-evaluation loop.'''

        with self._lock:
            if self._running:
                return
            self._running = True
            self._schedule_locked()

    def stop(self) -> None:
        '''Stop the loop and cancel any pending tick.'''

        with self._lock:
            self._running = False
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

    def tick_once(self) -> None:
        '''Pull a snapshot and apply it without scheduling another tick.

        Useful for tests and for immediate-sync paths where the caller
        controls the cadence.
        '''

        self._apply_snapshot()

    def _schedule_locked(self) -> None:
        if not self._running:
            return
        self._timer = threading.Timer(self._interval_seconds, self._tick)
        self._timer.daemon = True
        self._timer.start()

    def _tick(self) -> None:
        with self._lock:
            if not self._running:
                return

        self._apply_snapshot()

        with self._lock:
            self._schedule_locked()

    def _apply_snapshot(self) -> None:
        try:
            snapshot = self._snapshot_provider()
        except Exception:  # noqa: BLE001 - intentional catch-all for provider
            _log.exception('health snapshot fetch failed')
            return

        try:
            new_mode = self._evaluator.evaluate(snapshot)
        except Exception:  # noqa: BLE001 - intentional catch-all for evaluator
            _log.exception('health evaluation failed')
            return

        current_mode = self._state.mode.mode
        if new_mode == current_mode:
            return

        self._state.mode = ModeState(
            mode=new_mode,
            trigger=_HEALTH_TRIGGER,
            transitioned_at=datetime.now(tz=timezone.utc),
        )
        _log.info(
            'operational mode transition (health)',
            extra={'from': current_mode.value, 'to': new_mode.value},
        )
