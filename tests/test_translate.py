'''Verify translate_to_trade_command function.'''

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from nexus.core.domain.enums import OrderSide
from nexus.core.domain.instance_state import InstanceState
from nexus.core.stp_mode import STPMode
from nexus.core.validator.pipeline_models import (
    ValidationAction,
    ValidationDecision,
    ValidationRequestContext,
)
from nexus.core.capital_controller.reservation import Reservation
from nexus.infrastructure.praxis_connector.trade_command_type import TradeCommandType
from nexus.infrastructure.praxis_connector.translate import translate_to_trade_command
from nexus.instance_config import InstanceConfig


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
        expires_at=now.replace(minute=now.minute + 1),
    )


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
    context = _enter_context()
    decision = ValidationDecision(allowed=True, reservation=_reservation())
    config = _config()
    now = _now()

    cmd = translate_to_trade_command(context, decision, config, now)

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


def test_exit_translation() -> None:
    context = _exit_context()
    decision = ValidationDecision(allowed=True)
    config = _config(stp_mode=STPMode.CANCEL_MAKER)
    now = _now()

    cmd = translate_to_trade_command(context, decision, config, now)

    assert cmd.command_type == TradeCommandType.NEW_ORDER
    assert cmd.side == OrderSide.SELL
    assert cmd.stp_mode == STPMode.CANCEL_MAKER
    assert cmd.trade_id == 'trade_001'
    assert cmd.reservation_id is None


def test_modify_translation() -> None:
    context = _modify_context()
    decision = ValidationDecision(allowed=True)
    config = _config()
    now = _now()

    cmd = translate_to_trade_command(context, decision, config, now)

    assert cmd.command_type == TradeCommandType.AMEND_ORDER
    assert cmd.command_id == 'cmd_003'
    assert cmd.notional == Decimal('1500')
    assert cmd.side is None
    assert cmd.size is None
    assert cmd.stp_mode is None


def test_abort_translation() -> None:
    context = _abort_context()
    decision = ValidationDecision(allowed=True)
    config = _config()
    now = _now()

    cmd = translate_to_trade_command(context, decision, config, now)

    assert cmd.command_type == TradeCommandType.CANCEL_ORDER
    assert cmd.command_id == 'cmd_004'
    assert cmd.stp_mode is None


def test_cancel_translation() -> None:
    context = _cancel_context()
    decision = ValidationDecision(allowed=True)
    config = _config()
    now = _now()

    cmd = translate_to_trade_command(context, decision, config, now)

    assert cmd.command_type == TradeCommandType.CANCEL_ORDER
    assert cmd.command_id == 'cmd_005'
    assert cmd.stp_mode is None


def test_naive_datetime_rejected() -> None:
    context = _enter_context()
    decision = ValidationDecision(allowed=True, reservation=_reservation())
    config = _config()
    now = datetime.now()

    with pytest.raises(ValueError, match='timezone-aware'):
        translate_to_trade_command(context, decision, config, now)


def test_deterministic_output() -> None:
    context = _enter_context()
    decision = ValidationDecision(allowed=True, reservation=_reservation())
    config = _config()
    now = datetime(2026, 3, 25, 12, 0, 0, tzinfo=timezone.utc)

    cmd1 = translate_to_trade_command(context, decision, config, now)
    cmd2 = translate_to_trade_command(context, decision, config, now)

    assert cmd1 == cmd2


def test_stp_mode_from_config() -> None:
    context = _enter_context()
    decision = ValidationDecision(allowed=True, reservation=_reservation())
    now = _now()

    for mode in STPMode:
        config = _config(stp_mode=mode)
        cmd = translate_to_trade_command(context, decision, config, now)
        assert cmd.stp_mode == mode
