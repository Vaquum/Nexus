'''Action output from strategy callbacks.'''

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from nexus.core.domain.enums import OrderSide
from nexus.core.domain.order_types import ExecutionMode, MakerPreference, OrderType

__all__ = ['Action', 'ActionType']

_ZERO = Decimal(0)


class ActionType(Enum):
    '''Type of action a strategy can request.

    Args:
        ENTER: Request to enter a new position.
        EXIT: Request to exit an existing position.
        MODIFY: Request to modify an existing position or order.
        ABORT: Request to abort a pending action.
    '''

    ENTER = 'enter'
    EXIT = 'exit'
    MODIFY = 'modify'
    ABORT = 'abort'


@dataclass(frozen=True)
class Action:
    '''Action output from a strategy callback.

    Args:
        action_type: Type of action requested.
        direction: BUY or SELL. Required for ENTER and EXIT.
        size: Base asset quantity. Required for ENTER and EXIT.
        execution_mode: How to execute (e.g. SingleShot). Required for ENTER.
        order_type: Order type (e.g. Market, Limit). Required for ENTER.
        execution_params: Mode-specific parameters. Optional.
        deadline: Timeout in seconds. Required for ENTER.
        trade_id: Existing trade reference. Required for EXIT.
        command_id: Existing command reference. Required for MODIFY, ABORT.
        maker_preference: Maker/taker preference. Optional.
        reference_price: Strategy reference price for slippage measurement. Optional.
    '''

    action_type: ActionType
    direction: OrderSide | None = None
    size: Decimal | None = None
    execution_mode: ExecutionMode | None = None
    order_type: OrderType | None = None
    execution_params: dict[str, object] | None = None
    deadline: int | None = None
    trade_id: str | None = None
    command_id: str | None = None
    maker_preference: MakerPreference | None = None
    reference_price: Decimal | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.action_type, ActionType):
            msg = 'action_type must be an ActionType'
            raise ValueError(msg)

        if self.direction is not None and not isinstance(self.direction, OrderSide):
            msg = 'direction must be an OrderSide member or None'
            raise ValueError(msg)

        if self.size is not None and (
            not isinstance(self.size, Decimal)
            or not self.size.is_finite()
            or self.size <= _ZERO
        ):
            msg = 'size must be a finite positive Decimal or None'
            raise ValueError(msg)

        if self.execution_mode is not None and not isinstance(
            self.execution_mode, ExecutionMode
        ):
            msg = 'execution_mode must be an ExecutionMode member or None'
            raise ValueError(msg)

        if self.order_type is not None and not isinstance(self.order_type, OrderType):
            msg = 'order_type must be an OrderType member or None'
            raise ValueError(msg)

        if self.execution_params is not None and not isinstance(
            self.execution_params, dict
        ):
            msg = 'execution_params must be a dict or None'
            raise ValueError(msg)

        if self.deadline is not None and (
            isinstance(self.deadline, bool)
            or not isinstance(self.deadline, int)
            or self.deadline <= 0
        ):
            msg = 'deadline must be a positive int or None'
            raise ValueError(msg)

        if self.trade_id is not None and (
            not isinstance(self.trade_id, str)
            or not self.trade_id.strip()
        ):
            msg = 'trade_id must be a non-empty string or None'
            raise ValueError(msg)

        if self.command_id is not None and (
            not isinstance(self.command_id, str)
            or not self.command_id.strip()
        ):
            msg = 'command_id must be a non-empty string or None'
            raise ValueError(msg)

        if self.maker_preference is not None and not isinstance(
            self.maker_preference, MakerPreference
        ):
            msg = 'maker_preference must be a MakerPreference member or None'
            raise ValueError(msg)

        if self.reference_price is not None and (
            not isinstance(self.reference_price, Decimal)
            or not self.reference_price.is_finite()
            or self.reference_price <= _ZERO
        ):
            msg = 'reference_price must be a finite positive Decimal or None'
            raise ValueError(msg)

        self._validate_action_type_requirements()

    def _validate_action_type_requirements(self) -> None:
        '''Validate field requirements per action_type.'''

        if self.action_type == ActionType.ENTER:
            missing = []
            if self.direction is None:
                missing.append('direction')
            if self.size is None:
                missing.append('size')
            if self.execution_mode is None:
                missing.append('execution_mode')
            if self.order_type is None:
                missing.append('order_type')
            if self.deadline is None:
                missing.append('deadline')
            if missing:
                msg = f"ENTER requires: {', '.join(missing)}"
                raise ValueError(msg)

        elif self.action_type == ActionType.EXIT:
            missing = []
            if self.trade_id is None:
                missing.append('trade_id')
            if self.size is None:
                missing.append('size')
            if missing:
                msg = f"EXIT requires: {', '.join(missing)}"
                raise ValueError(msg)

        elif self.action_type == ActionType.MODIFY:
            if self.command_id is None:
                msg = 'MODIFY requires: command_id'
                raise ValueError(msg)

        elif self.action_type == ActionType.ABORT:
            if self.command_id is None:
                msg = 'ABORT requires: command_id'
                raise ValueError(msg)
