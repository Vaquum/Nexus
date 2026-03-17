'''Risk tracking state for a Manager instance.

Mutable dataclasses holding instance-level and per-strategy risk
metrics. Updated by Praxis Connector on fills, checked by Validator
on every action.
'''

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

__all__ = ['RiskState', 'StrategyRiskState']

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


@dataclass
class RiskState:
    '''Instance-level risk metrics for a Manager instance.

    Rolling losses and realized P&L are derived from per-strategy state.
    High water mark tracks lifetime peak total equity independently
    (not a sum of per-strategy HWMs — they peak at different times).

    Args:
        high_water_mark: Lifetime peak total equity.
        per_strategy: Per-strategy risk state keyed by strategy_id.
    '''

    high_water_mark: Decimal = _ZERO
    per_strategy: dict[str, StrategyRiskState] = field(default_factory=dict)

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
