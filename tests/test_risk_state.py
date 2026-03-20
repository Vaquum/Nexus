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
    assert rs.starting_capital == Decimal(0)
    assert rs.cumulative_realized_pnl == Decimal(0)
    assert rs.unrealized_pnl == Decimal(0)
    assert rs.equity == Decimal(0)
    assert rs.equity_hwm == Decimal(0)
    assert rs.realized_equity_hwm == Decimal(0)
    assert rs.total_drawdown == Decimal(0)
    assert rs.total_drawdown_pct == Decimal(0)
    assert rs.realized_drawdown == Decimal(0)
    assert rs.unrealized_drawdown == Decimal(0)
    assert rs.max_drawdown == Decimal(0)
    assert rs.max_drawdown_pct == Decimal(0)
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


def test_nan_strategy_hwm_rejected() -> None:
    '''Verify NaN high_water_mark in StrategyRiskState raises ValueError.'''

    with pytest.raises(ValueError, match='high_water_mark'):
        StrategyRiskState(strategy_id='momentum', high_water_mark=Decimal('NaN'))


def test_negative_strategy_hwm_rejected() -> None:
    '''Verify negative high_water_mark in StrategyRiskState raises ValueError.'''

    with pytest.raises(ValueError, match='high_water_mark'):
        StrategyRiskState(strategy_id='momentum', high_water_mark=Decimal('-1'))


def test_nan_rolling_loss_rejected() -> None:
    '''Verify NaN rolling_loss_24h in StrategyRiskState raises ValueError.'''

    with pytest.raises(ValueError, match='rolling_loss_24h'):
        StrategyRiskState(strategy_id='momentum', rolling_loss_24h=Decimal('NaN'))


def test_negative_rolling_loss_rejected() -> None:
    '''Verify negative rolling_loss_24h raises ValueError.'''

    with pytest.raises(ValueError, match='rolling_loss_24h'):
        StrategyRiskState(strategy_id='momentum', rolling_loss_24h=Decimal('-50'))


def test_infinity_strategy_pnl_rejected() -> None:
    '''Verify Infinity strategy_realized_pnl raises ValueError.'''

    inf_value = Decimal('Infinity')
    with pytest.raises(ValueError, match='strategy_realized_pnl'):
        StrategyRiskState(strategy_id='momentum', strategy_realized_pnl=inf_value)


def test_nan_instance_hwm_rejected() -> None:
    '''Verify NaN high_water_mark in RiskState raises ValueError.'''

    with pytest.raises(ValueError, match='high_water_mark'):
        RiskState(high_water_mark=Decimal('NaN'))


def test_negative_instance_hwm_rejected() -> None:
    '''Verify negative high_water_mark in RiskState raises ValueError.'''

    with pytest.raises(ValueError, match='high_water_mark'):
        RiskState(high_water_mark=Decimal('-1'))


def test_negative_starting_capital_rejected() -> None:
    '''Verify negative starting_capital in RiskState raises ValueError.'''

    with pytest.raises(ValueError, match='starting_capital'):
        RiskState(starting_capital=Decimal('-1'))


def test_nan_equity_rejected() -> None:
    '''Verify NaN equity in RiskState raises ValueError.'''

    with pytest.raises(ValueError, match='equity'):
        RiskState(equity=Decimal('NaN'))


def test_negative_total_drawdown_rejected() -> None:
    '''Verify negative total_drawdown in RiskState raises ValueError.'''

    with pytest.raises(ValueError, match='total_drawdown'):
        RiskState(total_drawdown=Decimal('-1'))


def test_negative_total_drawdown_pct_rejected() -> None:
    '''Verify negative total_drawdown_pct in RiskState raises ValueError.'''

    with pytest.raises(ValueError, match='total_drawdown_pct'):
        RiskState(total_drawdown_pct=Decimal('-0.01'))


def test_negative_max_drawdown_rejected() -> None:
    '''Verify negative max_drawdown in RiskState raises ValueError.'''

    with pytest.raises(ValueError, match='max_drawdown'):
        RiskState(max_drawdown=Decimal('-1'))


