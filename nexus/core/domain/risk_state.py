'''Risk tracking state for a Manager instance.

Mutable dataclasses holding instance-level and per-strategy risk
metrics. Updated by Praxis Connector on fills, checked by Validator
on every action.
'''

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

__all__ = ['DrawdownDiagnostics', 'RiskCheckMetrics', 'RiskState', 'StrategyRiskState']

_ZERO = Decimal(0)


@dataclass
class StrategyRiskState:
    '''Per-strategy risk metrics keyed by strategy_id.

    Args:
        strategy_id: Which strategy this state belongs to.
        high_water_mark: Lifetime peak equity for this strategy.
        rolling_loss_24h: Rolling 24-hour realized loss.
        rolling_loss_7d: Rolling 7-day realized loss (optional).
        rolling_loss_30d: Rolling 30-day realized loss (optional).
        strategy_realized_pnl: Cumulative realized P&L for this strategy.
    '''

    strategy_id: str
    high_water_mark: Decimal = _ZERO
    rolling_loss_24h: Decimal = _ZERO
    rolling_loss_7d: Decimal = _ZERO
    rolling_loss_30d: Decimal = _ZERO
    strategy_realized_pnl: Decimal = _ZERO

    def __post_init__(self) -> None:
        '''Validate invariants at construction time.'''

        if not isinstance(self.strategy_id, str) or not self.strategy_id.strip():
            msg = 'StrategyRiskState.strategy_id must be a non-empty string'
            raise ValueError(msg)

        if not self.high_water_mark.is_finite() or self.high_water_mark < _ZERO:
            msg = 'StrategyRiskState.high_water_mark must be finite and non-negative'
            raise ValueError(msg)

        for field_name in ('rolling_loss_24h', 'rolling_loss_7d', 'rolling_loss_30d'):
            val = getattr(self, field_name)
            if not val.is_finite() or val < _ZERO:
                msg = f'StrategyRiskState.{field_name} must be a finite non-negative value'
                raise ValueError(msg)

        if not self.strategy_realized_pnl.is_finite():
            msg = 'StrategyRiskState.strategy_realized_pnl must be finite'
            raise ValueError(msg)


@dataclass(frozen=True)
class RiskCheckMetrics:
    '''Drawdown metrics exposed to risk-limit checks.'''

    total_drawdown: Decimal
    total_drawdown_pct: Decimal
    max_drawdown: Decimal
    max_drawdown_pct: Decimal


@dataclass(frozen=True)
class DrawdownDiagnostics:
    '''Drawdown telemetry exposed for diagnostics surfaces.'''

    equity: Decimal
    equity_hwm: Decimal
    realized_equity_hwm: Decimal
    total_drawdown: Decimal
    total_drawdown_pct: Decimal
    realized_drawdown: Decimal
    unrealized_drawdown: Decimal
    max_drawdown: Decimal
    max_drawdown_pct: Decimal


