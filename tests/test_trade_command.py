'''Verify TradeCommand creation and validation.'''

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import cast

import pytest

from nexus.core.domain.enums import OrderSide
from nexus.core.stp_mode import STPMode
from nexus.infrastructure.praxis_connector.trade_command import TradeCommand
from nexus.infrastructure.praxis_connector.trade_command_type import TradeCommandType


def _now() -> datetime:
    return datetime.now(timezone.utc)


def test_valid_new_order_creation() -> None:
    cmd = TradeCommand(
        command_id='cmd_001',
        command_type=TradeCommandType.NEW_ORDER,
        account_id='acc_001',
        venue='binance_spot',
        symbol='BTCUSDT',
        notional=Decimal('1000'),
        created_at=_now(),
        side=OrderSide.BUY,
        size=Decimal('0.01'),
        stp_mode=STPMode.CANCEL_TAKER,
        reservation_id='res_001',
    )
    assert cmd.command_type == TradeCommandType.NEW_ORDER
    assert cmd.side == OrderSide.BUY
    assert cmd.stp_mode == STPMode.CANCEL_TAKER


def test_valid_new_order_with_trade_id() -> None:
    cmd = TradeCommand(
        command_id='cmd_002',
        command_type=TradeCommandType.NEW_ORDER,
        account_id='acc_001',
        venue='binance_spot',
        symbol='BTCUSDT',
        notional=Decimal('1000'),
        created_at=_now(),
        side=OrderSide.SELL,
        size=Decimal('0.01'),
        stp_mode=STPMode.CANCEL_MAKER,
        trade_id='trade_001',
    )
    assert cmd.trade_id == 'trade_001'


def test_valid_amend_order_creation() -> None:
    cmd = TradeCommand(
        command_id='cmd_003',
        command_type=TradeCommandType.AMEND_ORDER,
        account_id='acc_001',
        venue='binance_spot',
        symbol='BTCUSDT',
        notional=Decimal('1500'),
        created_at=_now(),
    )
    assert cmd.command_type == TradeCommandType.AMEND_ORDER
    assert cmd.stp_mode is None


def test_valid_cancel_order_creation() -> None:
    cmd = TradeCommand(
        command_id='cmd_004',
        command_type=TradeCommandType.CANCEL_ORDER,
        account_id='acc_001',
        venue='binance_spot',
        symbol='BTCUSDT',
        notional=Decimal('0'),
        created_at=_now(),
    )
    assert cmd.command_type == TradeCommandType.CANCEL_ORDER
    assert cmd.stp_mode is None


def test_frozen() -> None:
    cmd = TradeCommand(
        command_id='cmd_001',
        command_type=TradeCommandType.NEW_ORDER,
        account_id='acc_001',
        venue='binance_spot',
        symbol='BTCUSDT',
        notional=Decimal('1000'),
        created_at=_now(),
        side=OrderSide.BUY,
        size=Decimal('0.01'),
        stp_mode=STPMode.CANCEL_TAKER,
    )
    with pytest.raises(AttributeError):
        cmd.command_id = 'cmd_002'  # type: ignore[misc]


def test_empty_command_id_rejected() -> None:
    with pytest.raises(ValueError, match='command_id'):
        TradeCommand(
            command_id='',
            command_type=TradeCommandType.NEW_ORDER,
            account_id='acc_001',
            venue='binance_spot',
            symbol='BTCUSDT',
            notional=Decimal('1000'),
            created_at=_now(),
            side=OrderSide.BUY,
            size=Decimal('0.01'),
            stp_mode=STPMode.CANCEL_TAKER,
        )


def test_invalid_command_type_rejected() -> None:
    with pytest.raises(ValueError, match='command_type'):
        TradeCommand(
            command_id='cmd_001',
            command_type=cast(TradeCommandType, cast(object, 'NEW_ORDER')),
            account_id='acc_001',
            venue='binance_spot',
            symbol='BTCUSDT',
            notional=Decimal('1000'),
            created_at=_now(),
            side=OrderSide.BUY,
            size=Decimal('0.01'),
            stp_mode=STPMode.CANCEL_TAKER,
        )


