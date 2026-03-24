'''Price-stage contract and placeholder validation checks.'''

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from nexus.core.validator.pipeline_models import (
    ValidationDecision,
    ValidationRequestContext,
    ValidationStage,
)

__all__ = [
    'PriceCheckSnapshot',
    'PriceFailureConsequence',
    'PriceStageLimits',
    'derive_price_failure_consequence',
    'validate_price_stage',
]

_ZERO_DECIMAL = Decimal(0)


@dataclass(frozen=True)
class PriceCheckSnapshot:
    '''Market-data snapshot consumed by price-stage checks.'''

    now_ms: int | None = None
    book_timestamp_ms: int | None = None
    spread_bps: Decimal | None = None
    deviation_bps: Decimal | None = None

    def __post_init__(self) -> None:
        '''Validate snapshot field invariants.'''

        if self.now_ms is not None and (
            isinstance(self.now_ms, bool)
            or not isinstance(self.now_ms, int)
            or self.now_ms < 0
        ):
            msg = 'PriceCheckSnapshot.now_ms must be a non-negative int or None'
            raise ValueError(msg)

        if self.book_timestamp_ms is not None and (
            isinstance(self.book_timestamp_ms, bool)
            or not isinstance(self.book_timestamp_ms, int)
            or self.book_timestamp_ms < 0
        ):
            msg = 'PriceCheckSnapshot.book_timestamp_ms must be a non-negative int or None'
            raise ValueError(msg)

        for field_name in ('spread_bps', 'deviation_bps'):
            value = getattr(self, field_name)
            if value is None:
                continue
            if (
                not isinstance(value, Decimal)
                or not value.is_finite()
                or value < _ZERO_DECIMAL
            ):
                msg = (
                    f'PriceCheckSnapshot.{field_name} must be a finite '
                    'non-negative Decimal or None'
                )
                raise ValueError(msg)


@dataclass(frozen=True)
class PriceStageLimits:
    '''Optional thresholds for price-stage placeholder checks.'''

    max_staleness_ms: int | None = None
    max_spread_bps: Decimal | None = None
    max_deviation_bps: Decimal | None = None

    def __post_init__(self) -> None:
        '''Validate configured price-stage thresholds.'''

        if self.max_staleness_ms is not None and (
            isinstance(self.max_staleness_ms, bool)
            or not isinstance(self.max_staleness_ms, int)
            or self.max_staleness_ms < 0
        ):
            msg = 'PriceStageLimits.max_staleness_ms must be a non-negative int or None'
            raise ValueError(msg)

        for field_name in ('max_spread_bps', 'max_deviation_bps'):
            value = getattr(self, field_name)
            if value is None:
                continue
            if (
                not isinstance(value, Decimal)
                or not value.is_finite()
                or value < _ZERO_DECIMAL
            ):
                msg = (
                    f'PriceStageLimits.{field_name} must be a finite '
                    'non-negative Decimal or None'
                )
                raise ValueError(msg)


@dataclass(frozen=True)
class PriceFailureConsequence:
    '''Notification-routing consequence for denied price-stage decisions.'''

    notify_strategy_owner: bool
    notify_platform_ops: bool
    severity: str


def derive_price_failure_consequence(
    decision: ValidationDecision,
) -> PriceFailureConsequence | None:
    '''Map denied price-stage decisions to notification-routing consequences.'''

    if decision.allowed:
        return None

    if decision.failed_stage != ValidationStage.PRICE:
        return None

    reason_code = decision.reason_code
    if reason_code in (
        'PRICE_SYSTEM_DATA_UNAVAILABLE',
        'PRICE_SNAPSHOT_INVALID',
        'PRICE_BOOK_STALE',
    ):
        return PriceFailureConsequence(
            notify_strategy_owner=True,
            notify_platform_ops=True,
            severity='critical',
        )

    if reason_code in ('PRICE_SPREAD_LIMIT', 'PRICE_DEVIATION_LIMIT'):
        return PriceFailureConsequence(
            notify_strategy_owner=True,
            notify_platform_ops=False,
            severity='warning',
        )

    return PriceFailureConsequence(
        notify_strategy_owner=True,
        notify_platform_ops=True,
        severity='error',
    )


def validate_price_stage(
    context: ValidationRequestContext,
    limits: PriceStageLimits,
    snapshot: PriceCheckSnapshot | None,
) -> ValidationDecision:
    '''Validate price stage using optional snapshot and configured limits.'''

    _ = context
    decision: ValidationDecision | None = None

    if limits.max_staleness_ms is not None:
        if (
            snapshot is None
            or snapshot.now_ms is None
            or snapshot.book_timestamp_ms is None
        ):
            decision = ValidationDecision(
                allowed=False,
                failed_stage=ValidationStage.PRICE,
                reason_code='PRICE_SYSTEM_DATA_UNAVAILABLE',
                message=(
                    'Price system data unavailable: now_ms/book_timestamp_ms '
                    'missing for staleness validation'
                ),
            )
        elif snapshot.now_ms < snapshot.book_timestamp_ms:
            decision = ValidationDecision(
                allowed=False,
                failed_stage=ValidationStage.PRICE,
                reason_code='PRICE_SNAPSHOT_INVALID',
                message='Price snapshot timestamps are inconsistent',
            )
        else:
            staleness_ms = snapshot.now_ms - snapshot.book_timestamp_ms
            if staleness_ms > limits.max_staleness_ms:
                decision = ValidationDecision(
                    allowed=False,
                    failed_stage=ValidationStage.PRICE,
                    reason_code='PRICE_BOOK_STALE',
                    message=(
                        f'book staleness {staleness_ms}ms exceeded '
                        f'limit {limits.max_staleness_ms}ms'
                    ),
                )

    if limits.max_spread_bps is not None and decision is None:
        if snapshot is None or snapshot.spread_bps is None:
            decision = ValidationDecision(
                allowed=False,
                failed_stage=ValidationStage.PRICE,
                reason_code='PRICE_SYSTEM_DATA_UNAVAILABLE',
                message='Price system data unavailable: spread_bps missing',
            )
        elif snapshot.spread_bps > limits.max_spread_bps:
            decision = ValidationDecision(
                allowed=False,
                failed_stage=ValidationStage.PRICE,
                reason_code='PRICE_SPREAD_LIMIT',
                message=(
                    f'spread {snapshot.spread_bps}bps exceeded '
                    f'limit {limits.max_spread_bps}bps'
                ),
            )

    if limits.max_deviation_bps is not None and decision is None:
        if snapshot is None or snapshot.deviation_bps is None:
            decision = ValidationDecision(
                allowed=False,
                failed_stage=ValidationStage.PRICE,
                reason_code='PRICE_SYSTEM_DATA_UNAVAILABLE',
                message='Price system data unavailable: deviation_bps missing',
            )
        elif snapshot.deviation_bps > limits.max_deviation_bps:
            decision = ValidationDecision(
                allowed=False,
                failed_stage=ValidationStage.PRICE,
                reason_code='PRICE_DEVIATION_LIMIT',
                message=(
                    f'deviation {snapshot.deviation_bps}bps exceeded '
                    f'limit {limits.max_deviation_bps}bps'
                ),
            )

    if decision is not None:
        return decision

    return ValidationDecision(allowed=True)
