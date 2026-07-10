import threading
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from nexus.core.domain.capital_state import CapitalState
from nexus.core.domain.enums import OperationalMode
from nexus.core.domain.instance_state import InstanceState
from nexus.core.domain.operational_mode import HaltHold, ModeState
from nexus.core.domain.risk_breaker_thresholds import RiskBreakerThresholds
from nexus.core.domain.risk_state import StrategyRiskState
from nexus.core.mode_controller import ModeController

_TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _state() -> InstanceState:
    return InstanceState(capital=CapitalState(capital_pool=Decimal('10000')))


def _controller(state: InstanceState) -> ModeController:
    return ModeController(state, threading.Lock(), clock=lambda: _TS)


def test_manual_halt_sets_halted() -> None:
    state = _state()
    controller = _controller(state)

    assert controller.set_manual_halt('manual stop')
    assert state.mode.mode is OperationalMode.HALTED
    assert state.mode.trigger == 'manual'
    assert state.mode_holds.manual_hold.active
    assert state.mode_holds.manual_hold.since == _TS


def test_healthy_tick_cannot_lift_manual_halt() -> None:
    state = _state()
    controller = _controller(state)
    controller.set_manual_halt('manual stop')

    changed = controller.apply_health_mode(OperationalMode.ACTIVE)

    assert not changed
    assert state.mode.mode is OperationalMode.HALTED


def test_resume_holds_halted_while_risk_hold_active() -> None:
    state = _state()
    controller = _controller(state)
    controller.set_manual_halt('manual stop')
    controller.set_daily_loss_halt('daily loss breached')

    changed = controller.clear_manual_halt()

    assert not changed
    assert state.mode.mode is OperationalMode.HALTED
    assert state.mode.trigger == 'risk'


def test_resume_returns_to_health_mode_when_no_other_hold() -> None:
    state = _state()
    controller = _controller(state)
    controller.apply_health_mode(OperationalMode.REDUCE_ONLY)
    controller.set_manual_halt('manual stop')

    controller.clear_manual_halt()

    assert state.mode.mode is OperationalMode.REDUCE_ONLY
    assert state.mode.trigger == 'health'


def test_resume_stays_halted_when_health_is_halted() -> None:
    state = _state()
    controller = _controller(state)
    controller.set_manual_halt('manual stop')
    controller.apply_health_mode(OperationalMode.HALTED)

    controller.clear_manual_halt()

    assert state.mode.mode is OperationalMode.HALTED
    assert state.mode.trigger == 'health'


def test_health_drives_mode_without_holds() -> None:
    state = _state()
    controller = _controller(state)

    assert controller.apply_health_mode(OperationalMode.REDUCE_ONLY)
    assert state.mode.mode is OperationalMode.REDUCE_ONLY
    assert state.mode.trigger == 'health'


def test_all_holds_cleared_and_healthy_returns_active() -> None:
    state = _state()
    controller = _controller(state)
    controller.set_daily_loss_halt('daily loss breached')
    controller.apply_health_mode(OperationalMode.ACTIVE)

    controller.clear_daily_loss_halt()

    assert state.mode.mode is OperationalMode.ACTIVE


def test_drawdown_hold_is_independent_of_daily_loss() -> None:
    state = _state()
    controller = _controller(state)
    controller.set_daily_loss_halt('daily loss breached')
    controller.set_drawdown_halt('drawdown breached')

    controller.clear_daily_loss_halt()

    assert state.mode.mode is OperationalMode.HALTED
    assert state.mode_holds.risk_drawdown.active


def test_repeated_halt_is_idempotent() -> None:
    state = _state()
    controller = _controller(state)
    controller.set_manual_halt('manual stop')

    assert not controller.set_manual_halt('manual stop again')


def test_clearing_inactive_hold_is_a_noop() -> None:
    state = _state()
    controller = _controller(state)

    assert not controller.clear_manual_halt()
    assert state.mode.mode is OperationalMode.ACTIVE


