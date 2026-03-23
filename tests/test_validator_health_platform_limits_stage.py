from __future__ import annotations

from decimal import Decimal

import pytest

from nexus.core.domain.instance_state import InstanceState
from nexus.core.validator import (
    HEALTH_CONSECUTIVE_FAILURES_BREACH_CODE,
    HEALTH_CONSECUTIVE_FAILURES_HALT_CODE,
    HEALTH_LATENCY_BREACH_CODE,
    HEALTH_LATENCY_HALT_CODE,
    HEALTH_RATE_LIMIT_HEADROOM_BREACH_CODE,
    HealthMetricThresholds,
    HealthStagePolicy,
    HealthStageSnapshot,
    PLATFORM_LIMITS_DAILY_LOSS_LIMIT_CODE,
    PLATFORM_LIMITS_MAX_CAPITAL_UTILIZATION_LIMIT_CODE,
    PLATFORM_LIMITS_MAX_ORDER_NOTIONAL_LIMIT_CODE,
    PLATFORM_LIMITS_MAX_ORDER_RATE_LIMIT_CODE,
    PLATFORM_LIMITS_MAX_POSITION_LIMIT_CODE,
    PLATFORM_LIMITS_SNAPSHOT_MISSING_CODE,
    PlatformLimitsStageLimits,
    PlatformLimitsStageSnapshot,
    ValidationRequestContext,
    ValidationStage,
    evaluate_health_status,
    validate_health_stage,
    validate_platform_limits_stage,
)
from nexus.instance_config import InstanceConfig


def _make_context() -> ValidationRequestContext:
    config = InstanceConfig(
        account_id='acc_001',
        venue='binance_spot',
        allocated_capital=Decimal('10000'),
    )
    return ValidationRequestContext(
        strategy_id='strat_a',
        command_id='cmd_health_1',
        order_notional=Decimal('100'),
        estimated_fees=Decimal('1'),
        strategy_budget=Decimal('5000'),
        state=InstanceState.from_config(config),
        config=config,
    )


class TestHealthThresholds:
    def test_rejects_non_monotonic_higher_is_worse_policy(self) -> None:
        with pytest.raises(ValueError, match='warn <= breach'):
            HealthStagePolicy(
                latency_ms=HealthMetricThresholds(
                    warn=Decimal('12'),
                    breach=Decimal('10'),
                )
            )

    def test_accepts_descending_headroom_thresholds(self) -> None:
        policy = HealthStagePolicy(
            rate_limit_headroom=HealthMetricThresholds(
                warn=Decimal('0.20'),
                breach=Decimal('0.10'),
                halt=Decimal('0.05'),
            )
        )

        assert policy.rate_limit_headroom.warn == Decimal('0.20')

    def test_rejects_non_monotonic_lower_is_worse_policy(self) -> None:
        with pytest.raises(ValueError, match='warn >= breach'):
            HealthStagePolicy(
                rate_limit_headroom=HealthMetricThresholds(
                    warn=Decimal('0.05'),
                    breach=Decimal('0.10'),
                )
            )

    def test_rejects_failure_rate_over_one(self) -> None:
        with pytest.raises(ValueError, match='between 0 and 1'):
            HealthStagePolicy(
                failure_rate=HealthMetricThresholds(breach=Decimal('1.1')),
            )