def test_empty_account_id_rejected() -> None:
    with pytest.raises(ValueError, match='account_id'):
        TradeCommand(
            command_id='cmd_001',
            command_type=TradeCommandType.NEW_ORDER,
            account_id='',
            venue='binance_spot',
            symbol='BTCUSDT',
            notional=Decimal('1000'),
            created_at=_now(),
            side=OrderSide.BUY,
            size=Decimal('0.01'),
            stp_mode=STPMode.CANCEL_TAKER,
        )


def test_empty_venue_rejected() -> None:
    with pytest.raises(ValueError, match='venue'):
        TradeCommand(
            command_id='cmd_001',
            command_type=TradeCommandType.NEW_ORDER,
            account_id='acc_001',
            venue='',
            symbol='BTCUSDT',
            notional=Decimal('1000'),
            created_at=_now(),
            side=OrderSide.BUY,
            size=Decimal('0.01'),
            stp_mode=STPMode.CANCEL_TAKER,
        )


def test_empty_symbol_rejected() -> None:
    with pytest.raises(ValueError, match='symbol'):
        TradeCommand(
            command_id='cmd_001',
            command_type=TradeCommandType.NEW_ORDER,
            account_id='acc_001',
            venue='binance_spot',
            symbol='',
            notional=Decimal('1000'),
            created_at=_now(),
            side=OrderSide.BUY,
            size=Decimal('0.01'),
            stp_mode=STPMode.CANCEL_TAKER,
        )


def test_negative_notional_rejected() -> None:
    with pytest.raises(ValueError, match='notional'):
        TradeCommand(
            command_id='cmd_001',
            command_type=TradeCommandType.NEW_ORDER,
            account_id='acc_001',
            venue='binance_spot',
            symbol='BTCUSDT',
            notional=Decimal('-100'),
            created_at=_now(),
            side=OrderSide.BUY,
            size=Decimal('0.01'),
            stp_mode=STPMode.CANCEL_TAKER,
        )


def test_nan_notional_rejected() -> None:
    with pytest.raises(ValueError, match='notional'):
        TradeCommand(
            command_id='cmd_001',
            command_type=TradeCommandType.NEW_ORDER,
            account_id='acc_001',
            venue='binance_spot',
            symbol='BTCUSDT',
            notional=Decimal('NaN'),
            created_at=_now(),
            side=OrderSide.BUY,
            size=Decimal('0.01'),
            stp_mode=STPMode.CANCEL_TAKER,
        )


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValueError, match='created_at'):
        TradeCommand(
            command_id='cmd_001',
            command_type=TradeCommandType.NEW_ORDER,
            account_id='acc_001',
            venue='binance_spot',
            symbol='BTCUSDT',
            notional=Decimal('1000'),
            created_at=datetime.now(),
            side=OrderSide.BUY,
            size=Decimal('0.01'),
            stp_mode=STPMode.CANCEL_TAKER,
        )


def test_invalid_side_rejected() -> None:
    with pytest.raises(ValueError, match='side'):
        TradeCommand(
            command_id='cmd_001',
            command_type=TradeCommandType.NEW_ORDER,
            account_id='acc_001',
            venue='binance_spot',
            symbol='BTCUSDT',
            notional=Decimal('1000'),
            created_at=_now(),
            side=cast(OrderSide, cast(object, 'BUY')),
            size=Decimal('0.01'),
            stp_mode=STPMode.CANCEL_TAKER,
        )


def test_zero_size_rejected() -> None:
    with pytest.raises(ValueError, match='size'):
        TradeCommand(
            command_id='cmd_001',
            command_type=TradeCommandType.NEW_ORDER,
            account_id='acc_001',
            venue='binance_spot',
            symbol='BTCUSDT',
            notional=Decimal('1000'),
            created_at=_now(),
            side=OrderSide.BUY,
            size=Decimal('0'),
            stp_mode=STPMode.CANCEL_TAKER,
        )


def test_negative_size_rejected() -> None:
    with pytest.raises(ValueError, match='size'):
        TradeCommand(
            command_id='cmd_001',
            command_type=TradeCommandType.NEW_ORDER,
            account_id='acc_001',
            venue='binance_spot',
            symbol='BTCUSDT',
            notional=Decimal('1000'),
            created_at=_now(),
            side=OrderSide.BUY,
            size=Decimal('-0.01'),
            stp_mode=STPMode.CANCEL_TAKER,
        )


