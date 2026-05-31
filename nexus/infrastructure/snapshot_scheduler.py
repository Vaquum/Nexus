'''Periodic state-store checkpoint scheduler.

Calls `state_store.checkpoint(state)` on a wall-clock interval so the
on-disk snapshot does not drift arbitrarily behind the live state.
Without this loop, `state_store.checkpoint()` runs only at boot-time
capital reconciliation (conditional) and at graceful shutdown, leaving
the WAL to grow unbounded between restarts and every read of
`snapshot.bin` between restarts stale.

The checkpoint must observe the same lock chain as the rest of the
state-mutation path:

    command_registry_lock -> positions_lock -> CapitalController._lock
                                            -> wal_lock

`state_store.checkpoint()` acquires `wal_lock` internally. This loop
holds `positions_lock` and `CapitalController._lock` around the call
so the snapshot captures a consistent point-in-time view of
`state.positions`, `state.risk.per_strategy`, and
`state.capital.per_strategy_deployed` (same lock pattern as
`shutdown_sequencer._checkpoint_state` at `shutdown_sequencer.py:935`).
'''

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext

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
        positions_lock: Optional `threading.Lock` shared with PredictLoop /
            OutcomeProcessor / ShutdownSequencer (the same object stored
            at `state.risk.lock`). Held around `checkpoint()` so the
            snapshot does not capture a partially-mutated state. None
            falls back to `nullcontext()` for legacy single-threaded
            test paths.
        capital_lock_cm: Optional callable returning a context manager
            for `CapitalController._lock`. Held around `checkpoint()`
            for the same reason. None falls back to `nullcontext()`.
    '''

    def __init__(
        self,
        state_store: StateStore,
        state: InstanceState,
        interval_seconds: float = 300.0,
        positions_lock: threading.Lock | None = None,
        capital_lock_cm: Callable[[], AbstractContextManager[None]] | None = None,
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
        self._positions_lock = positions_lock
        self._capital_lock_cm = capital_lock_cm
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
        '''Acquire the lock chain and call `state_store.checkpoint()`.

        A failure here must not abort the loop — disk-full, transient
        I/O error, or msgpack failure are all logged at ERROR and the
        next tick still fires. The WAL keeps growing (no truncation
        happens on a failed checkpoint) so no state is lost; the next
        successful checkpoint catches up.

        The scheduler's internal `_lock` is intentionally NOT acquired
        inside the heavy lock chain — that would add `_lock` between
        `CapitalController._lock` and `wal_lock` in the documented
        ordering. A racing `stop()` between the `_tick` entry check
        and the checkpoint completing simply writes one extra
        snapshot; duplicate checkpoints are harmless (no ratchet
        semantics to defeat).
        '''

        positions_cm: AbstractContextManager[bool | None] = (
            self._positions_lock if self._positions_lock is not None else nullcontext()
        )
        capital_cm: AbstractContextManager[bool | None] = (
            self._capital_lock_cm() if self._capital_lock_cm is not None else nullcontext()
        )

        try:
            with positions_cm, capital_cm:
                self._state_store.checkpoint(self._state)
        except Exception:  # noqa: BLE001 - checkpoint failure must not abort the loop
            _log.exception('snapshot checkpoint failed; next tick will retry')
