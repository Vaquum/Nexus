'''Tests for HealthEvaluator mode determination.'''

from __future__ import annotations

import pytest

from nexus.core.domain.enums import OperationalMode
from nexus.core.health_evaluator import (
    HealthEvaluator,
    HealthSnapshot,
    HealthThresholds,
)


class TestHealthEvaluator:

    def test_healthy_returns_active(self) -> None:
        '''All metrics within thresholds returns ACTIVE.'''

        evaluator = HealthEvaluator()
        snapshot = HealthSnapshot()

        assert evaluator.evaluate(snapshot) == OperationalMode.ACTIVE

    def test_latency_breach_returns_reduce_only(self) -> None:
        '''Latency at breach threshold returns REDUCE_ONLY.'''

        evaluator = HealthEvaluator()
        snapshot = HealthSnapshot(latency_p99_ms=500.0)

        assert evaluator.evaluate(snapshot) == OperationalMode.REDUCE_ONLY

    def test_latency_halt_returns_halted(self) -> None:
        '''Latency at halt threshold returns HALTED.'''

        evaluator = HealthEvaluator()
        snapshot = HealthSnapshot(latency_p99_ms=1000.0)

        assert evaluator.evaluate(snapshot) == OperationalMode.HALTED

    def test_consecutive_failures_breach(self) -> None:
        '''Consecutive failures at breach returns REDUCE_ONLY.'''

        evaluator = HealthEvaluator()
        snapshot = HealthSnapshot(consecutive_failures=5)

        assert evaluator.evaluate(snapshot) == OperationalMode.REDUCE_ONLY

    def test_consecutive_failures_halt(self) -> None:
        '''Consecutive failures at halt returns HALTED.'''

        evaluator = HealthEvaluator()
        snapshot = HealthSnapshot(consecutive_failures=10)

        assert evaluator.evaluate(snapshot) == OperationalMode.HALTED

    def test_failure_rate_breach(self) -> None:
        '''Failure rate at breach returns REDUCE_ONLY.'''

        evaluator = HealthEvaluator()
        snapshot = HealthSnapshot(failure_rate=0.20)

        assert evaluator.evaluate(snapshot) == OperationalMode.REDUCE_ONLY

    def test_failure_rate_halt(self) -> None:
        '''Failure rate at halt returns HALTED.'''

        evaluator = HealthEvaluator()
        snapshot = HealthSnapshot(failure_rate=0.40)

        assert evaluator.evaluate(snapshot) == OperationalMode.HALTED

    def test_headroom_breach(self) -> None:
        '''Rate limit headroom at breach returns REDUCE_ONLY.'''

        evaluator = HealthEvaluator()
        snapshot = HealthSnapshot(rate_limit_headroom=0.85)

        assert evaluator.evaluate(snapshot) == OperationalMode.REDUCE_ONLY

    def test_headroom_halt(self) -> None:
        '''Rate limit headroom at halt returns HALTED.'''

        evaluator = HealthEvaluator()
        snapshot = HealthSnapshot(rate_limit_headroom=0.90)

        assert evaluator.evaluate(snapshot) == OperationalMode.HALTED

    def test_clock_drift_halt(self) -> None:
        '''Clock drift at max returns HALTED.'''

        evaluator = HealthEvaluator()
        snapshot = HealthSnapshot(clock_drift_ms=500.0)

        assert evaluator.evaluate(snapshot) == OperationalMode.HALTED

    def test_halt_takes_precedence_over_breach(self) -> None:
        '''When both breach and halt metrics are hit, HALTED wins.'''

        evaluator = HealthEvaluator()
        snapshot = HealthSnapshot(
            latency_p99_ms=600.0,
            consecutive_failures=10,
        )

        assert evaluator.evaluate(snapshot) == OperationalMode.HALTED

    def test_custom_thresholds(self) -> None:
        '''Custom thresholds override defaults.'''

        thresholds = HealthThresholds(latency_warn_ms=50.0, latency_breach_ms=100.0, latency_halt_ms=200.0)
        evaluator = HealthEvaluator(thresholds)

        assert evaluator.evaluate(HealthSnapshot(latency_p99_ms=150.0)) == OperationalMode.REDUCE_ONLY
        assert evaluator.evaluate(HealthSnapshot(latency_p99_ms=200.0)) == OperationalMode.HALTED

    def test_just_below_breach_is_active(self) -> None:
        '''Metrics just below breach threshold remain ACTIVE.'''

        evaluator = HealthEvaluator()
        snapshot = HealthSnapshot(
            latency_p99_ms=499.9,
            consecutive_failures=4,
            failure_rate=0.19,
            rate_limit_headroom=0.84,
        )

        assert evaluator.evaluate(snapshot) == OperationalMode.ACTIVE


class TestHealthSnapshotValidation:

    def test_nan_latency_raises(self) -> None:
        with pytest.raises(ValueError, match='finite non-negative'):
            HealthSnapshot(latency_p99_ms=float('nan'))

    def test_negative_failure_rate_raises(self) -> None:
        with pytest.raises(ValueError, match='finite non-negative'):
            HealthSnapshot(failure_rate=-0.1)

    def test_failure_rate_over_one_raises(self) -> None:
        with pytest.raises(ValueError, match=r'<= 1\.0'):
            HealthSnapshot(failure_rate=1.5)

    def test_headroom_over_one_raises(self) -> None:
        with pytest.raises(ValueError, match=r'<= 1\.0'):
            HealthSnapshot(rate_limit_headroom=1.1)

    def test_bool_latency_raises(self) -> None:
        with pytest.raises(ValueError, match='finite non-negative'):
            HealthSnapshot(latency_p99_ms=True)  # type: ignore[arg-type]

    def test_bool_consecutive_failures_raises(self) -> None:
        with pytest.raises(ValueError, match='must be an int'):
            HealthSnapshot(consecutive_failures=True)  # type: ignore[arg-type]

    def test_negative_consecutive_failures_raises(self) -> None:
        with pytest.raises(ValueError, match='non-negative'):
            HealthSnapshot(consecutive_failures=-1)


class TestHealthThresholdsValidation:

    def test_inverted_latency_order_raises(self) -> None:
        with pytest.raises(ValueError, match='warn <= breach <= halt'):
            HealthThresholds(latency_warn_ms=500.0, latency_breach_ms=200.0)

    def test_negative_threshold_raises(self) -> None:
        with pytest.raises(ValueError, match='finite non-negative'):
            HealthThresholds(latency_warn_ms=-1.0)

    def test_bool_failure_threshold_raises(self) -> None:
        with pytest.raises(ValueError, match='non-negative int'):
            HealthThresholds(failure_warn=True)  # type: ignore[arg-type]