def test_invalid_stp_mode_rejected() -> None:
    with pytest.raises(ValueError, match='stp_mode'):
        TradeCommand(
            command_id='cmd_001',
            command_type=TradeCommandType.NEW_ORDER,
            account_id='acc_001',
            venue='binance_spot',
            symbol='BTCUSDT',
            notional=Decimal('1000'),
            created_at=_now(),
            side=OrderSide.BUY,
            size=Decimal('0.01'),
            stp_mode=cast(STPMode, cast(object, 'CANCEL_TAKER')),
        )


def test_empty_trade_id_rejected() -> None:
    with pytest.raises(ValueError, match='trade_id'):
        TradeCommand(
            command_id='cmd_001',
            command_type=TradeCommandType.NEW_ORDER,
            account_id='acc_001',
            venue='binance_spot',
            symbol='BTCUSDT',
            notional=Decimal('1000'),
            created_at=_now(),
            side=OrderSide.BUY,
            size=Decimal('0.01'),
            stp_mode=STPMode.CANCEL_TAKER,
            trade_id='   ',
        )


def test_empty_reservation_id_rejected() -> None:
    with pytest.raises(ValueError, match='reservation_id'):
        TradeCommand(
            command_id='cmd_001',
            command_type=TradeCommandType.NEW_ORDER,
            account_id='acc_001',
            venue='binance_spot',
            symbol='BTCUSDT',
            notional=Decimal('1000'),
            created_at=_now(),
            side=OrderSide.BUY,
            size=Decimal('0.01'),
            stp_mode=STPMode.CANCEL_TAKER,
            reservation_id='',
        )


def test_new_order_without_side_rejected() -> None:
    with pytest.raises(ValueError, match='NEW_ORDER requires side'):
        TradeCommand(
            command_id='cmd_001',
            command_type=TradeCommandType.NEW_ORDER,
            account_id='acc_001',
            venue='binance_spot',
            symbol='BTCUSDT',
            notional=Decimal('1000'),
            created_at=_now(),
            size=Decimal('0.01'),
            stp_mode=STPMode.CANCEL_TAKER,
        )


def test_new_order_without_size_rejected() -> None:
    with pytest.raises(ValueError, match='NEW_ORDER requires size'):
        TradeCommand(
            command_id='cmd_001',
            command_type=TradeCommandType.NEW_ORDER,
            account_id='acc_001',
            venue='binance_spot',
            symbol='BTCUSDT',
            notional=Decimal('1000'),
            created_at=_now(),
            side=OrderSide.BUY,
            stp_mode=STPMode.CANCEL_TAKER,
        )


def test_new_order_without_stp_mode_rejected() -> None:
    with pytest.raises(ValueError, match='NEW_ORDER requires stp_mode'):
        TradeCommand(
            command_id='cmd_001',
            command_type=TradeCommandType.NEW_ORDER,
            account_id='acc_001',
            venue='binance_spot',
            symbol='BTCUSDT',
            notional=Decimal('1000'),
            created_at=_now(),
            side=OrderSide.BUY,
            size=Decimal('0.01'),
        )


def test_amend_order_with_stp_mode_rejected() -> None:
    with pytest.raises(ValueError, match='AMEND_ORDER must not have stp_mode'):
        TradeCommand(
            command_id='cmd_001',
            command_type=TradeCommandType.AMEND_ORDER,
            account_id='acc_001',
            venue='binance_spot',
            symbol='BTCUSDT',
            notional=Decimal('1000'),
            created_at=_now(),
            stp_mode=STPMode.CANCEL_TAKER,
        )


def test_cancel_order_with_stp_mode_rejected() -> None:
    with pytest.raises(ValueError, match='CANCEL_ORDER must not have stp_mode'):
        TradeCommand(
            command_id='cmd_001',
            command_type=TradeCommandType.CANCEL_ORDER,
            account_id='acc_001',
            venue='binance_spot',
            symbol='BTCUSDT',
            notional=Decimal('0'),
            created_at=_now(),
            stp_mode=STPMode.CANCEL_TAKER,
        )


def test_amend_order_with_side_rejected() -> None:
    with pytest.raises(ValueError, match='AMEND_ORDER must not have side'):
        TradeCommand(
            command_id='cmd_001',
            command_type=TradeCommandType.AMEND_ORDER,
            account_id='acc_001',
            venue='binance_spot',
            symbol='BTCUSDT',
            notional=Decimal('1500'),
            created_at=_now(),
            side=OrderSide.BUY,
        )


