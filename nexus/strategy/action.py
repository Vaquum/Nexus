'''Action output from strategy callbacks.'''

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from types import MappingProxyType

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
        direction: BUY or SELL. Required for ENTER.
        size: Base asset quantity. Required for EXIT, and for ENTER
            when `quote_qty` is not set. Mutually exclusive with
            `quote_qty` on ENTER.
        quote_qty: Quote-asset spend (e.g. USDT). Optional alternative
            to `size` for ENTER MARKET BUY orders — the venue determines
            the resulting base quantity from live liquidity. Mutually
            exclusive with `size` on ENTER; must be unset for EXIT.
        execution_mode: How to execute (e.g. SingleShot). Required for ENTER;
            required for MODIFY to select the amend-parameter shape.
        order_type: Order type (e.g. Market, Limit). Required for ENTER.
        execution_params: Mode-specific parameters. Optional.
        modify_params: Mode-specific amend parameters, keyed by field
            name with absolute new values. Required for MODIFY.
        deadline: Timeout in seconds. Required for ENTER.
        trade_id: Existing trade reference. Required for EXIT.
        command_id: Existing command reference. Required for MODIFY, ABORT.
        maker_preference: Maker/taker preference. Optional.
        reference_price: Strategy reference price for slippage measurement. Optional.
    '''

    action_type: ActionType
    direction: OrderSide | None = None
    size: Decimal | None = None
    quote_qty: Decimal | None = None
    execution_mode: ExecutionMode | None = None
    order_type: OrderType | None = None
    execution_params: Mapping[str, object] | None = None
    modify_params: Mapping[str, object] | None = None
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

        if self.quote_qty is not None and (
            not isinstance(self.quote_qty, Decimal)
            or not self.quote_qty.is_finite()
            or self.quote_qty <= _ZERO
        ):
            msg = 'quote_qty must be a finite positive Decimal or None'
            raise ValueError(msg)

        if self.execution_mode is not None and not isinstance(
            self.execution_mode, ExecutionMode
        ):
            msg = 'execution_mode must be an ExecutionMode member or None'
            raise ValueError(msg)

        if self.order_type is not None and not isinstance(self.order_type, OrderType):
            msg = 'order_type must be an OrderType member or None'
            raise ValueError(msg)

        if self.execution_params is not None:
            if not isinstance(self.execution_params, Mapping):
                msg = 'execution_params must be a Mapping or None'
                raise ValueError(msg)
            object.__setattr__(
                self,
                'execution_params',
                MappingProxyType(dict(self.execution_params)),
            )

        if self.modify_params is not None:
            if not isinstance(self.modify_params, Mapping):
                msg = 'modify_params must be a Mapping or None'
                raise ValueError(msg)
            if not self.modify_params:
                msg = 'modify_params must not be empty'
                raise ValueError(msg)
            object.__setattr__(
                self,
                'modify_params',
                MappingProxyType(dict(self.modify_params)),
            )

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
            if self.size is None and self.quote_qty is None:
                missing.append('size or quote_qty')
            if self.execution_mode is None:
                missing.append('execution_mode')
            if self.order_type is None:
                missing.append('order_type')
            if self.deadline is None:
                missing.append('deadline')
            if missing:
                msg = f"ENTER requires: {', '.join(missing)}"
                raise ValueError(msg)

            if self.size is not None and self.quote_qty is not None:
                msg = 'ENTER requires exactly one of size or quote_qty, not both'
                raise ValueError(msg)

            if self.quote_qty is not None:

                assert self.direction is not None
                assert self.order_type is not None

                if self.direction != OrderSide.BUY:
                    msg = (
                        f'ENTER with quote_qty is only valid for BUY, '
                        f'got direction={self.direction.value}'
                    )
                    raise ValueError(msg)

                if self.order_type != OrderType.MARKET:
                    msg = (
                        f'ENTER with quote_qty is only valid for MARKET, '
                        f'got order_type={self.order_type.value}'
                    )
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

            if self.quote_qty is not None:
                msg = 'EXIT must not set quote_qty (use size)'
                raise ValueError(msg)

        elif self.action_type == ActionType.MODIFY:
            missing = []
            if self.command_id is None:
                missing.append('command_id')
            if self.execution_mode is None:
                missing.append('execution_mode')
            if self.modify_params is None:
                missing.append('modify_params')
            if missing:
                msg = f"MODIFY requires: {', '.join(missing)}"
                raise ValueError(msg)

        elif self.action_type == ActionType.ABORT:
            if self.command_id is None:
                msg = 'ABORT requires: command_id'
                raise ValueError(msg)
