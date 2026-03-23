from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from nexus.core.validator.pipeline_models import (
    ValidationDecision,
    ValidationRequestContext,
    ValidationStage,
)

__all__ = [
    'PLATFORM_LIMITS_DAILY_LOSS_LIMIT_CODE',
    'PLATFORM_LIMITS_MAX_CAPITAL_UTILIZATION_LIMIT_CODE',
    'PLATFORM_LIMITS_MAX_ORDER_NOTIONAL_LIMIT_CODE',
    'PLATFORM_LIMITS_MAX_ORDER_RATE_LIMIT_CODE',
    'PLATFORM_LIMITS_MAX_POSITION_LIMIT_CODE',
    'PLATFORM_LIMITS_SNAPSHOT_MISSING_CODE',
    'PlatformLimitsStageLimits',
    'PlatformLimitsStageSnapshot',
    'validate_platform_limits_stage',
]

_ZERO_DECIMAL = Decimal(0)

PLATFORM_LIMITS_MAX_ORDER_NOTIONAL_LIMIT_CODE = (
    'PLATFORM_LIMITS_MAX_ORDER_NOTIONAL_LIMIT'
)
PLATFORM_LIMITS_MAX_ORDER_RATE_LIMIT_CODE = 'PLATFORM_LIMITS_MAX_ORDER_RATE_LIMIT'
PLATFORM_LIMITS_MAX_POSITION_LIMIT_CODE = 'PLATFORM_LIMITS_MAX_POSITION_LIMIT'
PLATFORM_LIMITS_DAILY_LOSS_LIMIT_CODE = 'PLATFORM_LIMITS_DAILY_LOSS_LIMIT'
PLATFORM_LIMITS_MAX_CAPITAL_UTILIZATION_LIMIT_CODE = (
    'PLATFORM_LIMITS_MAX_CAPITAL_UTILIZATION_LIMIT'
)
PLATFORM_LIMITS_SNAPSHOT_MISSING_CODE = 'PLATFORM_LIMITS_SNAPSHOT_MISSING'


@dataclass(frozen=True)
class PlatformLimitsStageLimits:
    max_order_notional: Decimal | None = None
    max_order_rate: Decimal | None = None
    max_position: Decimal | None = None
    max_daily_loss: Decimal | None = None
    max_capital_utilization: Decimal | None = None

    def __post_init__(self) -> None:
        for field_name in (
            'max_order_notional',
            'max_order_rate',
            'max_position',
            'max_daily_loss',
            'max_capital_utilization',
        ):
            value = getattr(self, field_name)
            if value is None:
                continue
            if (
                not isinstance(value, Decimal)
                or not value.is_finite()
                or value < _ZERO_DECIMAL
            ):
                msg = (
                    f'PlatformLimitsStageLimits.{field_name} must be a finite '
                    'non-negative Decimal or None'
                )
                raise ValueError(msg)

        if (
            self.max_capital_utilization is not None
            and self.max_capital_utilization > Decimal(1)
        ):
            msg = 'PlatformLimitsStageLimits.max_capital_utilization must be <= 1'
            raise ValueError(msg)


@dataclass(frozen=True)
class PlatformLimitsStageSnapshot:
    current_order_rate: Decimal | None = None
    projected_position: Decimal | None = None
    current_daily_loss: Decimal | None = None
    projected_capital_utilization: Decimal | None = None

    def __post_init__(self) -> None:
        for field_name in (
            'current_order_rate',
            'projected_position',
            'current_daily_loss',
            'projected_capital_utilization',
        ):
            value = getattr(self, field_name)
            if value is None:
                continue
            if (
                not isinstance(value, Decimal)
                or not value.is_finite()
                or value < _ZERO_DECIMAL
            ):
                msg = (
                    f'PlatformLimitsStageSnapshot.{field_name} must be a finite '
                    'non-negative Decimal or None'
                )
                raise ValueError(msg)

        if (
            self.projected_capital_utilization is not None
            and self.projected_capital_utilization > Decimal(1)
        ):
            msg = (
                'PlatformLimitsStageSnapshot.projected_capital_utilization must be <= 1'
            )
            raise ValueError(msg)