def test_amend_order_with_size_rejected() -> None:
    with pytest.raises(ValueError, match='AMEND_ORDER must not have size'):
        TradeCommand(
            command_id='cmd_001',
            command_type=TradeCommandType.AMEND_ORDER,
            account_id='acc_001',
            venue='binance_spot',
            symbol='BTCUSDT',
            notional=Decimal('1500'),
            created_at=_now(),
            size=Decimal('0.02'),
        )


def test_cancel_order_with_side_rejected() -> None:
    with pytest.raises(ValueError, match='CANCEL_ORDER must not have side'):
        TradeCommand(
            command_id='cmd_001',
            command_type=TradeCommandType.CANCEL_ORDER,
            account_id='acc_001',
            venue='binance_spot',
            symbol='BTCUSDT',
            notional=Decimal('0'),
            created_at=_now(),
            side=OrderSide.SELL,
        )


def test_cancel_order_with_size_rejected() -> None:
    with pytest.raises(ValueError, match='CANCEL_ORDER must not have size'):
        TradeCommand(
            command_id='cmd_001',
            command_type=TradeCommandType.CANCEL_ORDER,
            account_id='acc_001',
            venue='binance_spot',
            symbol='BTCUSDT',
            notional=Decimal('0'),
            created_at=_now(),
            size=Decimal('0.01'),
        )


def test_execution_params_defensive_copy() -> None:
    '''TradeCommand stores a defensive copy of execution_params.'''

    source: dict[str, object] = {'slippage_bps': 10}
    cmd = TradeCommand(
        command_id='cmd_001',
        command_type=TradeCommandType.NEW_ORDER,
        account_id='acc_001',
        venue='binance_spot',
        symbol='BTCUSDT',
        notional=Decimal('1000'),
        created_at=_now(),
        side=OrderSide.BUY,
        size=Decimal('0.01'),
        stp_mode=STPMode.CANCEL_TAKER,
        execution_params=source,
    )

    source['slippage_bps'] = 999

    assert cmd.execution_params is not None
    assert cmd.execution_params['slippage_bps'] == 10


def test_execution_params_read_only() -> None:
    '''Stored execution_params is a read-only mapping.'''

    cmd = TradeCommand(
        command_id='cmd_001',
        command_type=TradeCommandType.NEW_ORDER,
        account_id='acc_001',
        venue='binance_spot',
        symbol='BTCUSDT',
        notional=Decimal('1000'),
        created_at=_now(),
        side=OrderSide.BUY,
        size=Decimal('0.01'),
        stp_mode=STPMode.CANCEL_TAKER,
        execution_params={'slippage_bps': 10},
    )

    assert cmd.execution_params is not None
    with pytest.raises(TypeError):
        cmd.execution_params['slippage_bps'] = 999  # type: ignore[index]


def test_new_order_accepts_strategy_id() -> None:
    cmd = TradeCommand(
        command_id='cmd_010',
        command_type=TradeCommandType.NEW_ORDER,
        account_id='acc_001',
        venue='binance_spot',
        symbol='BTCUSDT',
        notional=Decimal('1000'),
        created_at=_now(),
        side=OrderSide.BUY,
        size=Decimal('0.01'),
        stp_mode=STPMode.CANCEL_TAKER,
        strategy_id='momentum',
    )
    assert cmd.strategy_id == 'momentum'


def test_new_order_strategy_id_optional() -> None:
    cmd = TradeCommand(
        command_id='cmd_011',
        command_type=TradeCommandType.NEW_ORDER,
        account_id='acc_001',
        venue='binance_spot',
        symbol='BTCUSDT',
        notional=Decimal('1000'),
        created_at=_now(),
        side=OrderSide.BUY,
        size=Decimal('0.01'),
        stp_mode=STPMode.CANCEL_TAKER,
    )
    assert cmd.strategy_id is None


def test_empty_strategy_id_rejected() -> None:
    with pytest.raises(ValueError, match='strategy_id'):
        TradeCommand(
            command_id='cmd_012',
            command_type=TradeCommandType.NEW_ORDER,
            account_id='acc_001',
            venue='binance_spot',
            symbol='BTCUSDT',
            notional=Decimal('1000'),
            created_at=_now(),
            side=OrderSide.BUY,
            size=Decimal('0.01'),
            stp_mode=STPMode.CANCEL_TAKER,
            strategy_id='   ',
        )