def _breaker(state: InstanceState, thresholds: RiskBreakerThresholds) -> ModeController:
    return ModeController(state, threading.Lock(), clock=lambda: _TS, risk_thresholds=thresholds)


def test_daily_loss_breaker_trips_and_auto_clears() -> None:
    state = _state()
    state.risk.per_strategy['s1'] = StrategyRiskState(strategy_id='s1', rolling_loss_24h=Decimal('300'))
    controller = _breaker(state, RiskBreakerThresholds(max_daily_loss=Decimal('250')))

    controller.evaluate_risk()

    assert state.mode.mode is OperationalMode.HALTED
    assert state.mode.trigger == 'risk'
    assert state.mode_holds.risk_daily_loss.active

    state.risk.per_strategy['s1'].rolling_loss_24h = Decimal('100')
    controller.evaluate_risk()

    assert not state.mode_holds.risk_daily_loss.active
    assert state.mode.mode is OperationalMode.ACTIVE


def test_daily_loss_sums_across_strategies() -> None:
    state = _state()
    state.risk.per_strategy['s1'] = StrategyRiskState(strategy_id='s1', rolling_loss_24h=Decimal('150'))
    state.risk.per_strategy['s2'] = StrategyRiskState(strategy_id='s2', rolling_loss_24h=Decimal('150'))
    controller = _breaker(state, RiskBreakerThresholds(max_daily_loss=Decimal('250')))

    controller.evaluate_risk()

    assert state.mode_holds.risk_daily_loss.active


def test_drawdown_breaker_trips_and_does_not_auto_clear() -> None:
    state = _state()
    state.risk.max_total_drawdown_pct = Decimal('0.08')
    controller = _breaker(state, RiskBreakerThresholds(max_drawdown_pct=Decimal('0.05')))

    controller.evaluate_risk()

    assert state.mode_holds.risk_drawdown.active

    state.risk.max_total_drawdown_pct = Decimal('0.01')
    controller.evaluate_risk()

    assert state.mode_holds.risk_drawdown.active


def test_evaluate_risk_without_thresholds_is_a_noop() -> None:
    state = _state()
    state.risk.per_strategy['s1'] = StrategyRiskState(strategy_id='s1', rolling_loss_24h=Decimal('9999'))
    controller = ModeController(state, threading.Lock(), clock=lambda: _TS)

    controller.evaluate_risk()

    assert state.mode.mode is OperationalMode.ACTIVE


def test_risk_breaker_thresholds_reject_negative() -> None:
    with pytest.raises(ValueError, match='positive'):
        RiskBreakerThresholds(max_daily_loss=Decimal('-1'))


def test_risk_breaker_thresholds_reject_zero() -> None:
    with pytest.raises(ValueError, match='positive'):
        RiskBreakerThresholds(max_drawdown_pct=Decimal('0'))


def test_drawdown_breaker_trips_on_absolute_limit() -> None:
    state = _state()
    state.risk.max_total_drawdown = Decimal('1500')
    controller = _breaker(state, RiskBreakerThresholds(max_drawdown=Decimal('1000')))

    controller.evaluate_risk()

    assert state.mode_holds.risk_drawdown.active
    assert state.mode.mode is OperationalMode.HALTED


def test_reconcile_preserves_a_recovered_health_halt() -> None:
    state = _state()
    state.mode = ModeState(mode=OperationalMode.HALTED, trigger='health', transitioned_at=_TS)
    controller = _controller(state)

    controller.reconcile()

    assert state.mode.mode is OperationalMode.HALTED


def test_reconcile_keeps_a_recovered_manual_hold() -> None:
    state = _state()
    state.mode_holds.manual_hold = HaltHold(active=True, reason='manual stop', since=_TS)
    state.mode = ModeState(mode=OperationalMode.HALTED, trigger='manual', transitioned_at=_TS)
    controller = _controller(state)

    controller.reconcile()

    assert state.mode.mode is OperationalMode.HALTED
    assert state.mode.trigger == 'manual'


