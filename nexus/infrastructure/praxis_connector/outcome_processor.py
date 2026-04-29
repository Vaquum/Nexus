'''OutcomeProcessor for routing TradeOutcomes to Capital Controller.

Routes inbound TradeOutcomes to appropriate Capital Controller lifecycle
methods and updates positions on fill outcomes.
'''

from __future__ import annotations

import logging
from decimal import Decimal

from nexus.core.capital_controller.capital_controller import CapitalController
from nexus.core.domain.enums import OrderSide
from nexus.core.domain.instance_state import InstanceState
from nexus.core.domain.risk_state import StrategyRiskState
from nexus.infrastructure.praxis_connector.order_context import OrderContext
from nexus.infrastructure.praxis_connector.process_result import ProcessResult
from nexus.infrastructure.praxis_connector.trade_outcome import TradeOutcome
from nexus.infrastructure.praxis_connector.trade_outcome_type import TradeOutcomeType
from nexus.infrastructure.state_store import StateStore
from nexus.infrastructure.strategy_event import StrategyEvent

__all__ = ['OutcomeProcessor']

_log = logging.getLogger(__name__)

_ZERO = Decimal(0)


class OutcomeProcessor:
    '''Routes TradeOutcomes to Capital Controller and updates positions.

    Args:
        capital_controller: Capital lifecycle manager.
        instance_state: Runtime state containing positions.
        state_store: Persistence facade for WAL and snapshots.
    '''

    def __init__(
        self,
        capital_controller: CapitalController,
        instance_state: InstanceState,
        state_store: StateStore,
    ) -> None:
        self._capital = capital_controller
        self._state = instance_state
        self._store = state_store

    def process(
        self,
        outcome: TradeOutcome,
        context: OrderContext,
    ) -> ProcessResult:
        '''Process a TradeOutcome and update state accordingly.

        Contract — `outcome.command_id` ↔ `CapitalController.send_order`:
            Every ACK / FILL / REJECT / CANCEL handled here looks up
            `self._capital._orders[outcome.command_id]`. That dict is
            populated only by `CapitalController.send_order(reservation_id,
            order_id)`, where `order_id == outcome.command_id`. The
            launcher is responsible for calling `send_order` for every
            SUBMITTED action (use `nexus.strategy.action_submit.bridge_to_capital`
            to centralise the wiring). Renaming `outcome.command_id`
            without renaming `send_order(order_id=...)` (or vice
            versa) silently breaks the round trip — every outcome
            returns `INVARIANT_BREACH: order not found` and capital
            stays parked in `reservation_notional`.

        Args:
            outcome: Inbound outcome from Trading sub-system.
            context: Metadata for outcome processing.

        Returns:
            ProcessResult indicating success and what was updated.
        '''

        if context.command_id != outcome.command_id:
            return ProcessResult(
                success=False,
                outcome_type=outcome.outcome_type,
                error_reason=(
                    f'context.command_id {context.command_id!r} does not match '
                    f'outcome.command_id {outcome.command_id!r}'
                ),
            )

        if outcome.outcome_type == TradeOutcomeType.ACK:
            return self._handle_ack(outcome)

        if outcome.outcome_type in (TradeOutcomeType.PARTIAL, TradeOutcomeType.FILLED):
            return self._handle_fill(outcome, context)

        if outcome.outcome_type == TradeOutcomeType.REJECTED:
            return self._handle_reject(outcome, context)

        return self._handle_cancel(outcome, context)

    def _handle_ack(self, outcome: TradeOutcome) -> ProcessResult:
        result = self._capital.order_ack(outcome.command_id)

        if not result.success:
            return ProcessResult(
                success=False,
                outcome_type=outcome.outcome_type,
                error_reason=f'order_ack failed: {result.reason}',
            )

        return ProcessResult(
            success=True,
            outcome_type=outcome.outcome_type,
            capital_updated=True,
        )

    def _handle_fill(
        self,
        outcome: TradeOutcome,
        context: OrderContext,
    ) -> ProcessResult:
        assert outcome.fill_notional is not None
        assert outcome.actual_fees is not None
        assert outcome.fill_size is not None
        assert outcome.fill_price is not None

        capital_updated = False

        if context.is_entry:
            result = self._capital.order_fill(
                outcome.command_id,
                outcome.fill_notional,
                outcome.actual_fees,
            )

            if not result.success:
                return ProcessResult(
                    success=False,
                    outcome_type=outcome.outcome_type,
                    error_reason=f'order_fill failed: {result.reason}',
                )

            capital_updated = True

        position_updated, realized_pnl, cost_basis_released = (
            self._update_position_on_fill(outcome, context)
        )

        if cost_basis_released is not None:
            exit_result = self._capital.order_exit(
                context.strategy_id,
                cost_basis_released,
            )
            if not exit_result.success:
                return ProcessResult(
                    success=False,
                    outcome_type=outcome.outcome_type,
                    error_reason=f'order_exit failed: {exit_result.reason}',
                )
            capital_updated = True

        if realized_pnl is not None:
            self._update_strategy_risk_state(context.strategy_id, realized_pnl)
            self._state.risk.update_cumulative_realized_pnl(self._state.risk.realized_pnl)
            event = StrategyEvent(
                strategy_id=context.strategy_id,
                event_type='trade_outcome',
                realized_pnl=realized_pnl,
                timestamp=outcome.timestamp,
            )
            self._store.append_event(event)

        return ProcessResult(
            success=True,
            outcome_type=outcome.outcome_type,
            position_updated=position_updated,
            capital_updated=capital_updated,
        )

    def _handle_reject(
        self,
        outcome: TradeOutcome,
        context: OrderContext,
    ) -> ProcessResult:
        result = self._capital.order_reject(outcome.command_id)

        if not result.success:
            return ProcessResult(
                success=False,
                outcome_type=outcome.outcome_type,
                error_reason=f'order_reject failed: {result.reason}',
            )

        position_updated = False

        if context.is_exit:
            position_updated = self._clear_pending_exit(context, context.order_size)

        return ProcessResult(
            success=True,
            outcome_type=outcome.outcome_type,
            position_updated=position_updated,
            capital_updated=True,
        )

    def _handle_cancel(
        self,
        outcome: TradeOutcome,
        context: OrderContext,
    ) -> ProcessResult:
        result = self._capital.order_cancel(outcome.command_id)

        if not result.success:
            return ProcessResult(
                success=False,
                outcome_type=outcome.outcome_type,
                error_reason=f'order_cancel failed: {result.reason}',
            )

        position_updated = False

        if context.is_exit:
            clear_size = min(
                outcome.remaining_size
                if outcome.remaining_size is not None
                else context.order_size,
                context.order_size,
            )
            position_updated = self._clear_pending_exit(context, clear_size)

        return ProcessResult(
            success=True,
            outcome_type=outcome.outcome_type,
            position_updated=position_updated,
            capital_updated=True,
        )

    def _update_position_on_fill(
        self,
        outcome: TradeOutcome,
        context: OrderContext,
    ) -> tuple[bool, Decimal | None, Decimal | None]:
        '''Apply fill to position; return (updated, realized_pnl, cost_basis_released).

        `cost_basis_released` is set to `position.avg_cost_basis * fill_size`
        on EXIT fills so `_handle_fill` can call `CapitalController.order_exit`
        to decrement `position_notional` and `per_strategy_deployed`. None
        on ENTER fills (capital aggregates are incremented earlier via
        `order_fill`).
        '''

        assert outcome.fill_size is not None
        assert outcome.fill_price is not None

        if context.is_entry:
            return self._grow_position(outcome, context), None, None

        position_updated, realized_pnl, cost_basis_released = (
            self._reduce_position(outcome, context)
        )
        return position_updated, realized_pnl, cost_basis_released

    def _grow_position(
        self,
        outcome: TradeOutcome,
        context: OrderContext,
    ) -> bool:
        assert outcome.fill_size is not None
        assert outcome.fill_price is not None
        assert outcome.fill_notional is not None
        assert outcome.actual_fees is not None

        if context.trade_id is None:
            msg = f'entry fill without trade_id: command_id={outcome.command_id!r}'
            raise RuntimeError(msg)

        position = self._state.positions.get(context.trade_id)

        if position is None:
            msg = f'entry fill for missing position: trade_id={context.trade_id!r}'
            raise RuntimeError(msg)

        old_size = position.size
        fill_size = outcome.fill_size
        fill_price = outcome.fill_price
        fill_notional = outcome.fill_notional
        actual_fees = outcome.actual_fees

        new_size = old_size + fill_size
        new_entry_price = (
            old_size * position.entry_price + fill_size * fill_price
        ) / new_size
        new_avg_cost_basis = (
            old_size * position.avg_cost_basis + fill_notional + actual_fees
        ) / new_size

        position.size = new_size
        position.entry_price = new_entry_price
        position.avg_cost_basis = new_avg_cost_basis

        return True

    def _reduce_position(
        self,
        outcome: TradeOutcome,
        context: OrderContext,
    ) -> tuple[bool, Decimal | None, Decimal | None]:
        assert outcome.fill_size is not None
        assert outcome.fill_price is not None

        if context.trade_id is None:
            msg = f'exit fill without trade_id: command_id={outcome.command_id!r}'
            raise RuntimeError(msg)

        position = self._state.positions.get(context.trade_id)

        if position is None:
            msg = f'exit fill for missing position: trade_id={context.trade_id!r}'
            raise RuntimeError(msg)

        fill_size = outcome.fill_size
        fill_price = outcome.fill_price

        if fill_size > position.size:
            msg = (
                f'exit fill_size {fill_size} exceeds position size '
                f'{position.size}: trade_id={context.trade_id!r}'
            )
            raise RuntimeError(msg)

        entry_price = position.entry_price
        side_multiplier = Decimal(-1) if position.side == OrderSide.SELL else Decimal(1)
        realized_pnl = side_multiplier * (fill_price - entry_price) * fill_size

        if position.avg_cost_basis == _ZERO:
            _log.warning(
                'exit fill on position with avg_cost_basis=0; skipping '
                'capital decrement. Position grew through a path that '
                'did not populate avg_cost_basis (legacy snapshot, '
                'placeholder reused as real, or invariant break in '
                '_grow_position) — capital aggregates will leak by the '
                'cost basis of this fill until the next reconcile_at_boot',
                extra={
                    'command_id': outcome.command_id,
                    'trade_id': context.trade_id,
                    'fill_size': str(fill_size),
                    'position_entry_price': str(entry_price),
                },
            )
            cost_basis_released: Decimal | None = None
        else:
            cost_basis_released = position.avg_cost_basis * fill_size

        position.size = position.size - fill_size
        position.pending_exit = max(_ZERO, position.pending_exit - fill_size)

        if position.is_closed:
            del self._state.positions[context.trade_id]

        return True, realized_pnl, cost_basis_released

    def _clear_pending_exit(self, context: OrderContext, size: Decimal) -> bool:
        if context.trade_id is None:
            msg = f'clear pending exit without trade_id: command_id={context.command_id!r}'
            raise RuntimeError(msg)

        position = self._state.positions.get(context.trade_id)

        if position is None:
            return False

        position.pending_exit = max(_ZERO, position.pending_exit - size)

        return True

    def _update_strategy_risk_state(
        self,
        strategy_id: str,
        realized_pnl: Decimal,
    ) -> None:
        '''Update per-strategy risk metrics after an exit fill.

        Gets or creates StrategyRiskState for strategy_id, increments
        strategy_realized_pnl, adds to rolling loss counters if loss,
        and updates high_water_mark.

        Args:
            strategy_id: Strategy that realized the P&L.
            realized_pnl: P&L from exit fill (negative for losses).
        '''

        strategy_state = self._state.risk.per_strategy.get(strategy_id)

        if strategy_state is None:
            strategy_state = StrategyRiskState(strategy_id=strategy_id)
            self._state.risk.per_strategy[strategy_id] = strategy_state

        strategy_state.strategy_realized_pnl += realized_pnl

        if realized_pnl < _ZERO:
            loss = abs(realized_pnl)
            strategy_state.rolling_loss_24h += loss
            strategy_state.rolling_loss_7d += loss
            strategy_state.rolling_loss_30d += loss

        strategy_state.high_water_mark = max(
            strategy_state.high_water_mark,
            strategy_state.strategy_realized_pnl,
        )
