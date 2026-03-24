from __future__ import annotations

from dataclasses import dataclass, field
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
            if value < _ZERO_DECIMAL:
                msg = (
                    'HealthMetricThresholds values must be non-negative '
                    f'Decimals or None: {field_name}'
                )
                raise ValueError(msg)


@dataclass(frozen=True)
class HealthStagePolicy:
    latency_ms: HealthMetricThresholds = field(default_factory=HealthMetricThresholds)
    consecutive_failures: HealthMetricThresholds = field(
        default_factory=HealthMetricThresholds
    )
    failure_rate: HealthMetricThresholds = field(default_factory=HealthMetricThresholds)
    rate_limit_headroom: HealthMetricThresholds = field(
        default_factory=HealthMetricThresholds
    )
    clock_drift_ms: HealthMetricThresholds = field(
        default_factory=HealthMetricThresholds
    )

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

        _validate_threshold_order(
            metric_name='latency_ms',
            thresholds=self.latency_ms,
            higher_is_worse=True,
        )
        _validate_threshold_order(
            metric_name='consecutive_failures',
            thresholds=self.consecutive_failures,
            higher_is_worse=True,
        )
        _validate_threshold_order(
            metric_name='failure_rate',
            thresholds=self.failure_rate,
            higher_is_worse=True,
        )
        _validate_threshold_order(
            metric_name='rate_limit_headroom',
            thresholds=self.rate_limit_headroom,
            higher_is_worse=False,
        )
        _validate_threshold_order(
            metric_name='clock_drift_ms',
            thresholds=self.clock_drift_ms,
            higher_is_worse=True,
        )


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

    halt_code: str | None = None
    halt_message: str | None = None
    for (
        metric_name,
        observed,
        thresholds,
        higher_is_worse,
        _,
        current_halt_code,
    ) in evaluated:
        if thresholds.halt is not None:
            if higher_is_worse and observed >= thresholds.halt:
                halt_code = current_halt_code
                halt_message = (
                    f'{metric_name} {observed} reached halt threshold {thresholds.halt}'
                )
                break
            if not higher_is_worse and observed <= thresholds.halt:
                halt_code = current_halt_code
                halt_message = (
                    f'{metric_name} {observed} reached halt threshold {thresholds.halt}'
                )
                break

    if halt_code is not None:
        return (BreachLevel.HALT, halt_code, halt_message)

    breach_code: str | None = None
    breach_message: str | None = None
    for (
        metric_name,
        observed,
        thresholds,
        higher_is_worse,
        current_breach_code,
        _,
    ) in evaluated:
        if thresholds.breach is None:
            continue

        if higher_is_worse and observed >= thresholds.breach:
            breach_code = current_breach_code
            breach_message = (
                f'{metric_name} {observed} reached breach threshold {thresholds.breach}'
            )
            break

        if not higher_is_worse and observed <= thresholds.breach:
            breach_code = current_breach_code
            breach_message = (
                f'{metric_name} {observed} reached breach threshold {thresholds.breach}'
            )
            break

    if breach_code is not None:
        return (BreachLevel.BREACH, breach_code, breach_message)

    warn_message: str | None = None
    for metric_name, observed, thresholds, higher_is_worse, _, _ in evaluated:
        if thresholds.warn is None:
            continue

        if higher_is_worse and observed >= thresholds.warn:
            warn_message = (
                f'{metric_name} {observed} reached warn threshold {thresholds.warn}'
            )
            break

        if not higher_is_worse and observed <= thresholds.warn:
            warn_message = (
                f'{metric_name} {observed} reached warn threshold {thresholds.warn}'
            )
            break

    if warn_message is not None:
        return (BreachLevel.WARN, None, warn_message)

    return (BreachLevel.NONE, None, None)


def _validate_threshold_order(
    *,
    metric_name: str,
    thresholds: HealthMetricThresholds,
    higher_is_worse: bool,
) -> None:
    warn = thresholds.warn
    breach = thresholds.breach
    halt = thresholds.halt

    if warn is not None and breach is not None:
        if higher_is_worse and warn > breach:
            msg = (
                f'HealthStagePolicy.{metric_name} requires warn <= breach '
                'for higher-is-worse metrics'
            )
            raise ValueError(msg)
        if not higher_is_worse and warn < breach:
            msg = (
                f'HealthStagePolicy.{metric_name} requires warn >= breach '
                'for lower-is-worse metrics'
            )
            raise ValueError(msg)

    if breach is not None and halt is not None:
        if higher_is_worse and breach > halt:
            msg = (
                f'HealthStagePolicy.{metric_name} requires breach <= halt '
                'for higher-is-worse metrics'
            )
            raise ValueError(msg)
        if not higher_is_worse and breach < halt:
            msg = (
                f'HealthStagePolicy.{metric_name} requires breach >= halt '
                'for lower-is-worse metrics'
            )
            raise ValueError(msg)


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
