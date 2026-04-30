'''Risk-stage adapter from RiskState metrics to validator decisions.'''

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from nexus.core.domain.enums import BreachLevel
from nexus.core.domain.risk_state import RiskCheckMetrics
from nexus.core.validator.pipeline_models import (
    ValidationDecision,
    ValidationRequestContext,
    ValidationStage,
)

__all__ = ['RiskStageLimits', 'evaluate_risk_breach', 'validate_risk_stage']

_ZERO = Decimal(0)


@dataclass(frozen=True)
class RiskStageLimits:
    '''Optional risk thresholds used by validator risk-stage checks.'''

    max_total_drawdown: Decimal | None = None
    max_total_drawdown_pct: Decimal | None = None
    max_drawdown_limit: Decimal | None = None
    max_drawdown_pct_limit: Decimal | None = None
    max_rolling_loss_24h: Decimal | None = None
    max_rolling_loss_7d: Decimal | None = None
    max_rolling_loss_30d: Decimal | None = None

    def __post_init__(self) -> None:
        '''Validate configured risk thresholds.'''

        for field_name in (
            'max_total_drawdown',
            'max_total_drawdown_pct',
            'max_drawdown_limit',
            'max_drawdown_pct_limit',
            'max_rolling_loss_24h',
            'max_rolling_loss_7d',
            'max_rolling_loss_30d',
        ):
            value = getattr(self, field_name)
            if value is None:
                continue
            if not isinstance(value, Decimal) or not value.is_finite() or value < _ZERO:
                msg = (
                    f'RiskStageLimits.{field_name} must be a finite '
                    'non-negative Decimal or None'
                )
                raise ValueError(msg)


def evaluate_risk_breach(
    metrics: RiskCheckMetrics,
    limits: RiskStageLimits,
) -> tuple[BreachLevel, str | None, str | None]:
    '''Evaluate drawdown metrics against configured risk limits.

    Returns:
        Tuple of (breach level, reason code, message).
    '''

    threshold_checks: tuple[tuple[str, str, Decimal, Decimal | None], ...] = (
        (
            'total_drawdown',
            'RISK_TOTAL_DRAWDOWN_LIMIT',
            metrics.total_drawdown,
            limits.max_total_drawdown,
        ),
        (
            'total_drawdown_pct',
            'RISK_TOTAL_DRAWDOWN_PCT_LIMIT',
            metrics.total_drawdown_pct,
            limits.max_total_drawdown_pct,
        ),
        (
            'max_drawdown',
            'RISK_MAX_DRAWDOWN_LIMIT',
            metrics.max_drawdown,
            limits.max_drawdown_limit,
        ),
        (
            'max_drawdown_pct',
            'RISK_MAX_DRAWDOWN_PCT_LIMIT',
            metrics.max_drawdown_pct,
            limits.max_drawdown_pct_limit,
        ),
        (
            'rolling_loss_24h',
            'RISK_ROLLING_LOSS_24H_LIMIT',
            metrics.rolling_loss_24h,
            limits.max_rolling_loss_24h,
        ),
        (
            'rolling_loss_7d',
            'RISK_ROLLING_LOSS_7D_LIMIT',
            metrics.rolling_loss_7d,
            limits.max_rolling_loss_7d,
        ),
        (
            'rolling_loss_30d',
            'RISK_ROLLING_LOSS_30D_LIMIT',
            metrics.rolling_loss_30d,
            limits.max_rolling_loss_30d,
        ),
    )

    for metric_name, reason_code, observed, limit in threshold_checks:
        if limit is None:
            continue
        if observed > limit:
            return (
                BreachLevel.BREACH,
                reason_code,
                f'{metric_name} {observed} exceeded configured limit {limit}',
            )

    return (BreachLevel.NONE, None, None)


def validate_risk_stage(
    context: ValidationRequestContext,
    limits: RiskStageLimits,
) -> ValidationDecision:
    '''Validate risk stage using instance drawdown metrics and limits.'''

    metrics = context.state.risk.to_risk_check_metrics()
    breach_level, reason_code, message = evaluate_risk_breach(metrics, limits)

    if breach_level in (BreachLevel.BREACH, BreachLevel.HALT):
        return ValidationDecision(
            allowed=False,
            failed_stage=ValidationStage.RISK,
            reason_code=reason_code,
            message=message,
        )

    return ValidationDecision(allowed=True)
