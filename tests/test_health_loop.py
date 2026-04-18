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