class TestEvaluateHealthStatus:
    def test_returns_none_when_below_warn(self) -> None:
        level, code, message = evaluate_health_status(
            HealthStageSnapshot(
                latency_ms=Decimal('100'),
                consecutive_failures=Decimal('0'),
                failure_rate=Decimal('0.01'),
                rate_limit_headroom=Decimal('0.60'),
                clock_drift_ms=Decimal('50'),
            ),
            HealthStagePolicy(
                latency_ms=HealthMetricThresholds(
                    warn=Decimal('300'),
                    breach=Decimal('600'),
                    halt=Decimal('1000'),
                ),
            ),
        )

        assert level.value == 'NONE'
        assert code is None
        assert message is None

    def test_returns_warn_without_deny_code(self) -> None:
        level, code, message = evaluate_health_status(
            HealthStageSnapshot(
                latency_ms=Decimal('350'),
                consecutive_failures=Decimal('0'),
                failure_rate=Decimal('0.01'),
                rate_limit_headroom=Decimal('0.60'),
                clock_drift_ms=Decimal('50'),
            ),
            HealthStagePolicy(
                latency_ms=HealthMetricThresholds(
                    warn=Decimal('300'),
                    breach=Decimal('600'),
                    halt=Decimal('1000'),
                ),
            ),
        )

        assert level.value == 'WARN'
        assert code is None
        assert message is not None

    def test_returns_halt_when_later_metric_is_halt(self) -> None:
        level, code, message = evaluate_health_status(
            HealthStageSnapshot(
                latency_ms=Decimal('700'),
                consecutive_failures=Decimal('10'),
                failure_rate=Decimal('0.01'),
                rate_limit_headroom=Decimal('0.60'),
                clock_drift_ms=Decimal('50'),
            ),
            HealthStagePolicy(
                latency_ms=HealthMetricThresholds(
                    breach=Decimal('600'),
                    halt=Decimal('1000'),
                ),
                consecutive_failures=HealthMetricThresholds(
                    breach=Decimal('5'),
                    halt=Decimal('10'),
                ),
            ),
        )

        assert level.value == 'HALT'
        assert code == HEALTH_CONSECUTIVE_FAILURES_HALT_CODE
        assert message is not None

    def test_returns_breach_for_low_headroom(self) -> None:
        level, code, message = evaluate_health_status(
            HealthStageSnapshot(
                latency_ms=Decimal('100'),
                consecutive_failures=Decimal('0'),
                failure_rate=Decimal('0.01'),
                rate_limit_headroom=Decimal('0.07'),
                clock_drift_ms=Decimal('50'),
            ),
            HealthStagePolicy(
                rate_limit_headroom=HealthMetricThresholds(
                    warn=Decimal('0.20'),
                    breach=Decimal('0.10'),
                    halt=Decimal('0.05'),
                ),
            ),
        )

        assert level.value == 'BREACH'
        assert code == HEALTH_RATE_LIMIT_HEADROOM_BREACH_CODE
        assert message is not None


class TestValidateHealthStage:
    def test_denies_on_breach(self) -> None:
        decision = validate_health_stage(
            _make_context(),
            HealthStageSnapshot(
                latency_ms=Decimal('650'),
                consecutive_failures=Decimal('0'),
                failure_rate=Decimal('0.01'),
                rate_limit_headroom=Decimal('0.60'),
                clock_drift_ms=Decimal('50'),
            ),
            HealthStagePolicy(
                latency_ms=HealthMetricThresholds(
                    warn=Decimal('300'),
                    breach=Decimal('600'),
                    halt=Decimal('1000'),
                ),
            ),
        )

        assert decision.allowed is False
        assert decision.failed_stage == ValidationStage.HEALTH
        assert decision.reason_code == HEALTH_LATENCY_BREACH_CODE

    def test_denies_on_halt(self) -> None:
        decision = validate_health_stage(
            _make_context(),
            HealthStageSnapshot(
                latency_ms=Decimal('2000'),
                consecutive_failures=Decimal('0'),
                failure_rate=Decimal('0.01'),
                rate_limit_headroom=Decimal('0.60'),
                clock_drift_ms=Decimal('50'),
            ),
            HealthStagePolicy(
                latency_ms=HealthMetricThresholds(
                    warn=Decimal('300'),
                    breach=Decimal('600'),
                    halt=Decimal('1000'),
                ),
            ),
        )

        assert decision.allowed is False
        assert decision.failed_stage == ValidationStage.HEALTH
        assert decision.reason_code == HEALTH_LATENCY_HALT_CODE

    def test_allows_on_warn(self) -> None:
        decision = validate_health_stage(
            _make_context(),
            HealthStageSnapshot(
                latency_ms=Decimal('350'),
                consecutive_failures=Decimal('0'),
                failure_rate=Decimal('0.01'),
                rate_limit_headroom=Decimal('0.60'),
                clock_drift_ms=Decimal('50'),
            ),
            HealthStagePolicy(
                latency_ms=HealthMetricThresholds(
                    warn=Decimal('300'),
                    breach=Decimal('600'),
                    halt=Decimal('1000'),
                ),
            ),
        )

        assert decision.allowed is True

    def test_denies_on_consecutive_failures(self) -> None:
        decision = validate_health_stage(
            _make_context(),
            HealthStageSnapshot(
                latency_ms=Decimal('100'),
                consecutive_failures=Decimal('6'),
                failure_rate=Decimal('0.01'),
                rate_limit_headroom=Decimal('0.60'),
                clock_drift_ms=Decimal('50'),
            ),
            HealthStagePolicy(
                consecutive_failures=HealthMetricThresholds(
                    warn=Decimal('2'),
                    breach=Decimal('5'),
                    halt=Decimal('10'),
                ),
            ),
        )

        assert decision.allowed is False
        assert decision.reason_code == HEALTH_CONSECUTIVE_FAILURES_BREACH_CODE

    def test_halts_on_consecutive_failures(self) -> None:
        decision = validate_health_stage(
            _make_context(),
            HealthStageSnapshot(
                latency_ms=Decimal('100'),
                consecutive_failures=Decimal('10'),
                failure_rate=Decimal('0.01'),
                rate_limit_headroom=Decimal('0.60'),
                clock_drift_ms=Decimal('50'),
            ),
            HealthStagePolicy(
                consecutive_failures=HealthMetricThresholds(
                    warn=Decimal('2'),
                    breach=Decimal('5'),
                    halt=Decimal('10'),
                ),
            ),
        )

        assert decision.allowed is False
        assert decision.reason_code == HEALTH_CONSECUTIVE_FAILURES_HALT_CODE


