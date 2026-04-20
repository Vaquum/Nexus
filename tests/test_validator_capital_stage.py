from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import cast
from unittest.mock import Mock

import pytest

from nexus.core.capital_controller.capital_controller import CapitalController
from nexus.core.capital_controller.reservation import ReservationResult
from nexus.core.domain.instance_state import InstanceState
from nexus.core.validator import (
    ValidationAction,
    ValidationRequestContext,
    ValidationStage,
    validate_capital_stage,
)
from nexus.instance_config import InstanceConfig


def _make_context(
    *,
    strategy_id: str = 'strat_a',
    action: ValidationAction = ValidationAction.ENTER,
    order_notional: Decimal = Decimal('100'),
    current_order_notional: Decimal | None = None,
    estimated_fees: Decimal = Decimal('1'),
    strategy_budget: Decimal = Decimal('5000'),
) -> ValidationRequestContext:
    config = InstanceConfig(
        account_id='acc_001',
        venue='binance_spot',
    )
    state = InstanceState.fresh(Decimal('10000'))
    return ValidationRequestContext(
        strategy_id=strategy_id,
        action=action,
        command_id='cmd_cap_1',
        order_notional=order_notional,
        current_order_notional=current_order_notional,
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

    def test_modify_uses_positive_notional_delta_for_capital_check(self) -> None:
        context = _make_context(
            action=ValidationAction.MODIFY,
            order_notional=Decimal('100'),
            current_order_notional=Decimal('80'),
            strategy_budget=Decimal('30'),
        )
        capital_controller = CapitalController(context.state.capital)

        decision = validate_capital_stage(context, capital_controller)

        assert decision.allowed is True
        assert decision.reservation is not None

    def test_modify_non_increasing_notional_skips_capital_reservation(self) -> None:
        context = _make_context(
            action=ValidationAction.MODIFY,
            order_notional=Decimal('80'),
            current_order_notional=Decimal('100'),
            strategy_budget=Decimal('0'),
        )
        capital_controller = Mock()

        decision = validate_capital_stage(
            context,
            cast(CapitalController, capital_controller),
        )

        assert decision.allowed is True
        assert decision.reservation is None
        capital_controller.check_and_reserve.assert_not_called()

    def test_modify_same_notional_skips_capital_reservation(self) -> None:
        context = _make_context(
            action=ValidationAction.MODIFY,
            order_notional=Decimal('100'),
            current_order_notional=Decimal('100'),
            strategy_budget=Decimal('0'),
        )
        capital_controller = Mock()

        decision = validate_capital_stage(
            context,
            cast(CapitalController, capital_controller),
        )

        assert decision.allowed is True
        assert decision.reservation is None
        capital_controller.check_and_reserve.assert_not_called()

    def test_modify_uses_delta_estimated_fees_for_capital_check(self) -> None:
        context = _make_context(
            action=ValidationAction.MODIFY,
            order_notional=Decimal('120'),
            current_order_notional=Decimal('100'),
            estimated_fees=Decimal('6'),
            strategy_budget=Decimal('5000'),
        )
        capital_controller = Mock()
        capital_controller.check_and_reserve.return_value = ReservationResult(
            granted=False,
            denial_reason='denied',
        )

        _ = validate_capital_stage(context, cast(CapitalController, capital_controller))

        capital_controller.check_and_reserve.assert_called_once_with(
            strategy_id='strat_a',
            order_notional=Decimal('20'),
            estimated_fees=Decimal('1'),
            strategy_budget=Decimal('5000'),
        )

    @pytest.mark.parametrize('invalid_ttl', [True, 0, -1, 1.5])
    def test_rejects_invalid_ttl_seconds(self, invalid_ttl: object) -> None:
        context = _make_context()
        capital_controller = CapitalController(context.state.capital)

        with pytest.raises(ValueError, match='ttl_seconds'):
            validate_capital_stage(
                context,
                capital_controller,
                ttl_seconds=cast(int, invalid_ttl),
            )