def test_reconcile_retrips_risk_from_recovered_state() -> None:
    state = _state()
    state.risk.per_strategy['s1'] = StrategyRiskState(strategy_id='s1', rolling_loss_24h=Decimal('300'))
    controller = _breaker(state, RiskBreakerThresholds(max_daily_loss=Decimal('250')))

    controller.reconcile()

    assert state.mode.mode is OperationalMode.HALTED
    assert state.mode_holds.risk_daily_loss.active


def test_reconcile_leaves_a_clean_state_active() -> None:
    state = _state()
    controller = _controller(state)

    controller.reconcile()

    assert state.mode.mode is OperationalMode.ACTIVE


def test_on_halt_fires_with_source_on_halt_transition() -> None:
    state = _state()
    sources: list[str] = []
    controller = ModeController(state, threading.Lock(), clock=lambda: _TS, on_halt=sources.append)

    controller.set_manual_halt('manual stop')

    assert sources == ['manual']


def test_on_halt_does_not_fire_without_a_halt_transition() -> None:
    state = _state()
    sources: list[str] = []
    controller = ModeController(state, threading.Lock(), clock=lambda: _TS, on_halt=sources.append)

    controller.apply_health_mode(OperationalMode.REDUCE_ONLY)

    assert sources == []


def test_on_halt_not_refired_when_already_halted() -> None:
    state = _state()
    sources: list[str] = []
    controller = ModeController(state, threading.Lock(), clock=lambda: _TS, on_halt=sources.append)
    controller.set_manual_halt('manual stop')

    controller.set_daily_loss_halt('daily loss')

    assert sources == ['manual']


def test_on_halt_failure_does_not_break_controller() -> None:
    state = _state()

    def boom(_source: str) -> None:
        raise RuntimeError('alert down')

    controller = ModeController(state, threading.Lock(), clock=lambda: _TS, on_halt=boom)

    controller.set_manual_halt('manual stop')

    assert state.mode.mode is OperationalMode.HALTED


def test_on_halt_runs_outside_the_lock() -> None:
    state = _state()
    holder: dict[str, ModeController] = {}

    def on_halt(_source: str) -> None:
        holder['controller'].clear_daily_loss_halt()

    controller = ModeController(state, threading.Lock(), clock=lambda: _TS, on_halt=on_halt)
    holder['controller'] = controller

    done = threading.Event()

    def run() -> None:
        controller.set_manual_halt('manual stop')
        done.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    assert done.wait(timeout=2.0), 'on_halt ran under the lock and deadlocked'
    assert state.mode.mode is OperationalMode.HALTED


def test_notify_pending_halt_skips_when_no_longer_halted() -> None:
    state = _state()
    state.risk.per_strategy['s1'] = StrategyRiskState(strategy_id='s1', rolling_loss_24h=Decimal('300'))
    sources: list[str] = []
    controller = ModeController(
        state, threading.Lock(), clock=lambda: _TS,
        risk_thresholds=RiskBreakerThresholds(max_daily_loss=Decimal('250')),
        on_halt=sources.append,
    )

    controller.evaluate_risk(notify=False)
    assert state.mode.mode is OperationalMode.HALTED

    state.risk.per_strategy['s1'].rolling_loss_24h = Decimal('100')
    controller.evaluate_risk(notify=False)
    controller.notify_pending_halt()

    assert sources == []


def test_evaluate_risk_acquires_the_risk_lock() -> None:
    state = _state()
    state.risk.lock = threading.Lock()
    state.risk.per_strategy['s1'] = StrategyRiskState(strategy_id='s1', rolling_loss_24h=Decimal('300'))
    controller = _breaker(state, RiskBreakerThresholds(max_daily_loss=Decimal('250')))
    done = threading.Event()

    def run() -> None:
        controller.evaluate_risk()
        done.set()

    with state.risk.lock:
        worker = threading.Thread(target=run)
        worker.start()
        blocked = not done.wait(timeout=0.2)

    worker.join(timeout=2.0)

    assert blocked
    assert state.mode.mode is OperationalMode.HALTED


