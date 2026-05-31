'''Tests for SnapshotScheduler periodic state-store checkpointing.

Pins: invalid intervals are rejected; tick_once writes a fresh snapshot
file under the lock chain; provider exceptions are swallowed and the
loop survives; start/stop is idempotent; a periodic tick fires off the
timer thread and reaches checkpoint().
'''

from __future__ import annotations

import threading
from decimal import Decimal
from pathlib import Path

import pytest

from nexus.core.domain.capital_state import CapitalState
from nexus.core.domain.instance_state import InstanceState
from nexus.infrastructure.snapshot_scheduler import SnapshotScheduler
from nexus.infrastructure.state_store import StateStore


def _make_state() -> InstanceState:
    return InstanceState(capital=CapitalState(capital_pool=Decimal('10000')))


def test_invalid_interval_rejected(tmp_path: Path) -> None:
    state = _make_state()
    store = StateStore(base_path=tmp_path)

    with pytest.raises(ValueError, match='interval_seconds must be a positive number'):
        SnapshotScheduler(
            state_store=store,
            state=state,
            interval_seconds=0,
        )


def test_bool_interval_rejected(tmp_path: Path) -> None:
    state = _make_state()
    store = StateStore(base_path=tmp_path)

    with pytest.raises(ValueError, match='interval_seconds must be a positive number'):
        SnapshotScheduler(
            state_store=store,
            state=state,
            interval_seconds=True,  # type: ignore[arg-type]
        )


def test_tick_once_writes_snapshot_file(tmp_path: Path) -> None:
    state = _make_state()
    store = StateStore(base_path=tmp_path)
    scheduler = SnapshotScheduler(state_store=store, state=state)

    snap_path = tmp_path / 'snapshots' / 'snapshot.bin'
    if snap_path.exists():
        snap_path.unlink()
    assert not snap_path.exists()

    scheduler.tick_once()

    assert snap_path.exists()
    assert snap_path.stat().st_size > 0


def test_tick_once_holds_positions_and_capital_locks(tmp_path: Path) -> None:
    '''Pin the lock-chain contract: the checkpoint call must run
    *inside* the positions_lock and the capital_lock_cm.
    '''

    state = _make_state()
    store = StateStore(base_path=tmp_path)
    positions_lock = threading.Lock()

    capital_lock_acquired = threading.Event()
    capital_lock_released = threading.Event()

    class _CapitalLock:
        def __enter__(self) -> None:
            capital_lock_acquired.set()

        def __exit__(self, *_args: object) -> None:
            capital_lock_released.set()

    def capital_lock_cm() -> _CapitalLock:
        return _CapitalLock()

    checkpoints_seen: list[bool] = []
    original_checkpoint = store.checkpoint

    def spying_checkpoint(s: InstanceState) -> None:
        checkpoints_seen.append(positions_lock.locked() and capital_lock_acquired.is_set())
        original_checkpoint(s)

    store.checkpoint = spying_checkpoint  # type: ignore[method-assign]

    scheduler = SnapshotScheduler(
        state_store=store,
        state=state,
        positions_lock=positions_lock,
        capital_lock_cm=capital_lock_cm,
    )

    scheduler.tick_once()

    assert checkpoints_seen == [True], (
        'checkpoint() must run while positions_lock is held and capital_lock_cm is in __enter__'
    )
    assert capital_lock_released.is_set()


def test_checkpoint_exception_swallowed(tmp_path: Path) -> None:
    '''A failing checkpoint must not abort the loop.'''

    state = _make_state()
    store = StateStore(base_path=tmp_path)

    def failing_checkpoint(_s: InstanceState) -> None:
        raise OSError('disk full simulation')

    store.checkpoint = failing_checkpoint  # type: ignore[method-assign]

    scheduler = SnapshotScheduler(state_store=store, state=state)
    scheduler.tick_once()
    scheduler.tick_once()


def test_start_stop_is_idempotent(tmp_path: Path) -> None:
    state = _make_state()
    store = StateStore(base_path=tmp_path)
    scheduler = SnapshotScheduler(state_store=store, state=state, interval_seconds=60)

    assert not scheduler.running

    scheduler.start()
    assert scheduler.running
    scheduler.start()
    assert scheduler.running

    scheduler.stop()
    assert not scheduler.running
    scheduler.stop()
    assert not scheduler.running


def test_periodic_tick_fires_off_timer(tmp_path: Path) -> None:
    '''Confirm the daemon timer actually invokes checkpoint() at
    least once on its own (without the test calling tick_once).
    '''

    state = _make_state()
    store = StateStore(base_path=tmp_path)

    tick_count = threading.Event()

    def spying_checkpoint(_s: InstanceState) -> None:
        tick_count.set()

    store.checkpoint = spying_checkpoint  # type: ignore[method-assign]

    scheduler = SnapshotScheduler(
        state_store=store,
        state=state,
        interval_seconds=0.05,
    )

    scheduler.start()
    try:
        fired = tick_count.wait(timeout=2.0)
    finally:
        scheduler.stop()

    assert fired, 'periodic timer must invoke checkpoint at least once within 2s at 0.05s interval'


def test_tick_once_writes_even_when_loop_is_stopped(tmp_path: Path) -> None:
    '''`tick_once` honours the caller's "checkpoint right now" intent
    even when `stop()` is called concurrently. Pins the design
    decision that `tick_once` does NOT consult the `_running` flag.

    A duplicate checkpoint after `stop()` is harmless (no ratchet
    semantics to defeat), so the simpler "tick_once always writes"
    contract is preferred over racing the running flag through the
    heavy lock chain.
    '''

    state = _make_state()
    store = StateStore(base_path=tmp_path)

    inside_lock_chain = threading.Event()
    proceed_to_checkpoint = threading.Event()
    checkpoint_calls: list[int] = []

    class _BlockingCapitalLock:
        def __enter__(self) -> None:
            inside_lock_chain.set()
            assert proceed_to_checkpoint.wait(timeout=2), (
                'main thread did not release the capital-lock barrier within 2s'
            )

        def __exit__(self, *_args: object) -> None:
            return None

    def spying_checkpoint(_s: InstanceState) -> None:
        checkpoint_calls.append(1)

    store.checkpoint = spying_checkpoint  # type: ignore[method-assign]

    scheduler = SnapshotScheduler(
        state_store=store,
        state=state,
        capital_lock_cm=_BlockingCapitalLock,
    )

    worker_thread = threading.Thread(
        target=scheduler.tick_once,
        daemon=True,
    )
    worker_thread.start()

    assert inside_lock_chain.wait(timeout=2)
    scheduler.start()
    scheduler.stop()
    proceed_to_checkpoint.set()
    worker_thread.join(timeout=2)

    assert not worker_thread.is_alive()
    assert checkpoint_calls == [1]
