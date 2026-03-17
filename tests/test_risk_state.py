'''Verify RiskState and StrategyRiskState creation and derived properties.'''

from __future__ import annotations

from decimal import Decimal

import pytest

from nexus.core.domain.risk_state import RiskState, StrategyRiskState


def test_strategy_risk_state_creation() -> None:
    '''Verify a valid per-strategy risk state is created.'''

    srs = StrategyRiskState(strategy_id='momentum')
    assert srs.high_water_mark == Decimal(0)
    assert srs.rolling_loss_24h == Decimal(0)
    assert srs.strategy_realized_pnl == Decimal(0)


def test_strategy_risk_state_empty_id_rejected() -> None:
    '''Verify empty strategy_id raises ValueError.'''

    with pytest.raises(ValueError, match='strategy_id'):
        StrategyRiskState(strategy_id='')


def test_risk_state_empty() -> None:
    '''Verify instance-level risk state with no strategies.'''

    rs = RiskState()
    assert rs.high_water_mark == Decimal(0)
    assert rs.rolling_loss_24h == Decimal(0)
    assert rs.rolling_loss_7d == Decimal(0)
    assert rs.rolling_loss_30d == Decimal(0)
    assert rs.realized_pnl == Decimal(0)


def test_risk_state_derived_from_strategies() -> None:
    '''Verify instance-level losses are summed from per-strategy state.'''

    rs = RiskState(
        per_strategy={
            'momentum': StrategyRiskState(
                strategy_id='momentum',
                rolling_loss_24h=Decimal('100'),
                rolling_loss_7d=Decimal('500'),
                rolling_loss_30d=Decimal('2000'),
                strategy_realized_pnl=Decimal('800'),
            ),
            'mean_rev': StrategyRiskState(
                strategy_id='mean_rev',
                rolling_loss_24h=Decimal('50'),
                rolling_loss_7d=Decimal('200'),
                rolling_loss_30d=Decimal('1000'),
                strategy_realized_pnl=Decimal('-300'),
            ),
        },
    )
    assert rs.rolling_loss_24h == Decimal('150')
    assert rs.rolling_loss_7d == Decimal('700')
    assert rs.rolling_loss_30d == Decimal('3000')
    assert rs.realized_pnl == Decimal('500')


def test_risk_state_hwm_independent() -> None:
    '''Verify instance HWM is stored independently, not derived.'''

    rs = RiskState(
        high_water_mark=Decimal('50000'),
        per_strategy={
            'momentum': StrategyRiskState(
                strategy_id='momentum',
                high_water_mark=Decimal('30000'),
            ),
            'mean_rev': StrategyRiskState(
                strategy_id='mean_rev',
                high_water_mark=Decimal('25000'),
            ),
        },
    )
    assert rs.high_water_mark == Decimal('50000')
    assert rs.high_water_mark != Decimal('55000')


def test_key_mismatch_rejected() -> None:
    '''Verify dict key not matching strategy_id raises ValueError.'''

    with pytest.raises(ValueError, match='does not match'):
        RiskState(
            per_strategy={
                'wrong_key': StrategyRiskState(strategy_id='momentum'),
            },
        )
