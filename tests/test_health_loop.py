'''Tests for HealthLoop periodic re-evaluation.'''

from __future__ import annotations

import threading
from datetime import datetime
from decimal import Decimal

import pytest

from nexus.core.domain.capital_state import CapitalState
from nexus.core.domain.enums import OperationalMode
from nexus.core.domain.instance_state import InstanceState
from nexus.core.health_evaluator import HealthEvaluator, HealthSnapshot, HealthThresholds
from nexus.core.health_loop import HealthLoop
from nexus.core.mode_controller import ModeController


def _make_state() -> InstanceState:
    return InstanceState(capital=CapitalState(capital_pool=Decimal('10000')))


def _make_evaluator() -> HealthEvaluator:
    return HealthEvaluator(
        thresholds=HealthThresholds(
            latency_warn_ms=10.0,
            latency_breach_ms=100.0,
            latency_halt_ms=1000.0,
        ),
    )


def test_invalid_interval_rejected() -> None:
    state = _make_state()
    evaluator = _make_evaluator()

    with pytest.raises(ValueError, match='interval_seconds must be a positive number'):
        HealthLoop(snapshot_provider=lambda: HealthSnapshot(), evaluator=evaluator, state=state, interval_seconds=0)


def test_bool_interval_rejected() -> None:
    state = _make_state()
    evaluator = _make_evaluator()

    with pytest.raises(ValueError, match='interval_seconds must be a positive number'):
        HealthLoop(
            snapshot_provider=lambda: HealthSnapshot(),
            evaluator=evaluator,
            state=state,
            interval_seconds=True,  # type: ignore[arg-type]
        )


def test_tick_once_no_transition_when_active() -> None:
    state = _make_state()
    snapshot = HealthSnapshot()
    loop = HealthLoop(
        snapshot_provider=lambda: snapshot,
        evaluator=_make_evaluator(),
        state=state,
    )

    loop.tick_once()

    assert state.mode.mode == OperationalMode.ACTIVE
    assert state.mode.trigger == 'init'


def test_tick_once_transitions_to_reduce_only() -> None:
    state = _make_state()
    snapshot = HealthSnapshot(latency_p99_ms=200.0)
    loop = HealthLoop(
        snapshot_provider=lambda: snapshot,
        evaluator=_make_evaluator(),
        state=state,
    )

    loop.tick_once()

    assert state.mode.mode == OperationalMode.REDUCE_ONLY
    assert state.mode.trigger == 'health'
    assert state.mode.transitioned_at != datetime.min


def test_tick_once_transitions_through_active_reduce_halted() -> None:
    state = _make_state()
    snapshots = iter([
        HealthSnapshot(),
        HealthSnapshot(latency_p99_ms=200.0),
        HealthSnapshot(latency_p99_ms=2000.0),
    ])
    loop = HealthLoop(
        snapshot_provider=lambda: next(snapshots),
        evaluator=_make_evaluator(),
        state=state,
    )

    loop.tick_once()
    assert state.mode.mode == OperationalMode.ACTIVE

    loop.tick_once()
    assert state.mode.mode == OperationalMode.REDUCE_ONLY

    loop.tick_once()
    assert state.mode.mode == OperationalMode.HALTED


def test_tick_once_provider_exception_swallowed() -> None:
    state = _make_state()

    def bad_provider() -> HealthSnapshot:
        raise RuntimeError('praxis down')

    loop = HealthLoop(
        snapshot_provider=bad_provider,
        evaluator=_make_evaluator(),
        state=state,
    )

    loop.tick_once()

    assert state.mode.mode == OperationalMode.ACTIVE


def test_start_then_stop_is_clean() -> None:
    state = _make_state()
    loop = HealthLoop(
        snapshot_provider=lambda: HealthSnapshot(),
        evaluator=_make_evaluator(),
        state=state,
        interval_seconds=0.05,
    )

    assert loop.running is False
    loop.start()
    assert loop.running is True
    loop.stop()
    assert loop.running is False


def test_periodic_tick_invokes_provider() -> None:
    state = _make_state()
    invocations = 0
    invoked = threading.Event()

    def provider() -> HealthSnapshot:
        nonlocal invocations
        invocations += 1
        invoked.set()
        return HealthSnapshot()

    loop = HealthLoop(
        snapshot_provider=provider,
        evaluator=_make_evaluator(),
        state=state,
        interval_seconds=0.05,
    )

    loop.start()
    triggered = invoked.wait(timeout=1.0)
    loop.stop()

    assert triggered
    assert invocations >= 1


def test_start_is_idempotent() -> None:
    state = _make_state()
    loop = HealthLoop(
        snapshot_provider=lambda: HealthSnapshot(),
        evaluator=_make_evaluator(),
        state=state,
        interval_seconds=0.05,
    )

    loop.start()
    loop.start()
    assert loop.running is True
    loop.stop()


