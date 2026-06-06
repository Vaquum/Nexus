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

        Uses `quote_qty` (quote-asset spend) so the venue determines the
        executed base quantity from live liquidity. This eliminates the
        kline-staleness slippage gap that the qty-native path exposed:
        the reservation matches the spend cap exactly, with no
        reference-price multiplication.
        '''

        if context.capital_available <= 0:
            return []

        quote_qty = context.capital_available * _ENTER_CAPITAL_FRACTION

        if not quote_qty.is_finite() or quote_qty <= 0:
            _log.warning(
                'logreg_binary_evsfd: non-positive or non-finite quote_qty (%s); '
                'skipping ENTER',
                quote_qty,
            )
            return []

        return [
            Action(
                action_type=ActionType.ENTER,
                direction=OrderSide.BUY,
                quote_qty=quote_qty,
                execution_mode=ExecutionMode.SINGLE_SHOT,
                order_type=OrderType.MARKET,
                deadline=_EXECUTION_DEADLINE_SECONDS,
                reference_price=self._reference_price(signal),
            ),
        ]

    def _exit(self, context: StrategyContext) -> list[Action]:
        '''Exit every open position with `remaining > 0` via a SELL each.

        The strategy enforces a single concurrent position by gating ENTER
        on `not context.positions`, so the steady-state path produces
        exactly one EXIT action. Iterating defends against any unexpected
        multi-position state (crash-recovery residue, manifest config
        drift, future code changes) without introducing scope.
        '''

        actions: list[Action] = []

        for position in context.positions:
            remaining = position.size - position.pending_exit

            if remaining <= 0:
                continue

            actions.append(
                Action(
                    action_type=ActionType.EXIT,
                    direction=OrderSide.SELL,
                    size=remaining,
                    execution_mode=ExecutionMode.SINGLE_SHOT,
                    order_type=OrderType.MARKET,
                    deadline=_EXECUTION_DEADLINE_SECONDS,
                    trade_id=position.trade_id,
                ),
            )

        return actions

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
