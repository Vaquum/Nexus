'''Verify InstanceState composition and factory method.'''

from __future__ import annotations

from decimal import Decimal

import pytest

from nexus.core.domain.capital_state import CapitalState
from nexus.core.domain.enums import OperationalMode
from nexus.core.domain.instance_state import InstanceState
from nexus.instance_config import InstanceConfig


def test_direct_creation() -> None:
    '''Verify InstanceState can be created with explicit components.'''

    state = InstanceState(
        capital=CapitalState(capital_pool=Decimal('10000')),
    )
    assert state.capital.capital_pool == Decimal('10000')
    assert state.risk.high_water_mark == Decimal(0)
    assert state.positions == {}
    assert state.mode.mode == OperationalMode.ACTIVE
    assert state.strategy_modes == {}


def test_from_config() -> None:
    '''Verify factory creates state from InstanceConfig.'''

    config = InstanceConfig(
        account_id='acc_001',
        venue='binance_spot',
        allocated_capital=Decimal('50000'),
    )
    state = InstanceState.from_config(config)
    assert state.capital.capital_pool == Decimal('50000')
    assert state.capital.available == Decimal('50000')
    assert state.risk.realized_pnl == Decimal(0)
    assert state.positions == {}
    assert state.mode.mode == OperationalMode.ACTIVE


def test_positions_key_mismatch_rejected() -> None:
    '''Verify positions dict key not matching trade_id raises ValueError.'''

    from nexus.core.domain.enums import OrderSide
    from nexus.core.domain.position import Position

    with pytest.raises(ValueError, match='does not match trade_id'):
        InstanceState(
            capital=CapitalState(capital_pool=Decimal('10000')),
            positions={
                'wrong': Position(
                    trade_id='t1',
                    strategy_id='momentum',
                    symbol='BTCUSDT',
                    side=OrderSide.BUY,
                    size=Decimal('0.5'),
                    entry_price=Decimal('50000'),
                ),
            },
        )


def test_strategy_modes_key_mismatch_rejected() -> None:
    '''Verify strategy_modes dict key not matching strategy_id raises ValueError.'''

    from nexus.core.domain.operational_mode import StrategyModeState

    with pytest.raises(ValueError, match='does not match strategy_id'):
        InstanceState(
            capital=CapitalState(capital_pool=Decimal('10000')),
            strategy_modes={
                'wrong': StrategyModeState(strategy_id='momentum'),
            },
        )