def test_amend_order_with_strategy_id_rejected() -> None:
    with pytest.raises(ValueError, match='AMEND_ORDER must not have strategy_id'):
        TradeCommand(
            command_id='cmd_013',
            command_type=TradeCommandType.AMEND_ORDER,
            account_id='acc_001',
            venue='binance_spot',
            symbol='BTCUSDT',
            notional=Decimal('1500'),
            created_at=_now(),
            strategy_id='momentum',
        )


def test_cancel_order_with_strategy_id_rejected() -> None:
    with pytest.raises(ValueError, match='CANCEL_ORDER must not have strategy_id'):
        TradeCommand(
            command_id='cmd_014',
            command_type=TradeCommandType.CANCEL_ORDER,
            account_id='acc_001',
            venue='binance_spot',
            symbol='BTCUSDT',
            notional=Decimal('0'),
            created_at=_now(),
            strategy_id='momentum',
        )


def test_new_order_with_quote_qty_succeeds() -> None:
    cmd = TradeCommand(
        command_id='cmd_015',
        command_type=TradeCommandType.NEW_ORDER,
        account_id='acc_001',
        venue='binance_spot',
        symbol='BTCUSDT',
        notional=Decimal('100'),
        created_at=_now(),
        side=OrderSide.BUY,
        quote_qty=Decimal('100'),
        stp_mode=STPMode.CANCEL_TAKER,
    )

    assert cmd.quote_qty == Decimal('100')
    assert cmd.size is None


def test_new_order_with_size_and_quote_qty_rejected() -> None:
    with pytest.raises(ValueError, match='NEW_ORDER requires exactly one of size or quote_qty'):
        TradeCommand(
            command_id='cmd_016',
            command_type=TradeCommandType.NEW_ORDER,
            account_id='acc_001',
            venue='binance_spot',
            symbol='BTCUSDT',
            notional=Decimal('100'),
            created_at=_now(),
            side=OrderSide.BUY,
            size=Decimal('0.01'),
            quote_qty=Decimal('100'),
            stp_mode=STPMode.CANCEL_TAKER,
        )


def test_new_order_without_size_or_quote_qty_rejected() -> None:
    with pytest.raises(ValueError, match='NEW_ORDER requires size or quote_qty'):
        TradeCommand(
            command_id='cmd_017',
            command_type=TradeCommandType.NEW_ORDER,
            account_id='acc_001',
            venue='binance_spot',
            symbol='BTCUSDT',
            notional=Decimal('100'),
            created_at=_now(),
            side=OrderSide.BUY,
            stp_mode=STPMode.CANCEL_TAKER,
        )


def test_amend_order_with_quote_qty_rejected() -> None:
    with pytest.raises(ValueError, match='AMEND_ORDER must not have quote_qty'):
        TradeCommand(
            command_id='cmd_018',
            command_type=TradeCommandType.AMEND_ORDER,
            account_id='acc_001',
            venue='binance_spot',
            symbol='BTCUSDT',
            notional=Decimal('100'),
            created_at=_now(),
            quote_qty=Decimal('100'),
        )


def test_cancel_order_with_quote_qty_rejected() -> None:
    with pytest.raises(ValueError, match='CANCEL_ORDER must not have quote_qty'):
        TradeCommand(
            command_id='cmd_019',
            command_type=TradeCommandType.CANCEL_ORDER,
            account_id='acc_001',
            venue='binance_spot',
            symbol='BTCUSDT',
            notional=Decimal('0'),
            created_at=_now(),
            quote_qty=Decimal('100'),
        )


def test_quote_qty_must_be_positive_decimal() -> None:
    with pytest.raises(ValueError, match='quote_qty must be a finite positive Decimal'):
        TradeCommand(
            command_id='cmd_020',
            command_type=TradeCommandType.NEW_ORDER,
            account_id='acc_001',
            venue='binance_spot',
            symbol='BTCUSDT',
            notional=Decimal('100'),
            created_at=_now(),
            side=OrderSide.BUY,
            quote_qty=Decimal('0'),
            stp_mode=STPMode.CANCEL_TAKER,
        )

