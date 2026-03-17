'''Verify Position dataclass creation and validation.'''

from __future__ import annotations

from decimal import Decimal

import pytest

from nexus.core.domain.enums import OrderSide
from nexus.core.domain.position import Position


def test_valid_creation() -> None:
    '''Verify a valid position is created without error.'''

    pos = Position(
        trade_id='t1',
        strategy_id='momentum',
        symbol='BTCUSDT',
        side=OrderSide.BUY,
        size=Decimal('0.5'),
        entry_price=Decimal('50000'),
    )
    assert pos.trade_id == 't1'
    assert pos.strategy_id == 'momentum'
    assert pos.symbol == 'BTCUSDT'
    assert pos.side == OrderSide.BUY
    assert pos.size == Decimal('0.5')
    assert pos.entry_price == Decimal('50000')
    assert pos.unrealized_pnl == Decimal(0)
    assert pos.pending_exit == Decimal(0)


def test_is_closed() -> None:
    '''Verify is_closed returns True when size is zero.'''

    pos = Position(
        trade_id='t1',
        strategy_id='momentum',
        symbol='BTCUSDT',
        side=OrderSide.BUY,
        size=Decimal(0),
        entry_price=Decimal('50000'),
    )
    assert pos.is_closed is True


def test_is_not_closed() -> None:
    '''Verify is_closed returns False when size is positive.'''

    pos = Position(
        trade_id='t1',
        strategy_id='momentum',
        symbol='BTCUSDT',
        side=OrderSide.BUY,
        size=Decimal('0.1'),
        entry_price=Decimal('50000'),
    )
    assert pos.is_closed is False


def test_empty_trade_id_rejected() -> None:
    '''Verify empty trade_id raises ValueError.'''

    with pytest.raises(ValueError, match='trade_id'):
        Position(
            trade_id='',
            strategy_id='momentum',
            symbol='BTCUSDT',
            side=OrderSide.BUY,
            size=Decimal('0.5'),
            entry_price=Decimal('50000'),
        )


def test_empty_strategy_id_rejected() -> None:
    '''Verify empty strategy_id raises ValueError.'''

    with pytest.raises(ValueError, match='strategy_id'):
        Position(
            trade_id='t1',
            strategy_id='',
            symbol='BTCUSDT',
            side=OrderSide.BUY,
            size=Decimal('0.5'),
            entry_price=Decimal('50000'),
        )


def test_negative_size_rejected() -> None:
    '''Verify negative size raises ValueError.'''

    with pytest.raises(ValueError, match='size'):
        Position(
            trade_id='t1',
            strategy_id='momentum',
            symbol='BTCUSDT',
            side=OrderSide.BUY,
            size=Decimal('-1'),
            entry_price=Decimal('50000'),
        )


def test_zero_entry_price_rejected() -> None:
    '''Verify zero entry_price raises ValueError.'''

    with pytest.raises(ValueError, match='entry_price'):
        Position(
            trade_id='t1',
            strategy_id='momentum',
            symbol='BTCUSDT',
            side=OrderSide.BUY,
            size=Decimal('0.5'),
            entry_price=Decimal(0),
        )


def test_negative_pending_exit_rejected() -> None:
    '''Verify negative pending_exit raises ValueError.'''

    with pytest.raises(ValueError, match='pending_exit'):
        Position(
            trade_id='t1',
            strategy_id='momentum',
            symbol='BTCUSDT',
            side=OrderSide.BUY,
            size=Decimal('0.5'),
            entry_price=Decimal('50000'),
            pending_exit=Decimal('-1'),
        )


def test_mutable() -> None:
    '''Verify position fields can be mutated.'''

    pos = Position(
        trade_id='t1',
        strategy_id='momentum',
        symbol='BTCUSDT',
        side=OrderSide.BUY,
        size=Decimal('0.5'),
        entry_price=Decimal('50000'),
    )
    pos.size = Decimal('0.3')
    assert pos.size == Decimal('0.3')
