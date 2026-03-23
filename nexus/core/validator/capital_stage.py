'''Capital-stage adapter to CapitalController check-and-reserve semantics.'''

from __future__ import annotations

from nexus.core.capital_controller.capital_controller import CapitalController
from nexus.core.validator.pipeline_models import (
    ValidationDecision,
    ValidationRequestContext,
    ValidationStage,
)

__all__ = ['CAPITAL_RESERVATION_DENIED_CODE', 'validate_capital_stage']

CAPITAL_RESERVATION_DENIED_CODE = 'CAPITAL_RESERVATION_DENIED'


def validate_capital_stage(
    context: ValidationRequestContext,
    capital_controller: CapitalController,
    *,
    ttl_seconds: int | None = None,
) -> ValidationDecision:
    '''Validate capital stage by delegating to CapitalController reservation checks.'''

    kwargs: dict[str, int] = {}
    if ttl_seconds is not None:
        kwargs['ttl_seconds'] = ttl_seconds

    result = capital_controller.check_and_reserve(
        strategy_id=context.strategy_id,
        order_notional=context.order_notional,
        estimated_fees=context.estimated_fees,
        strategy_budget=context.strategy_budget,
        **kwargs,
    )

    if result.granted:
        return ValidationDecision(allowed=True, reservation=result.reservation)

    message = result.denial_reason
    if not message or not message.strip():
        message = 'Capital reservation denied by capital controller'

    return ValidationDecision(
        allowed=False,
        failed_stage=ValidationStage.CAPITAL,
        reason_code=CAPITAL_RESERVATION_DENIED_CODE,
        message=message,
    )
