'''Verify translate_to_trade_command function.'''

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from nexus.core.capital_controller.reservation import Reservation
from nexus.core.domain.enums import OrderSide
from nexus.core.domain.instance_state import InstanceState
from nexus.core.domain.order_types import ExecutionMode, MakerPreference, OrderType
from nexus.core.stp_mode import STPMode
from nexus.core.validator.pipeline_models import (
    ValidationAction,
    ValidationDecision,
    ValidationRequestContext,
    ValidationStage,
)
from nexus.infrastructure.praxis_connector.trade_command_type import TradeCommandType
from nexus.infrastructure.praxis_connector.translate import translate_to_trade_command
from nexus.instance_config import InstanceConfig
from nexus.strategy.action import Action, ActionType


def _config(stp_mode: STPMode = STPMode.CANCEL_TAKER) -> InstanceConfig:
    return InstanceConfig(
        account_id='acc_001',
        venue='binance_spot',
        allocated_capital=Decimal('10000'),
        stp_mode=stp_mode,
    )


def _state() -> InstanceState:
    return InstanceState.from_config(_config())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _reservation() -> Reservation:
    now = _now()
    return Reservation(
        reservation_id='res_001',
        strategy_id='strat_001',
        notional=Decimal('1000'),
        estimated_fees=Decimal('1'),
        created_at=now,
        expires_at=now + timedelta(minutes=1),
    )


def _enter_action() -> Action:
    return Action(
        action_type=ActionType.ENTER,
        direction=OrderSide.BUY,
        size=Decimal('0.01'),
        execution_mode=ExecutionMode.SINGLE_SHOT,
        order_type=OrderType.MARKET,
        deadline=300,
        maker_preference=MakerPreference.NO_PREFERENCE,
        reference_price=Decimal('100000'),
        execution_params={'foo': 'bar'},
    )


def _exit_action() -> Action:
    return Action(
        action_type=ActionType.EXIT,
        trade_id='trade_001',
        size=Decimal('0.01'),
    )


def _modify_action() -> Action:
    return Action(action_type=ActionType.MODIFY, command_id='cmd_003')


def _abort_action() -> Action:
    return Action(action_type=ActionType.ABORT, command_id='cmd_004')


def _cancel_action() -> Action:
    return Action(action_type=ActionType.ABORT, command_id='cmd_005')


def _enter_context() -> ValidationRequestContext:
    return ValidationRequestContext(
        strategy_id='strat_001',
        action=ValidationAction.ENTER,
        symbol='BTCUSDT',
        order_side=OrderSide.BUY,
        order_size=Decimal('0.01'),
        command_id='cmd_001',
        order_notional=Decimal('1000'),
        estimated_fees=Decimal('1'),
        strategy_budget=Decimal('5000'),
        state=_state(),
        config=_config(),
    )


def _exit_context() -> ValidationRequestContext:
    return ValidationRequestContext(
        strategy_id='strat_001',
        action=ValidationAction.EXIT,
        symbol='BTCUSDT',
        order_side=OrderSide.SELL,
        order_size=Decimal('0.01'),
        command_id='cmd_002',
        trade_id='trade_001',
        order_notional=Decimal('1000'),
        estimated_fees=Decimal('1'),
        strategy_budget=Decimal('5000'),
        state=_state(),
        config=_config(),
    )


def _modify_context() -> ValidationRequestContext:
    return ValidationRequestContext(
        strategy_id='strat_001',
        action=ValidationAction.MODIFY,
        symbol='BTCUSDT',
        command_id='cmd_003',
        order_notional=Decimal('1500'),
        current_order_notional=Decimal('1000'),
        estimated_fees=Decimal('1'),
        strategy_budget=Decimal('5000'),
        state=_state(),
        config=_config(),
    )


def _abort_context() -> ValidationRequestContext:
    return ValidationRequestContext(
        strategy_id='strat_001',
        action=ValidationAction.ABORT,
        symbol='BTCUSDT',
        command_id='cmd_004',
        order_notional=Decimal('0'),
        estimated_fees=Decimal('0'),
        strategy_budget=Decimal('5000'),
        state=_state(),
        config=_config(),
    )


def _cancel_context() -> ValidationRequestContext:
    return ValidationRequestContext(
        strategy_id='strat_001',
        action=ValidationAction.CANCEL,
        symbol='BTCUSDT',
        command_id='cmd_005',
        order_notional=Decimal('0'),
        estimated_fees=Decimal('0'),
        strategy_budget=Decimal('5000'),
        state=_state(),
        config=_config(),
    )


def test_enter_translation() -> None:
    action = _enter_action()
    context = _enter_context()
    decision = ValidationDecision(allowed=True, reservation=_reservation())
    config = _config()
    now = _now()

    cmd = translate_to_trade_command(action, context, decision, config, now)

    assert cmd.command_type == TradeCommandType.NEW_ORDER
    assert cmd.command_id == 'cmd_001'
    assert cmd.account_id == 'acc_001'
    assert cmd.venue == 'binance_spot'
    assert cmd.symbol == 'BTCUSDT'
    assert cmd.side == OrderSide.BUY
    assert cmd.size == Decimal('0.01')
    assert cmd.notional == Decimal('1000')
    assert cmd.stp_mode == STPMode.CANCEL_TAKER
    assert cmd.reservation_id == 'res_001'
    assert cmd.created_at == now
    assert cmd.execution_mode == ExecutionMode.SINGLE_SHOT
    assert cmd.order_type == OrderType.MARKET
    assert cmd.deadline == 300
    assert cmd.maker_preference == MakerPreference.NO_PREFERENCE
    assert cmd.reference_price == Decimal('100000')
    assert cmd.execution_params == {'foo': 'bar'}


