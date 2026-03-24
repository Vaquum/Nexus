'''Validator pipeline stage ordering and typed context/result models.

Defines the canonical six-stage validator order and immutable models used
to pass request context and final allow/deny decisions through the pipeline.
'''

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from nexus.core.capital_controller.reservation import Reservation
from nexus.core.domain.enums import OrderSide
from nexus.core.domain.instance_state import InstanceState
from nexus.instance_config import InstanceConfig

__all__ = [
    'DEFAULT_VALIDATION_STAGE_ORDER',
    'ValidationAction',
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
    PLATFORM_LIMITS = 'PLATFORM_LIMITS'


class ValidationAction(Enum):
    '''Strategy action types entering the validator pipeline.'''

    ENTER = 'ENTER'
    EXIT = 'EXIT'
    MODIFY = 'MODIFY'
    ABORT = 'ABORT'
    CANCEL = 'CANCEL'


DEFAULT_VALIDATION_STAGE_ORDER: tuple[ValidationStage, ...] = (
    ValidationStage.INTAKE,
    ValidationStage.RISK,
    ValidationStage.PRICE,
    ValidationStage.CAPITAL,
    ValidationStage.HEALTH,
    ValidationStage.PLATFORM_LIMITS,
)


@dataclass(frozen=True)
class ValidationRequestContext:
    '''Immutable request context passed through validator stages.

    Args:
        strategy_id: Strategy requesting action validation.
        action: Strategy action type entering validation.
        symbol: Trading symbol for this action.
        order_side: Direction for actions that carry side semantics.
        order_size: Base-asset size for actions that carry size semantics.
        current_order_size: Current base-asset size for edit (`MODIFY`) context.
        trade_id: Trade reference for actions targeting existing positions.
        command_id: Command reference for actions targeting existing commands.
        order_notional: Requested order notional (quote units).
        current_order_notional: Current command/order notional for edit
            (`MODIFY`) context (quote units).
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
    action: ValidationAction = ValidationAction.ENTER
    symbol: str = 'BTCUSDT'
    order_side: OrderSide | None = OrderSide.BUY
    order_size: Decimal | None = None
    current_order_size: Decimal | None = None
    trade_id: str | None = None
    command_id: str | None = 'cmd_default'
    current_order_notional: Decimal | None = None

    def __post_init__(self) -> None:
        '''Validate context invariants at construction time.'''

        if not isinstance(self.strategy_id, str) or not self.strategy_id.strip():
            msg = 'ValidationRequestContext.strategy_id must be a non-empty string'
            raise ValueError(msg)

        if not isinstance(self.action, ValidationAction):
            msg = 'ValidationRequestContext.action must be a ValidationAction member'
            raise ValueError(msg)

        if not isinstance(self.symbol, str) or not self.symbol.strip():
            msg = 'ValidationRequestContext.symbol must be a non-empty string'
            raise ValueError(msg)

        if self.order_side is not None and not isinstance(self.order_side, OrderSide):
            msg = 'ValidationRequestContext.order_side must be an OrderSide member or None'
            raise ValueError(msg)

        if self.order_size is not None and (
            not isinstance(self.order_size, Decimal)
            or not self.order_size.is_finite()
            or self.order_size < _ZERO
        ):
            msg = (
                'ValidationRequestContext.order_size must be a finite '
                'non-negative Decimal or None'
            )
            raise ValueError(msg)

        if self.current_order_size is not None and (
            not isinstance(self.current_order_size, Decimal)
            or not self.current_order_size.is_finite()
            or self.current_order_size < _ZERO
        ):
            msg = (
                'ValidationRequestContext.current_order_size must be a finite '
                'non-negative Decimal or None'
            )
            raise ValueError(msg)

        if self.trade_id is not None and (
            not isinstance(self.trade_id, str) or not self.trade_id.strip()
        ):
            msg = 'ValidationRequestContext.trade_id must be a non-empty string or None'
            raise ValueError(msg)

        if self.command_id is not None and (
            not isinstance(self.command_id, str) or not self.command_id.strip()
        ):
            msg = (
                'ValidationRequestContext.command_id must be a non-empty string or None'
            )
            raise ValueError(msg)

        if self.action == ValidationAction.ENTER and self.command_id is None:
            msg = (
                'ValidationRequestContext.command_id is required for '
                'ValidationAction.ENTER'
            )
            raise ValueError(msg)

        for field_name in ('order_notional', 'estimated_fees', 'strategy_budget'):
            value = getattr(self, field_name)
            if not isinstance(value, Decimal) or not value.is_finite() or value < _ZERO:
                msg = (
                    f'ValidationRequestContext.{field_name} must be a finite '
                    'non-negative Decimal'
                )
                raise ValueError(msg)

        if self.current_order_notional is not None and (
            not isinstance(self.current_order_notional, Decimal)
            or not self.current_order_notional.is_finite()
            or self.current_order_notional < _ZERO
        ):
            msg = (
                'ValidationRequestContext.current_order_notional must be a finite '
                'non-negative Decimal or None'
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

        if self.reservation is not None and not isinstance(
            self.reservation, Reservation
        ):
            msg = 'ValidationDecision.reservation must be Reservation or None'
            raise ValueError(msg)

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
