'''Verify RiskState and StrategyRiskState creation and derived properties.'''

from __future__ import annotations

from decimal import Decimal

import pytest

from nexus.core.domain.risk_state import (
    DrawdownDiagnostics,
    RiskCheckMetrics,
    RiskState,
    StrategyRiskState,
)


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


def test_update_cumulative_realized_pnl_triggers_recompute() -> None:
    '''Verify updating cumulative realized PnL recomputes drawdown metrics.'''

    rs = RiskState(
        starting_capital=Decimal('1000'),
        equity_hwm=Decimal('1100'),
        realized_equity_hwm=Decimal('1100'),
    )

    rs.update_cumulative_realized_pnl(Decimal('-50'))

    assert rs.cumulative_realized_pnl == Decimal('-50')
    assert rs.equity == Decimal('950')
    assert rs.total_drawdown == Decimal('150')
    assert rs.total_drawdown_pct == Decimal('0.1363636363636363636363636364')


def test_update_unrealized_pnl_triggers_recompute() -> None:
    '''Verify updating unrealized PnL recomputes drawdown metrics.'''

    rs = RiskState(
        starting_capital=Decimal('1000'),
        cumulative_realized_pnl=Decimal('0'),
        equity_hwm=Decimal('1000'),
        realized_equity_hwm=Decimal('1000'),
    )

    rs.update_unrealized_pnl(Decimal('-200'))

    assert rs.unrealized_pnl == Decimal('-200')
    assert rs.equity == Decimal('800')
    assert rs.total_drawdown == Decimal('200')
    assert rs.total_drawdown_pct == Decimal('0.2')
    assert rs.unrealized_drawdown == Decimal('200')


def test_to_risk_check_metrics_exposes_validator_fields() -> None:
    '''Verify validator-facing drawdown metrics are exposed in one typed view.'''

    rs = RiskState(
        total_drawdown=Decimal('100'),
        total_drawdown_pct=Decimal('0.1'),
        max_drawdown=Decimal('250'),
        max_drawdown_pct=Decimal('0.2'),
    )

    metrics = rs.to_risk_check_metrics()

    assert isinstance(metrics, RiskCheckMetrics)
    assert metrics.total_drawdown == Decimal('100')
    assert metrics.total_drawdown_pct == Decimal('0.1')
    assert metrics.max_drawdown == Decimal('250')
    assert metrics.max_drawdown_pct == Decimal('0.2')


def test_to_drawdown_diagnostics_exposes_telemetry_fields() -> None:
    '''Verify diagnostics-facing drawdown telemetry fields are exposed together.'''

    rs = RiskState(
        equity=Decimal('900'),
        equity_hwm=Decimal('1000'),
        realized_equity_hwm=Decimal('980'),
        total_drawdown=Decimal('100'),
        total_drawdown_pct=Decimal('0.1'),
        realized_drawdown=Decimal('80'),
        unrealized_drawdown=Decimal('20'),
        max_drawdown=Decimal('150'),
        max_drawdown_pct=Decimal('0.15'),
    )

    diagnostics = rs.to_drawdown_diagnostics()

    assert isinstance(diagnostics, DrawdownDiagnostics)
    assert diagnostics.equity == Decimal('900')
    assert diagnostics.equity_hwm == Decimal('1000')
    assert diagnostics.realized_equity_hwm == Decimal('980')
    assert diagnostics.total_drawdown == Decimal('100')
    assert diagnostics.total_drawdown_pct == Decimal('0.1')
    assert diagnostics.realized_drawdown == Decimal('80')
    assert diagnostics.unrealized_drawdown == Decimal('20')
    assert diagnostics.max_drawdown == Decimal('150')
    assert diagnostics.max_drawdown_pct == Decimal('0.15')


def test_drawdown_formula_correctness_across_pnl_updates() -> None:
    '''Verify recompute formulas hold over a multi-step PnL sequence.'''

    rs = RiskState(starting_capital=Decimal('1000'))

    steps = [
        (Decimal('0'), Decimal('0')),
        (Decimal('50'), Decimal('25')),
        (Decimal('20'), Decimal('-100')),
        (Decimal('-10'), Decimal('-250')),
        (Decimal('80'), Decimal('10')),
    ]

    for cumulative_realized_pnl, unrealized_pnl in steps:
        rs.update_cumulative_realized_pnl(cumulative_realized_pnl)
        rs.update_unrealized_pnl(unrealized_pnl)

        realized_equity = rs.starting_capital + rs.cumulative_realized_pnl
        equity = realized_equity + rs.unrealized_pnl

        assert rs.equity == equity
        assert rs.total_drawdown == max(Decimal('0'), rs.equity_hwm - equity)
        assert rs.realized_drawdown == max(
            Decimal('0'), rs.realized_equity_hwm - realized_equity
        )
        assert rs.unrealized_drawdown == max(Decimal('0'), -rs.unrealized_pnl)
        if rs.equity_hwm == Decimal('0'):
            assert rs.total_drawdown_pct == Decimal('0')
        else:
            assert rs.total_drawdown_pct == rs.total_drawdown / rs.equity_hwm


def test_equity_hwm_and_max_drawdown_are_monotonic_over_sequence() -> None:
    '''Verify HWM and max drawdown metrics never decrease over updates.'''

    rs = RiskState(starting_capital=Decimal('1000'))
    prior_equity_hwm = rs.equity_hwm
    prior_realized_hwm = rs.realized_equity_hwm
    prior_max_drawdown = rs.max_drawdown
    prior_max_drawdown_pct = rs.max_drawdown_pct

    sequence = [
        (Decimal('0'), Decimal('0')),
        (Decimal('100'), Decimal('0')),
        (Decimal('100'), Decimal('-50')),
        (Decimal('200'), Decimal('25')),
        (Decimal('-20'), Decimal('-300')),
    ]

    for cumulative_realized_pnl, unrealized_pnl in sequence:
        rs.update_cumulative_realized_pnl(cumulative_realized_pnl)
        rs.update_unrealized_pnl(unrealized_pnl)

        assert rs.equity_hwm >= prior_equity_hwm
        assert rs.realized_equity_hwm >= prior_realized_hwm
        assert rs.max_drawdown >= prior_max_drawdown
        assert rs.max_drawdown_pct >= prior_max_drawdown_pct

        prior_equity_hwm = rs.equity_hwm
        prior_realized_hwm = rs.realized_equity_hwm
        prior_max_drawdown = rs.max_drawdown
        prior_max_drawdown_pct = rs.max_drawdown_pct


def test_recompute_preserves_legacy_high_water_mark_when_equity_hwm_unset() -> None:
    '''Verify recompute keeps existing high_water_mark as HWM floor.'''

    rs = RiskState(
        high_water_mark=Decimal('1000'),
        starting_capital=Decimal('500'),
        cumulative_realized_pnl=Decimal('0'),
        unrealized_pnl=Decimal('0'),
        equity_hwm=Decimal('0'),
        realized_equity_hwm=Decimal('0'),
    )

    rs.recompute_drawdown_metrics()

    assert rs.equity == Decimal('500')
    assert rs.equity_hwm == Decimal('1000')
    assert rs.high_water_mark == Decimal('1000')
    assert rs.total_drawdown == Decimal('500')