class TestPlatformLimitsStage:
    def test_allows_when_under_limits(self) -> None:
        decision = validate_platform_limits_stage(
            _make_context(),
            PlatformLimitsStageLimits(
                max_order_notional=Decimal('200'),
                max_order_rate=Decimal('10'),
                max_position=Decimal('2'),
                max_daily_loss=Decimal('500'),
                max_capital_utilization=Decimal('0.9'),
            ),
            PlatformLimitsStageSnapshot(
                current_order_rate=Decimal('2'),
                projected_position=Decimal('1'),
                current_daily_loss=Decimal('100'),
                projected_capital_utilization=Decimal('0.5'),
            ),
        )

        assert decision.allowed is True

    def test_denies_on_max_order_notional(self) -> None:
        decision = validate_platform_limits_stage(
            _make_context(),
            PlatformLimitsStageLimits(max_order_notional=Decimal('50')),
            PlatformLimitsStageSnapshot(),
        )

        assert decision.allowed is False
        assert decision.failed_stage == ValidationStage.PLATFORM_LIMITS
        assert decision.reason_code == PLATFORM_LIMITS_MAX_ORDER_NOTIONAL_LIMIT_CODE

    def test_denies_when_required_snapshot_field_missing(self) -> None:
        decision = validate_platform_limits_stage(
            _make_context(),
            PlatformLimitsStageLimits(max_order_rate=Decimal('2')),
            PlatformLimitsStageSnapshot(),
        )

        assert decision.allowed is False
        assert decision.reason_code == PLATFORM_LIMITS_SNAPSHOT_MISSING_CODE

    def test_denies_on_max_order_rate(self) -> None:
        decision = validate_platform_limits_stage(
            _make_context(),
            PlatformLimitsStageLimits(max_order_rate=Decimal('2')),
            PlatformLimitsStageSnapshot(current_order_rate=Decimal('3')),
        )

        assert decision.allowed is False
        assert decision.reason_code == PLATFORM_LIMITS_MAX_ORDER_RATE_LIMIT_CODE

    def test_denies_on_max_position(self) -> None:
        decision = validate_platform_limits_stage(
            _make_context(),
            PlatformLimitsStageLimits(max_position=Decimal('1')),
            PlatformLimitsStageSnapshot(projected_position=Decimal('1.2')),
        )

        assert decision.allowed is False
        assert decision.reason_code == PLATFORM_LIMITS_MAX_POSITION_LIMIT_CODE

    def test_denies_on_max_daily_loss(self) -> None:
        decision = validate_platform_limits_stage(
            _make_context(),
            PlatformLimitsStageLimits(max_daily_loss=Decimal('100')),
            PlatformLimitsStageSnapshot(current_daily_loss=Decimal('120')),
        )

        assert decision.allowed is False
        assert decision.reason_code == PLATFORM_LIMITS_DAILY_LOSS_LIMIT_CODE

    def test_denies_on_max_capital_utilization(self) -> None:
        decision = validate_platform_limits_stage(
            _make_context(),
            PlatformLimitsStageLimits(max_capital_utilization=Decimal('0.7')),
            PlatformLimitsStageSnapshot(projected_capital_utilization=Decimal('0.8')),
        )

        assert decision.allowed is False
        assert (
            decision.reason_code == PLATFORM_LIMITS_MAX_CAPITAL_UTILIZATION_LIMIT_CODE
        )

    def test_identical_snapshot_produces_deterministic_denial_reason(self) -> None:
        limits = PlatformLimitsStageLimits(max_order_rate=Decimal('2'))
        snapshot = PlatformLimitsStageSnapshot(current_order_rate=Decimal('3'))

        decision_a = validate_platform_limits_stage(_make_context(), limits, snapshot)
        decision_b = validate_platform_limits_stage(_make_context(), limits, snapshot)

        assert decision_a.allowed is False
        assert decision_b.allowed is False
        assert decision_a.failed_stage == ValidationStage.PLATFORM_LIMITS
        assert decision_b.failed_stage == ValidationStage.PLATFORM_LIMITS
        assert decision_a.reason_code == PLATFORM_LIMITS_MAX_ORDER_RATE_LIMIT_CODE
        assert decision_b.reason_code == PLATFORM_LIMITS_MAX_ORDER_RATE_LIMIT_CODE
        assert decision_a.message == decision_b.message