def test_in_flight_tick_does_not_overwrite_mode_after_stop() -> None:
    '''PT-FIX-42: A `_tick` already past the `_running` check at the
    top of `_tick` continues into `_apply_snapshot` even after
    `stop()` is called. Pre-fix the post-stop tick wrote
    `state.mode = ModeState(mode=ACTIVE, ...)`, defeating any
    ratchet (e.g. PT-FIX-25's HALTED flip applied after `stop()`).

    Post-fix `_apply_snapshot` re-checks `_running` under the lock
    BEFORE the mode write — the post-stop tick observes
    `_running=False` and skips the mutation.

    Drives the race deterministically: the snapshot provider blocks
    inside the in-flight tick until the test calls `stop()`, then
    the tick resumes. Asserts `state.mode` was NOT mutated by the
    post-stop tick.
    '''

    state = _make_state()

    snapshot_started = threading.Event()
    release_snapshot = threading.Event()
    state.mode = state.mode  # baseline: ACTIVE

    def _slow_provider() -> HealthSnapshot:
        snapshot_started.set()
        release_snapshot.wait(timeout=2.0)
        return HealthSnapshot(latency_p99_ms=500.0)

    loop = HealthLoop(
        snapshot_provider=_slow_provider,
        evaluator=_make_evaluator(),
        state=state,
        interval_seconds=0.05,
    )

    loop.start()
    if not snapshot_started.wait(timeout=2.0):
        pytest.fail('provider was not called within timeout')

    in_flight_timer = loop._timer
    assert in_flight_timer is not None, 'in-flight tick timer should still be set before stop()'

    loop.stop()

    sentinel_trigger = 'sentinel-shutdown-ratchet'
    state.mode.trigger = sentinel_trigger

    release_snapshot.set()

    in_flight_timer.join(timeout=5.0)
    if in_flight_timer.is_alive():
        pytest.fail('in-flight tick did not complete within 5s after release_snapshot.set()')

    assert state.mode.trigger == sentinel_trigger, (
        'post-stop tick overwrote state.mode after stop() returned; '
        'PT-FIX-25 HALTED ratchet would be defeated'
    )


def test_tick_once_still_writes_mode_when_loop_not_started() -> None:
    '''PT-FIX-42 must not regress `tick_once`'s manual-driver use case.
    `tick_once` bypasses the `_running` re-check (via
    `respect_running=False`) so callers driving the loop manually
    (without `start()`) still get the resulting mode write.'''

    state = _make_state()
    state.mode = state.mode

    loop = HealthLoop(
        snapshot_provider=lambda: HealthSnapshot(latency_p99_ms=500.0),
        evaluator=_make_evaluator(),
        state=state,
        interval_seconds=0.05,
    )

    assert loop.running is False

    loop.tick_once()

    assert state.mode.mode != OperationalMode.ACTIVE


def test_tick_invokes_rolling_loss_refresher_when_wired() -> None:
    '''MAJOR-H: HealthLoop calls the optional rolling_loss_refresher
    on each tick to decay rolling_loss_24h/7d/30d as old loss events
    age out of their windows. Without periodic refresh the field
    over-counts (losses outside the window are never dropped) and the
    risk-stage rolling-loss limits become over-conservative.
    '''

    state = _make_state()
    snapshot = HealthSnapshot()
    refresh_calls: list[InstanceState] = []

    def _refresher(s: InstanceState) -> None:
        refresh_calls.append(s)

    loop = HealthLoop(
        snapshot_provider=lambda: snapshot,
        evaluator=_make_evaluator(),
        state=state,
        rolling_loss_refresher=_refresher,
    )

    loop.tick_once()

    assert len(refresh_calls) == 1
    assert refresh_calls[0] is state


def test_tick_swallows_rolling_loss_refresher_exception() -> None:
    '''Refresher exception must not abort the tick — health evaluation
    is the primary job; rolling-loss decay is best-effort.
    '''

    state = _make_state()
    snapshot = HealthSnapshot()

    def _refresher(_s: InstanceState) -> None:
        msg = 'WAL read failed'
        raise RuntimeError(msg)

    loop = HealthLoop(
        snapshot_provider=lambda: snapshot,
        evaluator=_make_evaluator(),
        state=state,
        rolling_loss_refresher=_refresher,
    )

    loop.tick_once()

    assert state.mode.mode == OperationalMode.ACTIVE


def test_mode_controller_hook_keeps_manual_halt_through_healthy_tick() -> None:
    state = _make_state()
    controller = ModeController(state, threading.Lock())
    controller.set_manual_halt('manual stop')
    loop = HealthLoop(
        snapshot_provider=lambda: HealthSnapshot(),
        evaluator=_make_evaluator(),
        state=state,
        mode_controller=controller,
    )

    loop.tick_once()

    assert state.mode.mode is OperationalMode.HALTED
    assert state.mode.trigger == 'manual'


def test_mode_controller_hook_applies_health_halt() -> None:
    state = _make_state()
    controller = ModeController(state, threading.Lock())
    loop = HealthLoop(
        snapshot_provider=lambda: HealthSnapshot(latency_p99_ms=5000.0),
        evaluator=_make_evaluator(),
        state=state,
        mode_controller=controller,
    )

    loop.tick_once()

    assert state.mode.mode is OperationalMode.HALTED
    assert state.mode.trigger == 'health'
