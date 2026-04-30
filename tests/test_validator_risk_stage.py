'''Verify risk-stage adapter and breach mapping behavior.'''

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from nexus.core.domain.enums import BreachLevel
from nexus.core.domain.instance_state import InstanceState
from nexus.core.domain.risk_state import RiskState
from nexus.core.validator import (
    RiskStageLimits,
    ValidationRequestContext,
    ValidationStage,
    evaluate_risk_breach,
    validate_risk_stage,
)
from nexus.instance_config import InstanceConfig


def _make_context(
    *,
    risk_state: RiskState | None = None,
    **overrides: Any,
) -> ValidationRequestContext:
    config = InstanceConfig(
        account_id='acc_001',
        venue='binance_spot',
    )
    state = InstanceState.fresh(Decimal('10000'))
    state.risk = risk_state or RiskState()

    defaults: dict[str, Any] = {
        'strategy_id': 'strat_a',
        'command_id': 'cmd_risk_1',
        'order_notional': Decimal('100'),
        'estimated_fees': Decimal('1'),
        'strategy_budget': Decimal('5000'),
        'state': state,
        'config': config,
    }
    defaults.update(overrides)
    return ValidationRequestContext(**defaults)


class TestRiskStageLimits:
    def test_rejects_negative_limit(self) -> None:
        with pytest.raises(ValueError, match='max_total_drawdown_pct'):
            RiskStageLimits(max_total_drawdown_pct=Decimal('-0.1'))


class TestEvaluateRiskBreach:
    def test_returns_none_when_within_limits(self) -> None:
        rs = RiskState(
            total_drawdown=Decimal('100'),
            total_drawdown_pct=Decimal('0.05'),
            max_drawdown=Decimal('120'),
            max_drawdown_pct=Decimal('0.06'),
        )
        metrics = rs.to_risk_check_metrics()
        limits = RiskStageLimits(
            max_total_drawdown=Decimal('200'),
            max_total_drawdown_pct=Decimal('0.10'),
            max_drawdown_limit=Decimal('300'),
            max_drawdown_pct_limit=Decimal('0.20'),
        )

        level, code, message = evaluate_risk_breach(metrics, limits)

        assert level == BreachLevel.NONE
        assert code is None
        assert message is None

    def test_returns_breach_for_total_drawdown_pct(self) -> None:
        rs = RiskState(
            total_drawdown=Decimal('100'),
            total_drawdown_pct=Decimal('0.11'),
            max_drawdown=Decimal('120'),
            max_drawdown_pct=Decimal('0.12'),
        )
        level, code, message = evaluate_risk_breach(
            rs.to_risk_check_metrics(),
            RiskStageLimits(max_total_drawdown_pct=Decimal('0.10')),
        )

        assert level == BreachLevel.BREACH
        assert code == 'RISK_TOTAL_DRAWDOWN_PCT_LIMIT'
        assert message is not None


class TestValidateRiskStage:
    def test_allows_when_under_limits(self) -> None:
        ctx = _make_context(
            risk_state=RiskState(
                total_drawdown=Decimal('30'),
                total_drawdown_pct=Decimal('0.02'),
                max_drawdown=Decimal('40'),
                max_drawdown_pct=Decimal('0.03'),
            )
        )
        decision = validate_risk_stage(
            ctx,
            RiskStageLimits(
                max_total_drawdown=Decimal('100'),
                max_total_drawdown_pct=Decimal('0.10'),
            ),
        )
        assert decision.allowed is True

    def test_denies_when_limit_breached(self) -> None:
        ctx = _make_context(
            risk_state=RiskState(
                total_drawdown=Decimal('30'),
                total_drawdown_pct=Decimal('0.12'),
                max_drawdown=Decimal('40'),
                max_drawdown_pct=Decimal('0.12'),
            )
        )
        decision = validate_risk_stage(
            ctx,
            RiskStageLimits(max_total_drawdown_pct=Decimal('0.10')),
        )

        assert decision.allowed is False
        assert decision.failed_stage == ValidationStage.RISK
        assert decision.reason_code == 'RISK_TOTAL_DRAWDOWN_PCT_LIMIT'


class TestRollingLossEnforcement:
    '''MAJOR-H: rolling_loss_24h/7d/30d are now consulted by the
    validator. Pre-fix the fields were tracked, persisted via WAL codec,
    and `state_store.refresh_rolling_losses` existed for decay — but no
    validator stage read them. Configured limits were silently ignored.
    '''

    def _state_with_rolling_loss(
        self,
        rolling_loss_24h: Decimal = Decimal('0'),
        rolling_loss_7d: Decimal = Decimal('0'),
        rolling_loss_30d: Decimal = Decimal('0'),
    ) -> InstanceState:
        from nexus.core.domain.risk_state import StrategyRiskState
        state = InstanceState.fresh(Decimal('10000'))
        state.risk.per_strategy['strat_a'] = StrategyRiskState(
            strategy_id='strat_a',
            rolling_loss_24h=rolling_loss_24h,
            rolling_loss_7d=rolling_loss_7d,
            rolling_loss_30d=rolling_loss_30d,
        )
        return state

    def test_rolling_loss_24h_within_limit_allowed(self) -> None:
        ctx = _make_context(
            risk_state=self._state_with_rolling_loss(rolling_loss_24h=Decimal('40')).risk,
        )
        decision = validate_risk_stage(
            ctx,
            RiskStageLimits(max_rolling_loss_24h=Decimal('100')),
        )
        assert decision.allowed is True

    def test_rolling_loss_24h_exceeds_limit_denied(self) -> None:
        ctx = _make_context(
            risk_state=self._state_with_rolling_loss(rolling_loss_24h=Decimal('150')).risk,
        )
        decision = validate_risk_stage(
            ctx,
            RiskStageLimits(max_rolling_loss_24h=Decimal('100')),
        )
        assert decision.allowed is False
        assert decision.reason_code == 'RISK_ROLLING_LOSS_24H_LIMIT'

    def test_rolling_loss_7d_exceeds_limit_denied(self) -> None:
        ctx = _make_context(
            risk_state=self._state_with_rolling_loss(rolling_loss_7d=Decimal('500')).risk,
        )
        decision = validate_risk_stage(
            ctx,
            RiskStageLimits(max_rolling_loss_7d=Decimal('400')),
        )
        assert decision.allowed is False
        assert decision.reason_code == 'RISK_ROLLING_LOSS_7D_LIMIT'

    def test_rolling_loss_30d_exceeds_limit_denied(self) -> None:
        ctx = _make_context(
            risk_state=self._state_with_rolling_loss(rolling_loss_30d=Decimal('900')).risk,
        )
        decision = validate_risk_stage(
            ctx,
            RiskStageLimits(max_rolling_loss_30d=Decimal('500')),
        )
        assert decision.allowed is False
        assert decision.reason_code == 'RISK_ROLLING_LOSS_30D_LIMIT'

    def test_rolling_loss_no_limit_configured_allowed(self) -> None:
        '''When max_rolling_loss_* is None (not configured), the field
        is not consulted regardless of value. Backward-compat for
        deployments not yet wiring rolling-loss limits.
        '''

        ctx = _make_context(
            risk_state=self._state_with_rolling_loss(
                rolling_loss_24h=Decimal('99999'),
            ).risk,
        )
        decision = validate_risk_stage(ctx, RiskStageLimits())
        assert decision.allowed is True
