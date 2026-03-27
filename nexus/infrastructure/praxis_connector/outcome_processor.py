'''OutcomeProcessor for routing TradeOutcomes to Capital Controller.

Routes inbound TradeOutcomes to appropriate Capital Controller lifecycle
methods and updates positions on fill outcomes.
'''

from __future__ import annotations

from decimal import Decimal

from nexus.core.capital_controller.capital_controller import CapitalController
from nexus.core.domain.instance_state import InstanceState
from nexus.infrastructure.praxis_connector.order_context import OrderContext
from nexus.infrastructure.praxis_connector.process_result import ProcessResult
from nexus.infrastructure.praxis_connector.trade_outcome import TradeOutcome
from nexus.infrastructure.praxis_connector.trade_outcome_type import TradeOutcomeType

__all__ = ['OutcomeProcessor']

_ZERO = Decimal(0)


class OutcomeProcessor:
    '''Routes TradeOutcomes to Capital Controller and updates positions.

    Args:
        capital_controller: Capital lifecycle manager.
        instance_state: Runtime state containing positions.
    '''

    def __init__(
        self,
        capital_controller: CapitalController,
        instance_state: InstanceState,
    ) -> None:
        self._capital = capital_controller
        self._state = instance_state

    def process(
        self,
        outcome: TradeOutcome,
        context: OrderContext,
    ) -> ProcessResult:
        '''Process a TradeOutcome and update state accordingly.

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
        success = self._capital.order_ack(outcome.command_id)

        if not success:
            return ProcessResult(
                success=False,
                outcome_type=outcome.outcome_type,
                error_reason='order_ack failed: order not found or wrong state',
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

        success = self._capital.order_fill(
            outcome.command_id,
            outcome.fill_notional,
            outcome.actual_fees,
        )

        if not success:
            return ProcessResult(
                success=False,
                outcome_type=outcome.outcome_type,
                error_reason='order_fill failed: order not found, wrong state, fill_notional exceeds remaining, or insufficient fee_reserve',
            )

        position_updated = self._update_position_on_fill(outcome, context)

        return ProcessResult(
            success=True,
            outcome_type=outcome.outcome_type,
            position_updated=position_updated,
            capital_updated=True,
        )

    def _handle_reject(
        self,
        outcome: TradeOutcome,
        context: OrderContext,
    ) -> ProcessResult:
        success = self._capital.order_reject(outcome.command_id)

        if not success:
            return ProcessResult(
                success=False,
                outcome_type=outcome.outcome_type,
                error_reason='order_reject failed: order not found or wrong state',
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
        success = self._capital.order_cancel(outcome.command_id)

        if not success:
            return ProcessResult(
                success=False,
                outcome_type=outcome.outcome_type,
                error_reason='order_cancel failed: order not found or wrong state',
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
    ) -> bool:
        assert outcome.fill_size is not None
        assert outcome.fill_price is not None

        if context.is_entry:
            return self._grow_position(outcome, context)

        return self._reduce_position(outcome, context)

    def _grow_position(
        self,
        outcome: TradeOutcome,
        context: OrderContext,
    ) -> bool:
        assert outcome.fill_size is not None
        assert outcome.fill_price is not None

        if context.trade_id is None:
            return False

        position = self._state.positions.get(context.trade_id)

        if position is None:
            return False

        old_size = position.size
        fill_size = outcome.fill_size
        fill_price = outcome.fill_price

        new_size = old_size + fill_size
        new_entry_price = (
            old_size * position.entry_price + fill_size * fill_price
        ) / new_size

        position.size = new_size
        position.entry_price = new_entry_price

        return True

    def _reduce_position(
        self,
        outcome: TradeOutcome,
        context: OrderContext,
    ) -> bool:
        assert outcome.fill_size is not None

        if context.trade_id is None:
            return False

        position = self._state.positions.get(context.trade_id)

        if position is None:
            return False

        fill_size = outcome.fill_size

        if fill_size > position.size:
            return False

        position.size = position.size - fill_size
        position.pending_exit = max(_ZERO, position.pending_exit - fill_size)

        if position.is_closed:
            del self._state.positions[context.trade_id]

        return True

    def _clear_pending_exit(self, context: OrderContext, size: Decimal) -> bool:
        if context.trade_id is None:
            return False

        position = self._state.positions.get(context.trade_id)

        if position is None:
            return False

        position.pending_exit = max(_ZERO, position.pending_exit - size)

        return True