def test_reconcile_acquires_the_risk_lock() -> None:
    state = _state()
    state.risk.lock = threading.Lock()
    state.risk.per_strategy['s1'] = StrategyRiskState(strategy_id='s1', rolling_loss_24h=Decimal('300'))
    controller = _breaker(state, RiskBreakerThresholds(max_daily_loss=Decimal('250')))
    done = threading.Event()

    def run() -> None:
        controller.reconcile()
        done.set()

    with state.risk.lock:
        worker = threading.Thread(target=run)
        worker.start()
        blocked = not done.wait(timeout=0.2)

    worker.join(timeout=2.0)

    assert blocked
    assert state.mode.mode is OperationalMode.HALTED


class _CountingLock:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.entries = 0

    def __enter__(self) -> '_CountingLock':
        self.entries += 1
        self._lock.acquire()
        return self

    def __exit__(self, *args: object) -> None:
        self._lock.release()


def test_apply_health_and_risk_commits_under_a_single_lock_acquisition() -> None:
    state = _state()
    state.risk.per_strategy['s1'] = StrategyRiskState(strategy_id='s1', rolling_loss_24h=Decimal('300'))
    lock = _CountingLock()
    controller = ModeController(
        state, lock, clock=lambda: _TS,  # type: ignore[arg-type]
        risk_thresholds=RiskBreakerThresholds(max_daily_loss=Decimal('250')),
    )

    controller.apply_health_and_risk(OperationalMode.ACTIVE, notify=False)

    assert lock.entries == 1
    assert state.mode.mode is OperationalMode.HALTED
    assert state.mode.trigger == 'risk'


def test_apply_health_and_risk_honours_health_halt() -> None:
    state = _state()
    controller = _controller(state)

    changed = controller.apply_health_and_risk(OperationalMode.HALTED)

    assert changed
    assert state.mode.mode is OperationalMode.HALTED
    assert state.mode.trigger == 'health'


def test_risk_breaker_thresholds_are_frozen() -> None:
    thresholds = RiskBreakerThresholds(max_daily_loss=Decimal('250'))

    with pytest.raises(FrozenInstanceError):
        thresholds.max_daily_loss = Decimal('1')  # type: ignore[misc]


def test_set_hold_reasserts_halt_after_external_mode_drift() -> None:
    state = _state()
    controller = _controller(state)
    controller.set_manual_halt('manual stop')
    state.mode = ModeState(mode=OperationalMode.ACTIVE, trigger='health', transitioned_at=_TS)

    changed = controller.set_manual_halt('manual stop')

    assert changed
    assert state.mode.mode is OperationalMode.HALTED
    assert state.mode.trigger == 'manual'


def test_clear_hold_reconciles_mode_after_external_drift() -> None:
    state = _state()
    controller = _controller(state)
    state.mode = ModeState(mode=OperationalMode.HALTED, trigger='manual', transitioned_at=_TS)

    changed = controller.clear_manual_halt()

    assert changed
    assert state.mode.mode is OperationalMode.ACTIVE


def test_shared_lock_wiring_does_not_deadlock() -> None:
    state = _state()
    shared = threading.Lock()
    state.risk.lock = shared
    state.risk.per_strategy['s1'] = StrategyRiskState(strategy_id='s1', rolling_loss_24h=Decimal('300'))
    controller = ModeController(
        state, shared, clock=lambda: _TS,
        risk_thresholds=RiskBreakerThresholds(max_daily_loss=Decimal('250')),
    )
    done = threading.Event()

    def run() -> None:
        controller.reconcile()
        controller.evaluate_risk()
        controller.apply_health_and_risk(OperationalMode.ACTIVE)
        done.set()

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    finished = done.wait(timeout=5)
    worker.join(timeout=5)

    assert finished
    assert state.mode.mode is OperationalMode.HALTED
    assert state.mode.trigger == 'risk'
