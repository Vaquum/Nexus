'''Configured limits that trip the risk breakers.'''

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

__all__ = ['RiskBreakerThresholds']

_ZERO = Decimal('0')


@dataclass
class RiskBreakerThresholds:
    '''Configured limits that trip the risk breakers; `None` disables one.

    Args:
        max_daily_loss: Account 24h loss that trips the daily-loss breaker.
        max_drawdown_pct: Peak total-drawdown fraction that trips the
            drawdown breaker.
        max_drawdown: Peak total-drawdown amount that trips the drawdown
            breaker.
    '''

    max_daily_loss: Decimal | None = None
    max_drawdown_pct: Decimal | None = None
    max_drawdown: Decimal | None = None

    def __post_init__(self) -> None:
        '''Validate that configured thresholds are finite and non-negative.'''

        for field_name in ('max_daily_loss', 'max_drawdown_pct', 'max_drawdown'):
            value = getattr(self, field_name)

            if value is None:
                continue

            if not isinstance(value, Decimal) or not value.is_finite() or value < _ZERO:
                msg = f'RiskBreakerThresholds.{field_name} must be a finite non-negative Decimal or None'
                raise ValueError(msg)