def validate_platform_limits_stage(
    context: ValidationRequestContext,
    limits: PlatformLimitsStageLimits,
    snapshot: PlatformLimitsStageSnapshot,
) -> ValidationDecision:
    def missing_decision(field_name: str) -> ValidationDecision:
        return ValidationDecision(
            allowed=False,
            failed_stage=ValidationStage.PLATFORM_LIMITS,
            reason_code=PLATFORM_LIMITS_SNAPSHOT_MISSING_CODE,
            message=f'platform_limits snapshot missing {field_name}',
        )

    decision: ValidationDecision | None = None

    if (
        limits.max_order_notional is not None
        and context.order_notional > limits.max_order_notional
    ):
        decision = ValidationDecision(
            allowed=False,
            failed_stage=ValidationStage.PLATFORM_LIMITS,
            reason_code=PLATFORM_LIMITS_MAX_ORDER_NOTIONAL_LIMIT_CODE,
            message=(
                f'order_notional {context.order_notional} exceeded '
                'platform_limits '
                f'max_order_notional {limits.max_order_notional}'
            ),
        )

    if limits.max_order_rate is not None and decision is None:
        current_order_rate = snapshot.current_order_rate
        if current_order_rate is None:
            decision = missing_decision('current_order_rate')
        elif current_order_rate > limits.max_order_rate:
            decision = ValidationDecision(
                allowed=False,
                failed_stage=ValidationStage.PLATFORM_LIMITS,
                reason_code=PLATFORM_LIMITS_MAX_ORDER_RATE_LIMIT_CODE,
                message=(
                    f'current_order_rate {current_order_rate} exceeded '
                    f'platform_limits max_order_rate {limits.max_order_rate}'
                ),
            )

    if limits.max_position is not None and decision is None:
        projected_position = snapshot.projected_position
        if projected_position is None:
            decision = missing_decision('projected_position')
        elif projected_position > limits.max_position:
            decision = ValidationDecision(
                allowed=False,
                failed_stage=ValidationStage.PLATFORM_LIMITS,
                reason_code=PLATFORM_LIMITS_MAX_POSITION_LIMIT_CODE,
                message=(
                    f'projected_position {projected_position} exceeded '
                    f'platform_limits max_position {limits.max_position}'
                ),
            )

    if limits.max_daily_loss is not None and decision is None:
        current_daily_loss = snapshot.current_daily_loss
        if current_daily_loss is None:
            decision = missing_decision('current_daily_loss')
        elif current_daily_loss > limits.max_daily_loss:
            decision = ValidationDecision(
                allowed=False,
                failed_stage=ValidationStage.PLATFORM_LIMITS,
                reason_code=PLATFORM_LIMITS_DAILY_LOSS_LIMIT_CODE,
                message=(
                    f'current_daily_loss {current_daily_loss} exceeded '
                    f'platform_limits max_daily_loss {limits.max_daily_loss}'
                ),
            )

    if limits.max_capital_utilization is not None and decision is None:
        projected_capital_utilization = snapshot.projected_capital_utilization
        if projected_capital_utilization is None:
            decision = missing_decision('projected_capital_utilization')
        elif projected_capital_utilization > limits.max_capital_utilization:
            decision = ValidationDecision(
                allowed=False,
                failed_stage=ValidationStage.PLATFORM_LIMITS,
                reason_code=PLATFORM_LIMITS_MAX_CAPITAL_UTILIZATION_LIMIT_CODE,
                message=(
                    'projected_capital_utilization '
                    f'{projected_capital_utilization} exceeded '
                    'platform_limits max_capital_utilization '
                    f'{limits.max_capital_utilization}'
                ),
            )

    if decision is not None:
        return decision

    return ValidationDecision(allowed=True)
