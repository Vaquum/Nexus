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