def test_recompute_drawdown_metrics_updates_peaks_and_resets_drawdowns() -> None:
    '''Verify recompute updates HWMs and resets drawdowns at new peaks.'''

    rs = RiskState(
        starting_capital=Decimal('1000'),
        cumulative_realized_pnl=Decimal('120'),
        unrealized_pnl=Decimal('30'),
        equity_hwm=Decimal('1100'),
        realized_equity_hwm=Decimal('1100'),
    )

    rs.recompute_drawdown_metrics()

    assert rs.equity == Decimal('1150')
    assert rs.equity_hwm == Decimal('1150')
    assert rs.high_water_mark == Decimal('1150')
    assert rs.realized_equity_hwm == Decimal('1120')
    assert rs.total_drawdown == Decimal('0')
    assert rs.total_drawdown_pct == Decimal('0')
    assert rs.realized_drawdown == Decimal('0')
    assert rs.unrealized_drawdown == Decimal('0')
    assert rs.max_drawdown == Decimal('0')
    assert rs.max_drawdown_pct == Decimal('0')


def test_recompute_drawdown_metrics_tracks_losses_without_new_peak() -> None:
    '''Verify recompute grows drawdowns under worse PnL without updating HWMs.'''

    rs = RiskState(
        starting_capital=Decimal('1000'),
        cumulative_realized_pnl=Decimal('50'),
        unrealized_pnl=Decimal('-80'),
        equity_hwm=Decimal('1200'),
        realized_equity_hwm=Decimal('1100'),
    )

    rs.recompute_drawdown_metrics()

    assert rs.equity == Decimal('970')
    assert rs.equity_hwm == Decimal('1200')
    assert rs.high_water_mark == Decimal('1200')
    assert rs.total_drawdown == Decimal('230')
    assert rs.total_drawdown_pct == Decimal('0.1916666666666666666666666667')
    assert rs.realized_drawdown == Decimal('50')
    assert rs.unrealized_drawdown == Decimal('80')
    assert rs.max_drawdown == Decimal('230')
    assert rs.max_drawdown_pct == Decimal('0.1916666666666666666666666667')


def test_recompute_drawdown_metrics_sets_unrealized_drawdown_zero_when_flat() -> None:
    '''Verify unrealized drawdown is zero when unrealized_pnl is zero.'''

    rs = RiskState(
        starting_capital=Decimal('1000'),
        cumulative_realized_pnl=Decimal('10'),
        unrealized_pnl=Decimal('0'),
        equity_hwm=Decimal('1010'),
        realized_equity_hwm=Decimal('1010'),
    )

    rs.recompute_drawdown_metrics()

    assert rs.equity == Decimal('1010')
    assert rs.total_drawdown == Decimal('0')
    assert rs.total_drawdown_pct == Decimal('0')
    assert rs.realized_drawdown == Decimal('0')
    assert rs.unrealized_drawdown == Decimal('0')
    assert rs.max_drawdown == Decimal('0')
    assert rs.max_drawdown_pct == Decimal('0')


def test_recompute_drawdown_metrics_preserves_lifetime_max_drawdown() -> None:
    '''Verify max drawdown metrics remain at lifetime worst after recovery.'''

    rs = RiskState(
        starting_capital=Decimal('1000'),
        cumulative_realized_pnl=Decimal('0'),
        unrealized_pnl=Decimal('-300'),
        equity_hwm=Decimal('1000'),
        realized_equity_hwm=Decimal('1000'),
    )

    rs.recompute_drawdown_metrics()
    assert rs.max_drawdown == Decimal('300')
    assert rs.max_drawdown_pct == Decimal('0.3')

    rs.unrealized_pnl = Decimal('50')
    rs.recompute_drawdown_metrics()
    assert rs.total_drawdown == Decimal('0')
    assert rs.max_drawdown == Decimal('300')
    assert rs.max_drawdown_pct == Decimal('0.3')
