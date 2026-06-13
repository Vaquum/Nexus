'''Periodic state-store checkpoint scheduler.

Calls `state_store.checkpoint(state)` on a wall-clock interval so the
on-disk snapshot does not drift arbitrarily behind the live state.
Without this loop, `state_store.checkpoint()` runs only at boot-time
capital reconciliation (conditional) and at graceful shutdown, leaving
the WAL to grow unbounded between restarts and every read of
`snapshot.bin` between restarts stale.

The snapshot-serialization locks (`positions_lock` + `capital_lock`)
are owned by `StateStore` via its `StateSnapshotLocks` bundle and are
acquired inside `checkpoint()`, so this loop calls `checkpoint()`
directly — wrapping it would re-acquire the same non-reentrant locks
and self-deadlock.
'''

from __future__ import annotations

import logging
import threading

from nexus.core.domain.instance_state import InstanceState
from nexus.infrastructure.state_store import StateStore

__all__ = ['SnapshotScheduler']

_log = logging.getLogger(__name__)


class SnapshotScheduler:
    '''Periodically checkpoint InstanceState to the on-disk snapshot.

    Args:
        state_store: The StateStore that owns `snapshot.bin` and the WAL.
        state: The InstanceState whose snapshot is persisted on each tick.
        interval_seconds: Seconds between checkpoints. Must be positive.
            Recommended 60-600s; default 300 (5 min). Smaller intervals
            shrink the WAL replay-on-recovery window at the cost of more
            disk I/O per hour.
    '''

    def __init__(
        self,
        state_store: StateStore,
        state: InstanceState,
        interval_seconds: float = 300.0,
    ) -> None:
        if (
            isinstance(interval_seconds, bool)
            or not isinstance(interval_seconds, (int, float))
            or interval_seconds <= 0
        ):
            msg = 'SnapshotScheduler.interval_seconds must be a positive number'
            raise ValueError(msg)

        self._state_store = state_store
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
        '''Start the periodic checkpoint loop.'''

        with self._lock:
            if self._running:
                return

            self._running = True
            self._schedule_locked()

    def stop(self) -> None:
        '''Stop the loop and cancel any pending tick.

        Does NOT issue a final checkpoint — `ShutdownSequencer`
        already calls `state_store.checkpoint()` on its own path.
        Callers wiring this scheduler outside the standard launcher
        flow may want to call `tick_once()` after `stop()` to capture
        a final snapshot.
        '''

        with self._lock:
            self._running = False
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

    def tick_once(self) -> None:
        '''Run one checkpoint without scheduling another tick.

        Useful for tests and for immediate-sync paths where the
        caller controls cadence. Bypasses the `_running` guard so
        callers driving ticks manually (without `start()`) still
        write a snapshot — a duplicate checkpoint is harmless and
        the contract is "the caller asked for a checkpoint right now".
        '''

        self._checkpoint()

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

        self._checkpoint()

        with self._lock:
            self._schedule_locked()

    def _checkpoint(self) -> None:
        '''Call `state_store.checkpoint()`; the store owns the locks.

        A failure here must not abort the loop — disk-full, transient
        I/O error, or msgpack failure are all logged at ERROR and the
        next tick still fires. The WAL keeps growing (no truncation
        happens on a failed checkpoint) so no state is lost; the next
        successful checkpoint catches up.

        The snapshot-serialization locks are acquired inside
        `checkpoint()` (`StateStore`'s `StateSnapshotLocks` bundle), so
        this loop does not wrap the call — a racing `stop()` between the
        `_tick` entry check and the checkpoint completing simply writes
        one extra snapshot; duplicate checkpoints are harmless.
        '''

        try:
            self._state_store.checkpoint(self._state)
        except Exception:  # noqa: BLE001 - checkpoint failure must not abort the loop
            _log.exception('snapshot checkpoint failed; next tick will retry')