@dataclass
class RiskState:
    '''Instance-level risk metrics for a Manager instance.

    Rolling losses and realized P&L are derived from per-strategy state.
    High water mark tracks lifetime peak total equity independently
    (not a sum of per-strategy HWMs — they peak at different times).

    Args:
        high_water_mark: Lifetime peak total equity.
        starting_capital: Initial allocated capital for this instance.
        cumulative_realized_pnl: Cumulative realized P&L at instance scope.
        unrealized_pnl: Current mark-to-market unrealized P&L.
        equity: Current equity (realized equity + unrealized P&L).
        equity_hwm: Lifetime peak of equity.
        realized_equity_hwm: Lifetime peak of realized equity.
        total_drawdown: Current drawdown from equity_hwm.
        total_drawdown_pct: Current drawdown as fraction of equity_hwm.
        realized_drawdown: Current drawdown from realized_equity_hwm.
        unrealized_drawdown: Current unrealized-only drawdown component.
        max_drawdown: Lifetime worst total drawdown.
        max_drawdown_pct: Lifetime worst drawdown fraction.
        per_strategy: Per-strategy risk state keyed by strategy_id.
    '''

    high_water_mark: Decimal = _ZERO
    starting_capital: Decimal = _ZERO
    cumulative_realized_pnl: Decimal = _ZERO
    unrealized_pnl: Decimal = _ZERO
    equity: Decimal = _ZERO
    equity_hwm: Decimal = _ZERO
    realized_equity_hwm: Decimal = _ZERO
    total_drawdown: Decimal = _ZERO
    total_drawdown_pct: Decimal = _ZERO
    realized_drawdown: Decimal = _ZERO
    unrealized_drawdown: Decimal = _ZERO
    max_drawdown: Decimal = _ZERO
    max_drawdown_pct: Decimal = _ZERO
    per_strategy: dict[str, StrategyRiskState] = field(default_factory=dict)

    def __post_init__(self) -> None:
        '''Validate invariants at construction time.'''

        if not self.high_water_mark.is_finite() or self.high_water_mark < _ZERO:
            msg = 'RiskState.high_water_mark must be a finite non-negative value'
            raise ValueError(msg)

        for field_name in (
            'starting_capital',
            'equity_hwm',
            'realized_equity_hwm',
            'total_drawdown',
            'total_drawdown_pct',
            'realized_drawdown',
            'unrealized_drawdown',
            'max_drawdown',
            'max_drawdown_pct',
        ):
            val = getattr(self, field_name)
            if not val.is_finite() or val < _ZERO:
                msg = f'RiskState.{field_name} must be a finite non-negative value'
                raise ValueError(msg)

        for field_name in ('cumulative_realized_pnl', 'unrealized_pnl', 'equity'):
            val = getattr(self, field_name)
            if not val.is_finite():
                msg = f'RiskState.{field_name} must be finite'
                raise ValueError(msg)

        for key, state in self.per_strategy.items():
            if not isinstance(state, StrategyRiskState):
                msg = f'RiskState.per_strategy value for key {key!r} must be a StrategyRiskState'
                raise ValueError(msg)
            if key != state.strategy_id:
                msg = f'RiskState.per_strategy key {key!r} does not match strategy_id {state.strategy_id!r}'
                raise ValueError(msg)

    @property
    def rolling_loss_24h(self) -> Decimal:
        '''Sum of per-strategy 24-hour rolling losses.'''

        return sum((s.rolling_loss_24h for s in self.per_strategy.values()), _ZERO)

    @property
    def rolling_loss_7d(self) -> Decimal:
        '''Sum of per-strategy 7-day rolling losses.'''

        return sum((s.rolling_loss_7d for s in self.per_strategy.values()), _ZERO)

    @property
    def rolling_loss_30d(self) -> Decimal:
        '''Sum of per-strategy 30-day rolling losses.'''

        return sum((s.rolling_loss_30d for s in self.per_strategy.values()), _ZERO)

    @property
    def realized_pnl(self) -> Decimal:
        '''Sum of per-strategy cumulative realized P&L.'''

        return sum((s.strategy_realized_pnl for s in self.per_strategy.values()), _ZERO)

    def recompute_drawdown_metrics(self) -> None:
        '''Recompute equity, HWMs, and drawdown diagnostics deterministically.'''

        realized_equity = self.starting_capital + self.cumulative_realized_pnl
        equity = realized_equity + self.unrealized_pnl
        equity_hwm_seed = max(self.starting_capital, realized_equity, _ZERO)

        self.equity = equity
        self.equity_hwm = max(
            self.equity_hwm, self.high_water_mark, equity_hwm_seed, equity
        )
        self.high_water_mark = self.equity_hwm
        self.realized_equity_hwm = max(
            self.realized_equity_hwm,
            self.starting_capital,
            realized_equity,
        )

        self.total_drawdown = max(_ZERO, self.equity_hwm - equity)
        if self.equity_hwm == _ZERO:
            self.total_drawdown_pct = _ZERO
        else:
            self.total_drawdown_pct = self.total_drawdown / self.equity_hwm
        self.realized_drawdown = max(_ZERO, self.realized_equity_hwm - realized_equity)
        self.unrealized_drawdown = max(_ZERO, -self.unrealized_pnl)

        self.max_drawdown = max(self.max_drawdown, self.total_drawdown)
        self.max_drawdown_pct = max(self.max_drawdown_pct, self.total_drawdown_pct)

    def update_cumulative_realized_pnl(self, cumulative_realized_pnl: Decimal) -> None:
        '''Set cumulative realized P&L and recompute drawdown metrics.'''

        if not cumulative_realized_pnl.is_finite():
            msg = 'cumulative_realized_pnl must be finite'
            raise ValueError(msg)

        self.cumulative_realized_pnl = cumulative_realized_pnl
        self.recompute_drawdown_metrics()

    def update_unrealized_pnl(self, unrealized_pnl: Decimal) -> None:
        '''Set unrealized P&L and recompute drawdown metrics.'''

        if not unrealized_pnl.is_finite():
            msg = 'unrealized_pnl must be finite'
            raise ValueError(msg)

        self.unrealized_pnl = unrealized_pnl
        self.recompute_drawdown_metrics()

    def to_risk_check_metrics(self) -> RiskCheckMetrics:
        '''Return drawdown values needed for validator-style checks.'''

        return RiskCheckMetrics(
            total_drawdown=self.total_drawdown,
            total_drawdown_pct=self.total_drawdown_pct,
            max_drawdown=self.max_drawdown,
            max_drawdown_pct=self.max_drawdown_pct,
        )

    def to_drawdown_diagnostics(self) -> DrawdownDiagnostics:
        '''Return full drawdown telemetry for diagnostics consumers.'''

        return DrawdownDiagnostics(
            equity=self.equity,
            equity_hwm=self.equity_hwm,
            realized_equity_hwm=self.realized_equity_hwm,
            total_drawdown=self.total_drawdown,
            total_drawdown_pct=self.total_drawdown_pct,
            realized_drawdown=self.realized_drawdown,
            unrealized_drawdown=self.unrealized_drawdown,
            max_drawdown=self.max_drawdown,
            max_drawdown_pct=self.max_drawdown_pct,
        )
