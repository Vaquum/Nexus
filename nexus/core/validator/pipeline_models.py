'''Validator pipeline stage ordering and typed context/result models.

Defines the canonical six-stage validator order and immutable models used
to pass request context and final allow/deny decisions through the pipeline.
'''

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from nexus.core.capital_controller.reservation import Reservation
from nexus.core.domain.instance_state import InstanceState
from nexus.instance_config import InstanceConfig

__all__ = [
    'DEFAULT_VALIDATION_STAGE_ORDER',
    'ValidationDecision',
    'ValidationRequestContext',
    'ValidationStage',
]

_ZERO = Decimal(0)


class ValidationStage(Enum):
    '''Ordered validator stages for action preflight checks.'''

    INTAKE = 'INTAKE'
    RISK = 'RISK'
    PRICE = 'PRICE'
    CAPITAL = 'CAPITAL'
    HEALTH = 'HEALTH'
    GATEWAY = 'GATEWAY'


DEFAULT_VALIDATION_STAGE_ORDER: tuple[ValidationStage, ...] = (
    ValidationStage.INTAKE,
    ValidationStage.RISK,
    ValidationStage.PRICE,
    ValidationStage.CAPITAL,
    ValidationStage.HEALTH,
    ValidationStage.GATEWAY,
)


@dataclass(frozen=True)
class ValidationRequestContext:
    '''Immutable request context passed through validator stages.

    Args:
        strategy_id: Strategy requesting action validation.
        order_notional: Requested order notional (quote units).
        estimated_fees: Estimated fees for the action (quote units).
        strategy_budget: Current strategy budget ceiling (quote units).
        state: Current runtime instance state snapshot.
        config: Runtime instance configuration.
    '''

    strategy_id: str
    order_notional: Decimal
    estimated_fees: Decimal
    strategy_budget: Decimal
    state: InstanceState
    config: InstanceConfig

    def __post_init__(self) -> None:
        '''Validate context invariants at construction time.'''

        if not isinstance(self.strategy_id, str) or not self.strategy_id.strip():
            msg = 'ValidationRequestContext.strategy_id must be a non-empty string'
            raise ValueError(msg)

        for field_name in ('order_notional', 'estimated_fees', 'strategy_budget'):
            value = getattr(self, field_name)
            if not isinstance(value, Decimal) or not value.is_finite() or value < _ZERO:
                msg = (
                    f'ValidationRequestContext.{field_name} must be a finite '
                    'non-negative Decimal'
                )
                raise ValueError(msg)

        if not isinstance(self.state, InstanceState):
            msg = 'ValidationRequestContext.state must be an InstanceState instance'
            raise ValueError(msg)

        if not isinstance(self.config, InstanceConfig):
            msg = 'ValidationRequestContext.config must be an InstanceConfig instance'
            raise ValueError(msg)


@dataclass(frozen=True)
class ValidationDecision:
    '''Final allow/deny decision emitted by the validator pipeline.

    Args:
        allowed: Whether validation passed.
        failed_stage: Stage where validation failed when denied.
        reason_code: Machine-readable deny code when denied.
        message: Human-readable deny explanation when denied.
        reservation: Reservation returned by capital stage when allowed.
    '''

    allowed: bool
    failed_stage: ValidationStage | None = None
    reason_code: str | None = None
    message: str | None = None
    reservation: Reservation | None = None

    def __post_init__(self) -> None:
        '''Validate allow/deny field consistency.'''

        if self.allowed:
            if self.failed_stage is not None:
                msg = 'ValidationDecision: allowed=True must not set failed_stage'
                raise ValueError(msg)
            if self.reason_code is not None:
                msg = 'ValidationDecision: allowed=True must not set reason_code'
                raise ValueError(msg)
            if self.message is not None:
                msg = 'ValidationDecision: allowed=True must not set message'
                raise ValueError(msg)
            return

        if self.failed_stage is None:
            msg = 'ValidationDecision: allowed=False requires failed_stage'
            raise ValueError(msg)

        if not isinstance(self.failed_stage, ValidationStage):
            msg = 'ValidationDecision.failed_stage must be a ValidationStage member'
            raise ValueError(msg)

        if not isinstance(self.reason_code, str) or not self.reason_code.strip():
            msg = 'ValidationDecision: allowed=False requires non-empty reason_code'
            raise ValueError(msg)

        if not isinstance(self.message, str) or not self.message.strip():
            msg = 'ValidationDecision: allowed=False requires non-empty message'
            raise ValueError(msg)