def test_exit_translation() -> None:
    action = _exit_action()
    context = _exit_context()
    decision = ValidationDecision(allowed=True)
    config = _config(stp_mode=STPMode.CANCEL_MAKER)
    now = _now()

    cmd = translate_to_trade_command(action, context, decision, config, now)

    assert cmd.command_type == TradeCommandType.NEW_ORDER
    assert cmd.side == OrderSide.SELL
    assert cmd.stp_mode == STPMode.CANCEL_MAKER
    assert cmd.trade_id == 'trade_001'
    assert cmd.reservation_id is None


def test_modify_translation() -> None:
    action = _modify_action()
    context = _modify_context()
    decision = ValidationDecision(allowed=True)
    config = _config()
    now = _now()

    cmd = translate_to_trade_command(action, context, decision, config, now)

    assert cmd.command_type == TradeCommandType.AMEND_ORDER
    assert cmd.command_id == 'cmd_003'
    assert cmd.notional == Decimal('1500')
    assert cmd.side is None
    assert cmd.size is None
    assert cmd.stp_mode is None
    assert cmd.execution_mode is None
    assert cmd.order_type is None
    assert cmd.execution_params is None
    assert cmd.deadline is None
    assert cmd.maker_preference is None
    assert cmd.reference_price is None


def test_abort_translation() -> None:
    action = _abort_action()
    context = _abort_context()
    decision = ValidationDecision(allowed=True)
    config = _config()
    now = _now()

    cmd = translate_to_trade_command(action, context, decision, config, now)

    assert cmd.command_type == TradeCommandType.CANCEL_ORDER
    assert cmd.command_id == 'cmd_004'
    assert cmd.stp_mode is None
    assert cmd.execution_mode is None
    assert cmd.order_type is None
    assert cmd.maker_preference is None


def test_cancel_translation() -> None:
    action = _cancel_action()
    context = _cancel_context()
    decision = ValidationDecision(allowed=True)
    config = _config()
    now = _now()

    cmd = translate_to_trade_command(action, context, decision, config, now)

    assert cmd.command_type == TradeCommandType.CANCEL_ORDER
    assert cmd.command_id == 'cmd_005'
    assert cmd.stp_mode is None


def test_naive_datetime_rejected() -> None:
    action = _enter_action()
    context = _enter_context()
    decision = ValidationDecision(allowed=True, reservation=_reservation())
    config = _config()
    now = datetime.now()

    with pytest.raises(ValueError, match='requires UTC'):
        translate_to_trade_command(action, context, decision, config, now)


def test_non_utc_datetime_rejected() -> None:
    action = _enter_action()
    context = _enter_context()
    decision = ValidationDecision(allowed=True, reservation=_reservation())
    config = _config()
    non_utc = datetime(2026, 3, 25, 12, 0, 0, tzinfo=timezone(timedelta(hours=5)))

    with pytest.raises(ValueError, match='requires UTC'):
        translate_to_trade_command(action, context, decision, config, non_utc)


def test_deterministic_output() -> None:
    action = _enter_action()
    context = _enter_context()
    decision = ValidationDecision(allowed=True, reservation=_reservation())
    config = _config()
    now = datetime(2026, 3, 25, 12, 0, 0, tzinfo=timezone.utc)

    cmd1 = translate_to_trade_command(action, context, decision, config, now)
    cmd2 = translate_to_trade_command(action, context, decision, config, now)

    assert cmd1 == cmd2


def test_stp_mode_from_config() -> None:
    action = _enter_action()
    context = _enter_context()
    decision = ValidationDecision(allowed=True, reservation=_reservation())
    now = _now()

    for mode in STPMode:
        config = _config(stp_mode=mode)
        cmd = translate_to_trade_command(action, context, decision, config, now)
        assert cmd.stp_mode == mode


def test_denied_decision_rejected() -> None:
    action = _enter_action()
    context = _enter_context()
    decision = ValidationDecision(
        allowed=False,
        failed_stage=ValidationStage.CAPITAL,
        reason_code='CAPITAL_INSUFFICIENT',
        message='Insufficient capital',
    )
    config = _config()
    now = _now()

    with pytest.raises(ValueError, match='decision must be allowed'):
        translate_to_trade_command(action, context, decision, config, now)


def test_missing_command_id_rejected() -> None:
    action = _exit_action()
    context = ValidationRequestContext(
        strategy_id='strat_001',
        action=ValidationAction.EXIT,
        symbol='BTCUSDT',
        order_side=OrderSide.SELL,
        order_size=Decimal('0.01'),
        command_id=None,
        trade_id='trade_001',
        order_notional=Decimal('1000'),
        estimated_fees=Decimal('1'),
        strategy_budget=Decimal('5000'),
        state=_state(),
        config=_config(),
    )
    decision = ValidationDecision(allowed=True)
    config = _config()
    now = _now()

    with pytest.raises(ValueError, match='non-empty command_id'):
        translate_to_trade_command(action, context, decision, config, now)
