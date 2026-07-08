import threading
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from nexus.core.domain.capital_state import CapitalState
from nexus.core.domain.enums import OperationalMode
from nexus.core.domain.instance_state import InstanceState
from nexus.core.domain.risk_state import StrategyRiskState
from nexus.core.mode_controller import ModeController, RiskBreakerThresholds

_TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _state() -> InstanceState:
    return InstanceState(capital=CapitalState(capital_pool=Decimal('10000')))


def _controller(state: InstanceState) -> ModeController:
    return ModeController(state, threading.Lock(), clock=lambda: _TS)


def test_manual_halt_sets_halted():
    state = _state()
    controller = _controller(state)

    assert controller.set_manual_halt('manual stop')
    assert state.mode.mode is OperationalMode.HALTED
    assert state.mode.trigger == 'manual'
    assert state.mode_holds.manual_hold.active
    assert state.mode_holds.manual_hold.since == _TS


def test_healthy_tick_cannot_lift_manual_halt():
    state = _state()
    controller = _controller(state)
    controller.set_manual_halt('manual stop')

    changed = controller.apply_health_mode(OperationalMode.ACTIVE)

    assert not changed
    assert state.mode.mode is OperationalMode.HALTED


def test_resume_holds_halted_while_risk_hold_active():
    state = _state()
    controller = _controller(state)
    controller.set_manual_halt('manual stop')
    controller.set_daily_loss_halt('daily loss breached')

    changed = controller.clear_manual_halt()

    assert not changed
    assert state.mode.mode is OperationalMode.HALTED
    assert state.mode.trigger == 'risk'


def test_resume_returns_to_health_mode_when_no_other_hold():
    state = _state()
    controller = _controller(state)
    controller.apply_health_mode(OperationalMode.REDUCE_ONLY)
    controller.set_manual_halt('manual stop')

    controller.clear_manual_halt()

    assert state.mode.mode is OperationalMode.REDUCE_ONLY
    assert state.mode.trigger == 'health'


def test_resume_stays_halted_when_health_is_halted():
    state = _state()
    controller = _controller(state)
    controller.set_manual_halt('manual stop')
    controller.apply_health_mode(OperationalMode.HALTED)

    controller.clear_manual_halt()

    assert state.mode.mode is OperationalMode.HALTED
    assert state.mode.trigger == 'health'


def test_health_drives_mode_without_holds():
    state = _state()
    controller = _controller(state)

    assert controller.apply_health_mode(OperationalMode.REDUCE_ONLY)
    assert state.mode.mode is OperationalMode.REDUCE_ONLY
    assert state.mode.trigger == 'health'


def test_all_holds_cleared_and_healthy_returns_active():
    state = _state()
    controller = _controller(state)
    controller.set_daily_loss_halt('daily loss breached')
    controller.apply_health_mode(OperationalMode.ACTIVE)

    controller.clear_daily_loss_halt()

    assert state.mode.mode is OperationalMode.ACTIVE


def test_drawdown_hold_is_independent_of_daily_loss():
    state = _state()
    controller = _controller(state)
    controller.set_daily_loss_halt('daily loss breached')
    controller.set_drawdown_halt('drawdown breached')

    controller.clear_daily_loss_halt()

    assert state.mode.mode is OperationalMode.HALTED
    assert state.mode_holds.risk_drawdown.active


def test_repeated_halt_is_idempotent():
    state = _state()
    controller = _controller(state)
    controller.set_manual_halt('manual stop')

    assert not controller.set_manual_halt('manual stop again')


def test_clearing_inactive_hold_is_a_noop():
    state = _state()
    controller = _controller(state)

    assert not controller.clear_manual_halt()
    assert state.mode.mode is OperationalMode.ACTIVE


def _breaker(state: InstanceState, thresholds: RiskBreakerThresholds) -> ModeController:
    return ModeController(state, threading.Lock(), clock=lambda: _TS, risk_thresholds=thresholds)


def test_daily_loss_breaker_trips_and_auto_clears():
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


def test_daily_loss_sums_across_strategies():
    state = _state()
    state.risk.per_strategy['s1'] = StrategyRiskState(strategy_id='s1', rolling_loss_24h=Decimal('150'))
    state.risk.per_strategy['s2'] = StrategyRiskState(strategy_id='s2', rolling_loss_24h=Decimal('150'))
    controller = _breaker(state, RiskBreakerThresholds(max_daily_loss=Decimal('250')))

    controller.evaluate_risk()

    assert state.mode_holds.risk_daily_loss.active


def test_drawdown_breaker_trips_and_does_not_auto_clear():
    state = _state()
    state.risk.max_total_drawdown_pct = Decimal('0.08')
    controller = _breaker(state, RiskBreakerThresholds(max_drawdown_pct=Decimal('0.05')))

    controller.evaluate_risk()

    assert state.mode_holds.risk_drawdown.active

    state.risk.max_total_drawdown_pct = Decimal('0.01')
    controller.evaluate_risk()

    assert state.mode_holds.risk_drawdown.active


def test_evaluate_risk_without_thresholds_is_a_noop():
    state = _state()
    state.risk.per_strategy['s1'] = StrategyRiskState(strategy_id='s1', rolling_loss_24h=Decimal('9999'))
    controller = ModeController(state, threading.Lock(), clock=lambda: _TS)

    controller.evaluate_risk()

    assert state.mode.mode is OperationalMode.ACTIVE


def test_risk_breaker_thresholds_reject_negative():
    with pytest.raises(ValueError, match='non-negative'):
        RiskBreakerThresholds(max_daily_loss=Decimal('-1'))
