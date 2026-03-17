'''Verify InstanceState composition and factory method.'''

from __future__ import annotations

from decimal import Decimal

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
