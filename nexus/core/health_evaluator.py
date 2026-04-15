'''Health evaluation for operational mode determination.

Evaluates health metrics against three-threshold policy
(warn/breach/halt) to determine operational mode.
'''

from __future__ import annotations

from dataclasses import dataclass

from nexus.core.domain.enums import OperationalMode

__all__ = ['HealthEvaluator', 'HealthSnapshot', 'HealthThresholds']


@dataclass(frozen=True)
class HealthSnapshot:
    '''Point-in-time health metrics from Trading sub-system.

    Args:
        latency_p99_ms: Ack latency p99 in milliseconds.
        consecutive_failures: Number of consecutive failures.
        failure_rate: Failure rate over rolling window (0.0 to 1.0).
        rate_limit_headroom: Rate limit utilization (0.0 to 1.0).
        clock_drift_ms: Clock drift from exchange in milliseconds.
    '''

    latency_p99_ms: float = 0.0
    consecutive_failures: int = 0
    failure_rate: float = 0.0
    rate_limit_headroom: float = 0.0
    clock_drift_ms: float = 0.0

    def __post_init__(self) -> None:
        '''Validate health metric invariants.'''

        import math

        for field_name in ('latency_p99_ms', 'failure_rate', 'rate_limit_headroom', 'clock_drift_ms'):
            val = getattr(self, field_name)
            if not isinstance(val, (int, float)) or not math.isfinite(val) or val < 0:
                msg = f'HealthSnapshot.{field_name} must be a finite non-negative number'
                raise ValueError(msg)

        if not isinstance(self.consecutive_failures, int) or isinstance(self.consecutive_failures, bool):
            msg = 'HealthSnapshot.consecutive_failures must be an int'
            raise ValueError(msg)

        if self.consecutive_failures < 0:
            msg = 'HealthSnapshot.consecutive_failures must be non-negative'
            raise ValueError(msg)


@dataclass(frozen=True)
class HealthThresholds:
    '''Three-threshold health policy configuration.

    Args:
        latency_warn_ms: Latency alert threshold.
        latency_breach_ms: Latency breach action threshold.
        latency_halt_ms: Latency halt threshold.
        failure_warn: Consecutive failure alert threshold.
        failure_breach: Consecutive failure breach threshold.
        failure_halt: Consecutive failure halt threshold.
        failure_rate_warn: Failure rate alert threshold.
        failure_rate_breach: Failure rate breach threshold.
        failure_rate_halt: Failure rate halt threshold.
        headroom_warn: Rate limit headroom alert threshold.
        headroom_breach: Rate limit headroom breach threshold.
        headroom_halt: Rate limit headroom halt threshold.
        clock_drift_max_ms: Max clock drift before halt.
    '''

    latency_warn_ms: float = 200.0
    latency_breach_ms: float = 500.0
    latency_halt_ms: float = 1000.0
    failure_warn: int = 3
    failure_breach: int = 5
    failure_halt: int = 10
    failure_rate_warn: float = 0.10
    failure_rate_breach: float = 0.20
    failure_rate_halt: float = 0.40
    headroom_warn: float = 0.70
    headroom_breach: float = 0.85
    headroom_halt: float = 0.90
    clock_drift_max_ms: float = 500.0


class HealthEvaluator:
    '''Evaluate health snapshot against thresholds to determine mode.

    Args:
        thresholds: Health policy thresholds.
    '''

    def __init__(self, thresholds: HealthThresholds | None = None) -> None:
        self._thresholds = thresholds or HealthThresholds()

    def evaluate(self, snapshot: HealthSnapshot) -> OperationalMode:
        '''Determine operational mode from health snapshot.

        Args:
            snapshot: Current health metrics.

        Returns:
            OperationalMode based on worst metric breach level.
        '''

        t = self._thresholds

        if (
            snapshot.latency_p99_ms >= t.latency_halt_ms
            or snapshot.consecutive_failures >= t.failure_halt
            or snapshot.failure_rate >= t.failure_rate_halt
            or snapshot.rate_limit_headroom >= t.headroom_halt
            or snapshot.clock_drift_ms >= t.clock_drift_max_ms
        ):
            return OperationalMode.HALTED

        if (
            snapshot.latency_p99_ms >= t.latency_breach_ms
            or snapshot.consecutive_failures >= t.failure_breach
            or snapshot.failure_rate >= t.failure_rate_breach
            or snapshot.rate_limit_headroom >= t.headroom_breach
        ):
            return OperationalMode.REDUCE_ONLY

        return OperationalMode.ACTIVE
