from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from nexus.core.domain.enums import BreachLevel
from nexus.core.validator.pipeline_models import (
    ValidationDecision,
    ValidationRequestContext,
    ValidationStage,
)

__all__ = [
    'HEALTH_CLOCK_DRIFT_BREACH_CODE',
    'HEALTH_CLOCK_DRIFT_HALT_CODE',
    'HEALTH_CONSECUTIVE_FAILURES_BREACH_CODE',
    'HEALTH_CONSECUTIVE_FAILURES_HALT_CODE',
    'HEALTH_FAILURE_RATE_BREACH_CODE',
    'HEALTH_FAILURE_RATE_HALT_CODE',
    'HEALTH_LATENCY_BREACH_CODE',
    'HEALTH_LATENCY_HALT_CODE',
    'HEALTH_RATE_LIMIT_HEADROOM_BREACH_CODE',
    'HEALTH_RATE_LIMIT_HEADROOM_HALT_CODE',
    'HealthMetricThresholds',
    'HealthStagePolicy',
    'HealthStageSnapshot',
    'evaluate_health_status',
    'validate_health_stage',
]

_ZERO_DECIMAL = Decimal(0)
_ONE_DECIMAL = Decimal(1)

HEALTH_LATENCY_BREACH_CODE = 'HEALTH_LATENCY_BREACH'
HEALTH_LATENCY_HALT_CODE = 'HEALTH_LATENCY_HALT'
HEALTH_CONSECUTIVE_FAILURES_BREACH_CODE = 'HEALTH_CONSECUTIVE_FAILURES_BREACH'
HEALTH_CONSECUTIVE_FAILURES_HALT_CODE = 'HEALTH_CONSECUTIVE_FAILURES_HALT'
HEALTH_FAILURE_RATE_BREACH_CODE = 'HEALTH_FAILURE_RATE_BREACH'
HEALTH_FAILURE_RATE_HALT_CODE = 'HEALTH_FAILURE_RATE_HALT'
HEALTH_RATE_LIMIT_HEADROOM_BREACH_CODE = 'HEALTH_RATE_LIMIT_HEADROOM_BREACH'
HEALTH_RATE_LIMIT_HEADROOM_HALT_CODE = 'HEALTH_RATE_LIMIT_HEADROOM_HALT'
HEALTH_CLOCK_DRIFT_BREACH_CODE = 'HEALTH_CLOCK_DRIFT_BREACH'
HEALTH_CLOCK_DRIFT_HALT_CODE = 'HEALTH_CLOCK_DRIFT_HALT'


@dataclass(frozen=True)
class HealthMetricThresholds:
    warn: Decimal | None = None
    breach: Decimal | None = None
    halt: Decimal | None = None

    def __post_init__(self) -> None:
        for field_name in ('warn', 'breach', 'halt'):
            value = getattr(self, field_name)
            if value is None:
                continue
            if not isinstance(value, Decimal) or not value.is_finite():
                msg = (
                    'HealthMetricThresholds values must be finite Decimal values '
                    f'or None: {field_name}'
                )
                raise ValueError(msg)

        if (
            self.warn is not None
            and self.breach is not None
            and self.warn > self.breach
        ):
            msg = 'HealthMetricThresholds.warn must be <= breach when both are set'
            raise ValueError(msg)

        if (
            self.breach is not None
            and self.halt is not None
            and self.breach > self.halt
        ):
            msg = 'HealthMetricThresholds.breach must be <= halt when both are set'
            raise ValueError(msg)


@dataclass(frozen=True)
class HealthStagePolicy:
    latency_ms: HealthMetricThresholds = HealthMetricThresholds()
    consecutive_failures: HealthMetricThresholds = HealthMetricThresholds()
    failure_rate: HealthMetricThresholds = HealthMetricThresholds()
    rate_limit_headroom: HealthMetricThresholds = HealthMetricThresholds()
    clock_drift_ms: HealthMetricThresholds = HealthMetricThresholds()

    def __post_init__(self) -> None:
        for field_name in (
            'latency_ms',
            'consecutive_failures',
            'failure_rate',
            'rate_limit_headroom',
            'clock_drift_ms',
        ):
            value = getattr(self, field_name)
            if not isinstance(value, HealthMetricThresholds):
                msg = f'HealthStagePolicy.{field_name} must be HealthMetricThresholds'
                raise ValueError(msg)

        for field_name in ('failure_rate', 'rate_limit_headroom'):
            thresholds = getattr(self, field_name)
            for level_name in ('warn', 'breach', 'halt'):
                value = getattr(thresholds, level_name)
                if value is None:
                    continue
                if value < _ZERO_DECIMAL or value > _ONE_DECIMAL:
                    msg = (
                        f'HealthStagePolicy.{field_name}.{level_name} must be '
                        'between 0 and 1'
                    )
                    raise ValueError(msg)


