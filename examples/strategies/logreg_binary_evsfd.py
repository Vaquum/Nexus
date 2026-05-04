'''Primitive long-only strategy driven by a binary classifier signal.

Tracks Nexus issue #33: enter when the binary signal is `1`, exit when
it turns to `0`, hold otherwise. Single concurrent position per
strategy.
'''

from __future__ import annotations

import logging
from decimal import Decimal

from nexus.core.domain.enums import OrderSide
from nexus.core.domain.order_types import ExecutionMode, OrderType
from nexus.infrastructure.praxis_connector.trade_outcome import TradeOutcome
from nexus.strategy import (
    Action,
    ActionType,
    Signal,
    Strategy,
    StrategyContext,
    StrategyParams,
)

_ENTER_CAPITAL_FRACTION = Decimal('0.10')
_EXECUTION_DEADLINE_SECONDS = 60

_log = logging.getLogger(__name__)


class Strategy(Strategy):
    '''Enter on `_preds == 1`, exit on `_preds == 0`. One position at a time.'''

    def on_save(self) -> bytes:
        return b''

    def on_load(self, data: bytes) -> None:
        return None

    def on_startup(
        self,
        params: StrategyParams,
        context: StrategyContext,
    ) -> list[Action]:
        return []

    def on_signal(
        self,
        signal: Signal,
        params: StrategyParams,
        context: StrategyContext,
    ) -> list[Action]:
        prediction = signal.get('_preds')

        if prediction is None:
            return []

        if isinstance(prediction, bool) or not isinstance(prediction, int) or prediction not in (0, 1):
            _log.warning(
                'logreg_binary_evsfd: invalid _preds payload (%r, type=%s); '
                'expected int 0 or 1; skipping signal — strategy will not '
                'trade until the signal source is fixed',
                prediction,
                type(prediction).__name__,
            )
            return []

        if prediction == 1 and not context.positions:
            return self._enter(signal, context)

        if prediction == 0 and context.positions:
            return self._exit(context)

        return []

    def on_outcome(
        self,
        outcome: TradeOutcome,
        params: StrategyParams,
        context: StrategyContext,
    ) -> list[Action]:
        return []

    def on_timer(
        self,
        timer_id: str,
        params: StrategyParams,
        context: StrategyContext,
    ) -> list[Action]:
        return []

    def on_shutdown(
        self,
        params: StrategyParams,
        context: StrategyContext,
    ) -> list[Action]:
        return []

    def _enter(self, signal: Signal, context: StrategyContext) -> list[Action]:
        '''Build a single ENTER action sized as a fixed fraction of available capital.

        Returns no action when `_reference_price` cannot derive a price
        from the signal payload (capital sizing requires a price up
        front; the validator's poller-fallback path is not exercised).
        '''

        reference_price = self._reference_price(signal)

        if reference_price is None or context.capital_available <= 0:
            return []

        if not reference_price.is_finite() or reference_price <= 0:
            _log.warning(
                'logreg_binary_evsfd: non-positive or non-finite reference_price (%s); '
                'skipping ENTER',
                reference_price,
            )
            return []

        notional = context.capital_available * _ENTER_CAPITAL_FRACTION
        size = notional / reference_price

        return [
            Action(
                action_type=ActionType.ENTER,
                direction=OrderSide.BUY,
                size=size,
                execution_mode=ExecutionMode.SINGLE_SHOT,
                order_type=OrderType.MARKET,
                deadline=_EXECUTION_DEADLINE_SECONDS,
                reference_price=reference_price,
            ),
        ]

    def _exit(self, context: StrategyContext) -> list[Action]:
        '''Exit the open position with a single SELL.'''

        position = context.positions[0]
        remaining = position.size - position.pending_exit

        if remaining <= 0:
            return []

        return [
            Action(
                action_type=ActionType.EXIT,
                direction=OrderSide.SELL,
                size=remaining,
                execution_mode=ExecutionMode.SINGLE_SHOT,
                order_type=OrderType.MARKET,
                deadline=_EXECUTION_DEADLINE_SECONDS,
                trade_id=position.trade_id,
            ),
        ]

    def _reference_price(self, signal: Signal) -> Decimal | None:
        '''Read `close` from the signal payload; returns None if absent or unparseable.

        `_enter` short-circuits to no action when this returns None, so
        the launcher's poller-fallback path is not exercised.
        '''

        candidate = signal.get('close')

        if candidate is None:
            return None

        try:
            return Decimal(str(candidate))
        except (TypeError, ArithmeticError, ValueError):
            return None
