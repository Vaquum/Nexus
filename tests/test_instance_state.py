'''Verify InstanceState composition and factory method.'''

from __future__ import annotations

from decimal import Decimal

import pytest

from nexus.core.domain.capital_state import CapitalState
from nexus.core.domain.enums import OperationalMode, OrderSide
from nexus.core.domain.instance_state import InstanceState
from nexus.core.domain.operational_mode import StrategyModeState
from nexus.core.domain.position import Position


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


def test_fresh() -> None:
    '''Verify factory creates initial empty state seeded with the given capital_pool (operational allocation, not ceiling).'''

    state = InstanceState.fresh(Decimal('50000'))
    assert state.capital.capital_pool == Decimal('50000')
    assert state.capital.available == Decimal('50000')
    assert state.risk.realized_pnl == Decimal(0)
    assert state.positions == {}
    assert state.mode.mode == OperationalMode.ACTIVE


def test_fresh_rejects_non_decimal() -> None:
    '''int/float/str capital_pool raises ValueError, not AttributeError.'''

    with pytest.raises(ValueError, match='must receive a finite Decimal'):
        InstanceState.fresh(10000)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match='must receive a finite Decimal'):
        InstanceState.fresh(10000.0)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match='must receive a finite Decimal'):
        InstanceState.fresh('10000')  # type: ignore[arg-type]


def test_fresh_rejects_non_finite() -> None:
    '''NaN/Infinity capital_pool raises ValueError.'''

    with pytest.raises(ValueError, match='must receive a finite Decimal'):
        InstanceState.fresh(Decimal('NaN'))

    with pytest.raises(ValueError, match='must receive a finite Decimal'):
        InstanceState.fresh(Decimal('Infinity'))


def test_fresh_rejects_non_positive() -> None:
    '''Zero or negative capital_pool raises ValueError.'''

    with pytest.raises(ValueError, match='must receive a positive'):
        InstanceState.fresh(Decimal('0'))

    with pytest.raises(ValueError, match='must receive a positive'):
        InstanceState.fresh(Decimal('-1000'))


def test_positions_key_mismatch_rejected() -> None:
    '''Verify positions dict key not matching trade_id raises ValueError.'''

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

    with pytest.raises(ValueError, match='does not match strategy_id'):
        InstanceState(
            capital=CapitalState(capital_pool=Decimal('10000')),
            strategy_modes={
                'wrong': StrategyModeState(strategy_id='momentum'),
            },
        )
