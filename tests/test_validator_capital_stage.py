from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from nexus.core.capital_controller.capital_controller import CapitalController
from nexus.core.domain.instance_state import InstanceState
from nexus.core.validator import (
    ValidationRequestContext,
    ValidationStage,
    validate_capital_stage,
)
from nexus.instance_config import InstanceConfig


def _make_context(
    *,
    strategy_id: str = 'strat_a',
    order_notional: Decimal = Decimal('100'),
    estimated_fees: Decimal = Decimal('1'),
    strategy_budget: Decimal = Decimal('5000'),
) -> ValidationRequestContext:
    config = InstanceConfig(
        account_id='acc_001',
        venue='binance_spot',
        allocated_capital=Decimal('10000'),
    )
    state = InstanceState.from_config(config)
    return ValidationRequestContext(
        strategy_id=strategy_id,
        command_id='cmd_cap_1',
        order_notional=order_notional,
        estimated_fees=estimated_fees,
        strategy_budget=strategy_budget,
        state=state,
        config=config,
    )


class TestValidateCapitalStage:
    def test_returns_allowed_with_reservation_when_granted(self) -> None:
        context = _make_context()
        capital_controller = CapitalController(context.state.capital)

        decision = validate_capital_stage(context, capital_controller)

        assert decision.allowed is True
        assert decision.reservation is not None

    def test_returns_denied_when_reservation_rejected(self) -> None:
        context = _make_context(strategy_budget=Decimal('50'))
        capital_controller = CapitalController(context.state.capital)

        decision = validate_capital_stage(context, capital_controller)

        assert decision.allowed is False
        assert decision.failed_stage == ValidationStage.CAPITAL
        assert decision.reason_code == 'CAPITAL_RESERVATION_DENIED'
        assert decision.message is not None
        assert 'budget' in decision.message.lower()

    def test_forwards_ttl_seconds_to_capital_controller(self) -> None:
        context = _make_context()
        capital_controller = CapitalController(context.state.capital)

        decision = validate_capital_stage(
            context,
            capital_controller,
            ttl_seconds=7,
        )

        assert decision.allowed is True
        assert decision.reservation is not None
        assert (
            decision.reservation.expires_at - decision.reservation.created_at
            == timedelta(seconds=7)
        )
