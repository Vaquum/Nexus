'''Verify validator pipeline stage ordering and model invariants.'''

from __future__ import annotations

from decimal import Decimal
from typing import Any, cast

import pytest

from nexus.core.validator import (
    DEFAULT_VALIDATION_STAGE_ORDER,
    ValidationDecision,
    ValidationRequestContext,
    ValidationStage,
)
from nexus.instance_config import InstanceConfig
from nexus.core.domain.instance_state import InstanceState


def _make_config() -> InstanceConfig:
    return InstanceConfig(
        account_id='acc_001',
        venue='binance_spot',
        allocated_capital=Decimal('10000'),
    )


def _make_context(**overrides: Any) -> ValidationRequestContext:
    config = _make_config()
    defaults: dict[str, Any] = {
        'strategy_id': 'strat_a',
        'order_notional': Decimal('100'),
        'estimated_fees': Decimal('1'),
        'strategy_budget': Decimal('5000'),
        'state': InstanceState.from_config(config),
        'config': config,
    }
    defaults.update(overrides)
    return ValidationRequestContext(**defaults)


class TestValidationStageOrder:
    def test_stage_order_is_fixed(self) -> None:
        assert DEFAULT_VALIDATION_STAGE_ORDER == (
            ValidationStage.INTAKE,
            ValidationStage.RISK,
            ValidationStage.PRICE,
            ValidationStage.CAPITAL,
            ValidationStage.HEALTH,
            ValidationStage.GATEWAY,
        )

    def test_stage_order_contains_each_stage_once(self) -> None:
        assert len(DEFAULT_VALIDATION_STAGE_ORDER) == len(ValidationStage)
        assert set(DEFAULT_VALIDATION_STAGE_ORDER) == set(ValidationStage)


class TestValidationRequestContext:
    def test_valid_context_construction(self) -> None:
        ctx = _make_context()
        assert ctx.strategy_id == 'strat_a'
        assert ctx.order_notional == Decimal('100')
        assert ctx.estimated_fees == Decimal('1')
        assert ctx.strategy_budget == Decimal('5000')

    def test_empty_strategy_id_rejected(self) -> None:
        with pytest.raises(ValueError, match='strategy_id'):
            _make_context(strategy_id='')

    def test_negative_order_notional_rejected(self) -> None:
        with pytest.raises(ValueError, match='order_notional'):
            _make_context(order_notional=Decimal('-1'))

    def test_non_decimal_estimated_fees_rejected(self) -> None:
        with pytest.raises(ValueError, match='estimated_fees'):
            _make_context(estimated_fees=cast(Decimal, cast(Any, 1)))

    def test_nan_strategy_budget_rejected(self) -> None:
        with pytest.raises(ValueError, match='strategy_budget'):
            _make_context(strategy_budget=Decimal('NaN'))

    def test_non_instance_state_rejected(self) -> None:
        with pytest.raises(ValueError, match='state'):
            _make_context(state=cast(InstanceState, cast(Any, object())))

    def test_non_instance_config_rejected(self) -> None:
        with pytest.raises(ValueError, match='config'):
            _make_context(config=cast(InstanceConfig, cast(Any, object())))


class TestValidationDecision:
    def test_allowed_decision(self) -> None:
        decision = ValidationDecision(allowed=True)
        assert decision.allowed is True
        assert decision.failed_stage is None
        assert decision.reason_code is None
        assert decision.message is None

    def test_denied_decision(self) -> None:
        decision = ValidationDecision(
            allowed=False,
            failed_stage=ValidationStage.RISK,
            reason_code='RISK_LIMIT_BREACH',
            message='Total drawdown exceeds configured threshold',
        )
        assert decision.allowed is False
        assert decision.failed_stage == ValidationStage.RISK
        assert decision.reason_code == 'RISK_LIMIT_BREACH'
        assert decision.message == 'Total drawdown exceeds configured threshold'

    def test_allowed_with_failed_stage_rejected(self) -> None:
        with pytest.raises(ValueError, match='allowed=True must not set failed_stage'):
            ValidationDecision(allowed=True, failed_stage=ValidationStage.INTAKE)

    def test_denied_without_stage_rejected(self) -> None:
        with pytest.raises(ValueError, match='allowed=False requires failed_stage'):
            ValidationDecision(
                allowed=False,
                reason_code='X',
                message='x',
            )

    def test_denied_without_reason_code_rejected(self) -> None:
        with pytest.raises(ValueError, match='non-empty reason_code'):
            ValidationDecision(
                allowed=False,
                failed_stage=ValidationStage.PRICE,
                reason_code='',
                message='x',
            )

    def test_denied_without_message_rejected(self) -> None:
        with pytest.raises(ValueError, match='non-empty message'):
            ValidationDecision(
                allowed=False,
                failed_stage=ValidationStage.GATEWAY,
                reason_code='GATEWAY_UNAVAILABLE',
                message=' ',
            )