@dataclass(frozen=True)
class HealthStageSnapshot:
    latency_ms: Decimal
    consecutive_failures: Decimal
    failure_rate: Decimal
    rate_limit_headroom: Decimal
    clock_drift_ms: Decimal

    def __post_init__(self) -> None:
        for field_name in (
            'latency_ms',
            'consecutive_failures',
            'failure_rate',
            'rate_limit_headroom',
            'clock_drift_ms',
        ):
            value = getattr(self, field_name)
            if not isinstance(value, Decimal) or not value.is_finite():
                msg = f'HealthStageSnapshot.{field_name} must be a finite Decimal'
                raise ValueError(msg)

        if self.latency_ms < _ZERO_DECIMAL:
            msg = 'HealthStageSnapshot.latency_ms must be non-negative'
            raise ValueError(msg)

        if self.consecutive_failures < _ZERO_DECIMAL:
            msg = 'HealthStageSnapshot.consecutive_failures must be non-negative'
            raise ValueError(msg)

        if self.failure_rate < _ZERO_DECIMAL or self.failure_rate > _ONE_DECIMAL:
            msg = 'HealthStageSnapshot.failure_rate must be between 0 and 1'
            raise ValueError(msg)

        if (
            self.rate_limit_headroom < _ZERO_DECIMAL
            or self.rate_limit_headroom > _ONE_DECIMAL
        ):
            msg = 'HealthStageSnapshot.rate_limit_headroom must be between 0 and 1'
            raise ValueError(msg)

        if self.clock_drift_ms < _ZERO_DECIMAL:
            msg = 'HealthStageSnapshot.clock_drift_ms must be non-negative'
            raise ValueError(msg)


def evaluate_health_status(
    snapshot: HealthStageSnapshot,
    policy: HealthStagePolicy,
) -> tuple[BreachLevel, str | None, str | None]:
    evaluated: tuple[
        tuple[str, Decimal, HealthMetricThresholds, bool, str, str], ...
    ] = (
        (
            'latency_ms',
            snapshot.latency_ms,
            policy.latency_ms,
            True,
            HEALTH_LATENCY_BREACH_CODE,
            HEALTH_LATENCY_HALT_CODE,
        ),
        (
            'consecutive_failures',
            snapshot.consecutive_failures,
            policy.consecutive_failures,
            True,
            HEALTH_CONSECUTIVE_FAILURES_BREACH_CODE,
            HEALTH_CONSECUTIVE_FAILURES_HALT_CODE,
        ),
        (
            'failure_rate',
            snapshot.failure_rate,
            policy.failure_rate,
            True,
            HEALTH_FAILURE_RATE_BREACH_CODE,
            HEALTH_FAILURE_RATE_HALT_CODE,
        ),
        (
            'rate_limit_headroom',
            snapshot.rate_limit_headroom,
            policy.rate_limit_headroom,
            False,
            HEALTH_RATE_LIMIT_HEADROOM_BREACH_CODE,
            HEALTH_RATE_LIMIT_HEADROOM_HALT_CODE,
        ),
        (
            'clock_drift_ms',
            snapshot.clock_drift_ms,
            policy.clock_drift_ms,
            True,
            HEALTH_CLOCK_DRIFT_BREACH_CODE,
            HEALTH_CLOCK_DRIFT_HALT_CODE,
        ),
    )

    breach_level = BreachLevel.NONE
    reason_code: str | None = None
    message: str | None = None

    for (
        metric_name,
        observed,
        thresholds,
        higher_is_worse,
        breach_code,
        halt_code,
    ) in evaluated:
        if thresholds.halt is not None:
            if higher_is_worse and observed >= thresholds.halt:
                breach_level = BreachLevel.HALT
                reason_code = halt_code
                message = (
                    f'{metric_name} {observed} reached halt threshold {thresholds.halt}'
                )
                break
            if not higher_is_worse and observed <= thresholds.halt:
                breach_level = BreachLevel.HALT
                reason_code = halt_code
                message = (
                    f'{metric_name} {observed} reached halt threshold {thresholds.halt}'
                )
                break

        if thresholds.breach is not None:
            if higher_is_worse and observed >= thresholds.breach:
                breach_level = BreachLevel.BREACH
                reason_code = breach_code
                message = f'{metric_name} {observed} reached breach threshold {thresholds.breach}'
                break
            if not higher_is_worse and observed <= thresholds.breach:
                breach_level = BreachLevel.BREACH
                reason_code = breach_code
                message = f'{metric_name} {observed} reached breach threshold {thresholds.breach}'
                break

    if breach_level is not BreachLevel.NONE:
        return (breach_level, reason_code, message)

    for metric_name, observed, thresholds, higher_is_worse, _, _ in evaluated:
        if thresholds.warn is None:
            continue

        if higher_is_worse and observed >= thresholds.warn:
            breach_level = BreachLevel.WARN
            message = (
                f'{metric_name} {observed} reached warn threshold {thresholds.warn}'
            )
            break

        if not higher_is_worse and observed <= thresholds.warn:
            breach_level = BreachLevel.WARN
            message = (
                f'{metric_name} {observed} reached warn threshold {thresholds.warn}'
            )
            break

    if breach_level is BreachLevel.WARN:
        return (breach_level, None, message)

    return (BreachLevel.NONE, None, None)


def validate_health_stage(
    context: ValidationRequestContext,
    snapshot: HealthStageSnapshot,
    policy: HealthStagePolicy,
) -> ValidationDecision:
    _ = context

    breach_level, reason_code, message = evaluate_health_status(snapshot, policy)
    if breach_level in (BreachLevel.BREACH, BreachLevel.HALT):
        return ValidationDecision(
            allowed=False,
            failed_stage=ValidationStage.HEALTH,
            reason_code=reason_code,
            message=message,
        )

    return ValidationDecision(allowed=True)
