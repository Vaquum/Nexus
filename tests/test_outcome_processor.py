from __future__ import annotations

import tempfile
import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from nexus.core.capital_controller.capital_controller import CapitalController
from nexus.core.domain.capital_state import CapitalState
from nexus.core.domain.enums import OrderSide
from nexus.core.domain.instance_state import InstanceState
from nexus.core.domain.position import Position
from nexus.infrastructure.praxis_connector.order_context import OrderContext
from nexus.infrastructure.praxis_connector.outcome_processor import OutcomeProcessor
from nexus.infrastructure.praxis_connector.trade_outcome import TradeOutcome
from nexus.infrastructure.praxis_connector.trade_outcome_type import TradeOutcomeType
from nexus.infrastructure.state_store import StateStore

_POOL = Decimal('10000')
_ZERO = Decimal(0)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _make_processor() -> tuple[
    OutcomeProcessor, CapitalController, InstanceState, StateStore, tempfile.TemporaryDirectory[str]
]:
    capital_state = CapitalState(capital_pool=_POOL)
    instance_state = InstanceState(capital=capital_state)
    controller = CapitalController(capital_state)
    tmp_dir = tempfile.TemporaryDirectory()
    state_store = StateStore(Path(tmp_dir.name))
    processor = OutcomeProcessor(controller, instance_state, state_store)
    return processor, controller, instance_state, state_store, tmp_dir


def _setup_in_flight_order(ctrl: CapitalController, order_id: str = 'cmd_001') -> None:
    result = ctrl.check_and_reserve(
        strategy_id='strat_001',
        order_notional=Decimal('100'),
        estimated_fees=Decimal('1'),
        strategy_budget=Decimal('5000'),
    )
    assert result.reservation is not None
    ctrl.send_order(result.reservation.reservation_id, order_id)


def _setup_working_order(ctrl: CapitalController, order_id: str = 'cmd_001') -> None:
    _setup_in_flight_order(ctrl, order_id)
    ctrl.order_ack(order_id)


def _prime_open_position_capital(
    ctrl: CapitalController,
    strategy_id: str,
    size: Decimal,
    avg_cost_basis: Decimal,
) -> Decimal:
    '''Prime `position_notional` and `per_strategy_deployed[strategy_id]`
    so they reflect an open position of `size` at `avg_cost_basis`. Used
    by EXIT FILL tests to assert capital decrement post-conditions
    without running a full entry FILL pipeline. Returns the cost basis
    total added so the test can compute expected decrements.'''

    cost_basis_total = avg_cost_basis * size
    ctrl._state.position_notional += cost_basis_total
    current = ctrl._state.per_strategy_deployed.get(strategy_id, _ZERO)
    ctrl._state.per_strategy_deployed[strategy_id] = current + cost_basis_total
    return cost_basis_total


def _entry_context(trade_id: str = 'trade_001') -> OrderContext:
    return OrderContext(
        command_id='cmd_001',
        strategy_id='strat_001',
        trade_id=trade_id,
        side=OrderSide.BUY,
        order_size=Decimal('0.01'),
        order_notional=Decimal('100'),
        estimated_fees=Decimal('1'),
        is_entry=True,
    )


def _exit_context(trade_id: str = 'trade_001') -> OrderContext:
    return OrderContext(
        command_id='cmd_001',
        strategy_id='strat_001',
        trade_id=trade_id,
        side=OrderSide.SELL,
        order_size=Decimal('0.01'),
        order_notional=Decimal('100'),
        estimated_fees=Decimal('1'),
        is_entry=False,
    )


class TestOutcomeProcessorAck:
    def test_ack_success(self) -> None:
        proc, ctrl, _, _, _tmp = _make_processor()
        _setup_in_flight_order(ctrl)

        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.ACK,
            timestamp=_now(),
        )

        result = proc.process(outcome, _entry_context())
        assert result.success is True
        assert result.outcome_type == TradeOutcomeType.ACK
        assert result.capital_updated is True
        assert result.position_updated is False

    def test_ack_order_not_found(self) -> None:
        proc, _, _, _, _tmp = _make_processor()

        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='nonexistent',
            outcome_type=TradeOutcomeType.ACK,
            timestamp=_now(),
        )

        ctx = OrderContext(
            command_id='nonexistent',
            strategy_id='strat_001',
            trade_id=None,
            side=OrderSide.BUY,
            order_size=Decimal('0.01'),
            order_notional=Decimal('100'),
            estimated_fees=Decimal('1'),
            is_entry=True,
        )

        result = proc.process(outcome, ctx)
        assert result.success is False
        assert result.error_reason is not None
        assert 'order_ack failed' in result.error_reason


class TestOutcomeProcessorFill:
    def test_fill_success(self) -> None:
        proc, ctrl, state, _, _tmp = _make_processor()
        _setup_working_order(ctrl)

        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0.01'),
            entry_price=Decimal('50000'),
        )

        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.FILLED,
            timestamp=_now(),
            fill_size=Decimal('0.01'),
            fill_price=Decimal('50000'),
            fill_notional=Decimal('100'),
            actual_fees=Decimal('1'),
        )

        result = proc.process(outcome, _entry_context())
        assert result.success is True
        assert result.outcome_type == TradeOutcomeType.FILLED
        assert result.capital_updated is True
        assert result.position_updated is True

    def test_partial_fill_success(self) -> None:
        proc, ctrl, state, _, _tmp = _make_processor()

        res = ctrl.check_and_reserve(
            strategy_id='strat_001',
            order_notional=Decimal('1000'),
            estimated_fees=Decimal('10'),
            strategy_budget=Decimal('5000'),
        )
        assert res.reservation is not None
        ctrl.send_order(res.reservation.reservation_id, 'cmd_001')
        ctrl.order_ack('cmd_001')

        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0.01'),
            entry_price=Decimal('50000'),
        )

        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.PARTIAL,
            timestamp=_now(),
            fill_size=Decimal('0.005'),
            fill_price=Decimal('50000'),
            fill_notional=Decimal('500'),
            actual_fees=Decimal('5'),
            remaining_size=Decimal('0.005'),
        )

        result = proc.process(outcome, _entry_context())
        assert result.success is True
        assert result.outcome_type == TradeOutcomeType.PARTIAL

    def test_fill_order_not_found(self) -> None:
        proc, _, _, _, _tmp = _make_processor()

        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='nonexistent',
            outcome_type=TradeOutcomeType.FILLED,
            timestamp=_now(),
            fill_size=Decimal('0.01'),
            fill_price=Decimal('50000'),
            fill_notional=Decimal('100'),
            actual_fees=Decimal('1'),
        )

        ctx = OrderContext(
            command_id='nonexistent',
            strategy_id='strat_001',
            trade_id='trade_001',
            side=OrderSide.BUY,
            order_size=Decimal('0.01'),
            order_notional=Decimal('100'),
            estimated_fees=Decimal('1'),
            is_entry=True,
        )

        result = proc.process(outcome, ctx)
        assert result.success is False
        assert result.error_reason is not None
        assert 'order_fill failed' in result.error_reason


class TestOutcomeProcessorReject:
    def test_reject_entry_order(self) -> None:
        proc, ctrl, _, _, _tmp = _make_processor()
        _setup_in_flight_order(ctrl)

        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.REJECTED,
            timestamp=_now(),
            reject_reason='Insufficient balance',
        )

        result = proc.process(outcome, _entry_context())
        assert result.success is True
        assert result.outcome_type == TradeOutcomeType.REJECTED
        assert result.capital_updated is True
        assert result.position_updated is False

    def test_reject_after_ack_releases_working_capital(self) -> None:
        '''PT-FIX-40 end-to-end: ENTER → ACK → REJECTED. Pre-fix the
        REJECTED outcome arrived after the order was promoted from
        IN_FLIGHT to WORKING by the ACK; `order_reject` rejected the
        WORKING state and `working_order_notional` was leaked. Post-
        fix `order_reject` accepts WORKING and releases the capital;
        OutcomeProcessor returns success.'''

        proc, ctrl, _, _, _tmp = _make_processor()
        _setup_working_order(ctrl)

        assert ctrl._state.working_order_notional == Decimal('101')
        assert ctrl._state.in_flight_order_notional == _ZERO

        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.REJECTED,
            timestamp=_now(),
            reject_reason='venue late reject after ack',
        )

        result = proc.process(outcome, _entry_context())

        assert result.success is True
        assert result.outcome_type == TradeOutcomeType.REJECTED
        assert result.capital_updated is True
        assert ctrl._state.working_order_notional == _ZERO
        assert ctrl._state.in_flight_order_notional == _ZERO

    def test_reject_exit_order_clears_pending_exit(self) -> None:
        proc, ctrl, state, _, _tmp = _make_processor()
        _setup_in_flight_order(ctrl)

        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0.01'),
            entry_price=Decimal('50000'),
            pending_exit=Decimal('0.01'),
        )

        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.REJECTED,
            timestamp=_now(),
            reject_reason='Insufficient balance',
        )

        result = proc.process(outcome, _exit_context())
        assert result.success is True
        assert result.position_updated is True
        assert state.positions['trade_001'].pending_exit == _ZERO


class TestOutcomeProcessorCancel:
    def test_cancel_success(self) -> None:
        proc, ctrl, _, _, _tmp = _make_processor()
        _setup_working_order(ctrl)

        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.CANCELED,
            timestamp=_now(),
        )

        result = proc.process(outcome, _entry_context())
        assert result.success is True
        assert result.outcome_type == TradeOutcomeType.CANCELED
        assert result.capital_updated is True

    def test_expired_success(self) -> None:
        proc, ctrl, _, _, _tmp = _make_processor()
        _setup_working_order(ctrl)

        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.EXPIRED,
            timestamp=_now(),
        )

        result = proc.process(outcome, _entry_context())
        assert result.success is True
        assert result.outcome_type == TradeOutcomeType.EXPIRED

    def test_cancel_in_flight_releases_in_flight_capital(self) -> None:
        '''PT-FIX-43 end-to-end: ENTER → no ACK → EXPIRED. Pre-fix the
        EXPIRED outcome arrived while the order was still IN_FLIGHT
        (venue rejected before ACK or the ACK never landed);
        `order_cancel` rejected the IN_FLIGHT state and
        `in_flight_order_notional` was leaked. Post-fix `order_cancel`
        accepts IN_FLIGHT and releases the capital; OutcomeProcessor
        returns success.'''

        proc, ctrl, _, _, _tmp = _make_processor()
        _setup_in_flight_order(ctrl)

        assert ctrl._state.in_flight_order_notional == Decimal('101')
        assert ctrl._state.working_order_notional == _ZERO

        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.EXPIRED,
            timestamp=_now(),
        )

        result = proc.process(outcome, _entry_context())

        assert result.success is True
        assert result.outcome_type == TradeOutcomeType.EXPIRED
        assert result.capital_updated is True
        assert ctrl._state.in_flight_order_notional == _ZERO
        assert ctrl._state.working_order_notional == _ZERO

    def test_cancel_exit_order_clears_pending_exit(self) -> None:
        proc, ctrl, state, _, _tmp = _make_processor()
        _setup_working_order(ctrl)

        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0.01'),
            entry_price=Decimal('50000'),
            pending_exit=Decimal('0.01'),
        )

        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.CANCELED,
            timestamp=_now(),
        )

        result = proc.process(outcome, _exit_context())
        assert result.success is True
        assert result.position_updated is True
        assert state.positions['trade_001'].pending_exit == _ZERO


class TestPositionGrowth:
    def test_entry_fill_grows_position(self) -> None:
        proc, ctrl, state, _, _tmp = _make_processor()
        _setup_working_order(ctrl)

        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0.01'),
            entry_price=Decimal('50000'),
        )

        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.FILLED,
            timestamp=_now(),
            fill_size=Decimal('0.01'),
            fill_price=Decimal('50000'),
            fill_notional=Decimal('100'),
            actual_fees=Decimal('1'),
        )

        proc.process(outcome, _entry_context())
        assert state.positions['trade_001'].size == Decimal('0.02')

    def test_entry_fill_calculates_vwap(self) -> None:
        proc, ctrl, state, _, _tmp = _make_processor()
        _setup_working_order(ctrl)

        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0.01'),
            entry_price=Decimal('50000'),
        )

        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.FILLED,
            timestamp=_now(),
            fill_size=Decimal('0.02'),
            fill_price=Decimal('60000'),
            fill_notional=Decimal('100'),
            actual_fees=Decimal('1'),
        )

        proc.process(outcome, _entry_context())

        expected = (
            Decimal('0.01') * Decimal('50000') + Decimal('0.02') * Decimal('60000')
        ) / Decimal('0.03')
        assert state.positions['trade_001'].entry_price == expected

    def test_entry_fill_no_position_raises(self) -> None:
        proc, ctrl, _, _, _tmp = _make_processor()
        _setup_working_order(ctrl)

        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.FILLED,
            timestamp=_now(),
            fill_size=Decimal('0.01'),
            fill_price=Decimal('50000'),
            fill_notional=Decimal('100'),
            actual_fees=Decimal('1'),
        )

        with pytest.raises(RuntimeError, match='missing position'):
            proc.process(outcome, _entry_context())


class TestEntryFillTerminalThreading:
    '''Pin Vaquum/Nexus#78: OutcomeProcessor → CapitalController contract.

    The fix at PR-prep round-1 added a `terminal` keyword to
    `CapitalController.order_fill`. The OutcomeProcessor derives it as
    `outcome.outcome_type == TradeOutcomeType.FILLED` so the capital layer
    can release the unfilled reservation residual when the upstream venue
    declares the order terminal. These tests pin the cross-component
    contract: removing or mis-mapping the kwarg at the call site re-opens
    the v0.55.0 ghost-residual leak.
    '''

    def test_fill_outcome_terminal_releases_residual(self) -> None:
        '''FILLED outcome with `fill_notional < reservation.notional`
        releases the residual from `working_order_notional` and
        `per_strategy_deployed` (zero working, deployed equals actual
        cost basis of the filled portion).
        '''

        proc, ctrl, state, _, _tmp = _make_processor()
        _setup_working_order(ctrl)

        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0'),
            entry_price=Decimal('50000'),
        )

        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.FILLED,
            timestamp=_now(),
            fill_size=Decimal('0.001'),
            fill_price=Decimal('50000'),
            fill_notional=Decimal('50'),
            actual_fees=Decimal('0.5'),
        )

        proc.process(outcome, _entry_context())

        assert ctrl._state.working_order_notional == _ZERO
        assert ctrl._state.per_strategy_deployed.get('strat_001') == Decimal('50.5')
        assert 'cmd_001' not in ctrl._orders

    def test_partial_outcome_non_terminal_keeps_residual(self) -> None:
        '''PARTIAL outcome with `fill_notional < reservation.notional`
        retains the order in `_orders` with reduced `remaining_notional`;
        the residual stays in `working_order_notional` because more
        fills are still expected.
        '''

        proc, ctrl, state, _, _tmp = _make_processor()
        _setup_working_order(ctrl)

        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0'),
            entry_price=Decimal('50000'),
        )

        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.PARTIAL,
            timestamp=_now(),
            fill_size=Decimal('0.001'),
            fill_price=Decimal('50000'),
            fill_notional=Decimal('50'),
            actual_fees=Decimal('0.5'),
        )

        proc.process(outcome, _entry_context())

        assert ctrl._state.working_order_notional > _ZERO
        assert 'cmd_001' in ctrl._orders
        assert ctrl._orders['cmd_001'].remaining_notional == Decimal('50')


class TestPositionReduction:
    def test_exit_fill_reduces_position(self) -> None:
        proc, ctrl, state, _, _tmp = _make_processor()
        _setup_working_order(ctrl)

        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0.02'),
            entry_price=Decimal('50000'),
            pending_exit=Decimal('0.01'),
        )

        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.FILLED,
            timestamp=_now(),
            fill_size=Decimal('0.01'),
            fill_price=Decimal('51000'),
            fill_notional=Decimal('100'),
            actual_fees=Decimal('1'),
        )

        proc.process(outcome, _exit_context())
        assert state.positions['trade_001'].size == Decimal('0.01')
        assert state.positions['trade_001'].pending_exit == _ZERO

    def test_exit_fill_removes_closed_position(self) -> None:
        proc, ctrl, state, _, _tmp = _make_processor()
        _setup_working_order(ctrl)

        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0.01'),
            entry_price=Decimal('50000'),
            pending_exit=Decimal('0.01'),
        )

        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.FILLED,
            timestamp=_now(),
            fill_size=Decimal('0.01'),
            fill_price=Decimal('51000'),
            fill_notional=Decimal('100'),
            actual_fees=Decimal('1'),
        )

        proc.process(outcome, _exit_context())
        assert 'trade_001' not in state.positions

    def test_exit_fill_overfill_raises(self) -> None:
        proc, ctrl, state, _, _tmp = _make_processor()
        _setup_working_order(ctrl)

        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0.01'),
            entry_price=Decimal('50000'),
            pending_exit=Decimal('0.01'),
        )

        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.FILLED,
            timestamp=_now(),
            fill_size=Decimal('0.02'),
            fill_price=Decimal('51000'),
            fill_notional=Decimal('100'),
            actual_fees=Decimal('1'),
        )

        with pytest.raises(RuntimeError, match='exceeds position size'):
            proc.process(outcome, _exit_context())


class TestCommandIdMismatch:
    def test_command_id_mismatch_rejected(self) -> None:
        proc, ctrl, _, _, _tmp = _make_processor()
        _setup_working_order(ctrl)

        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.FILLED,
            timestamp=_now(),
            fill_size=Decimal('0.01'),
            fill_price=Decimal('50000'),
            fill_notional=Decimal('100'),
            actual_fees=Decimal('1'),
        )

        mismatched_ctx = OrderContext(
            command_id='cmd_999',
            strategy_id='strat_001',
            trade_id='trade_001',
            side=OrderSide.BUY,
            order_size=Decimal('0.01'),
            order_notional=Decimal('100'),
            estimated_fees=Decimal('1'),
            is_entry=True,
        )

        result = proc.process(outcome, mismatched_ctx)
        assert result.success is False
        assert result.error_reason is not None
        assert 'does not match' in result.error_reason


class TestCancelUsesRemainingSize:
    def test_cancel_after_partial_fill_uses_remaining_size(self) -> None:
        proc, ctrl, state, _, _tmp = _make_processor()
        _setup_working_order(ctrl)

        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0.01'),
            entry_price=Decimal('50000'),
            pending_exit=Decimal('0.01'),
        )

        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.CANCELED,
            timestamp=_now(),
            remaining_size=Decimal('0.005'),
        )

        ctx = OrderContext(
            command_id='cmd_001',
            strategy_id='strat_001',
            trade_id='trade_001',
            side=OrderSide.SELL,
            order_size=Decimal('0.01'),
            order_notional=Decimal('100'),
            estimated_fees=Decimal('1'),
            is_entry=False,
        )

        result = proc.process(outcome, ctx)
        assert result.success is True
        assert result.position_updated is True
        assert state.positions['trade_001'].pending_exit == Decimal('0.005')


class TestRiskMetricsRecalculation:
    def test_exit_fill_loss_updates_rolling_loss_counters(self) -> None:
        proc, ctrl, state, _, _tmp = _make_processor()
        _setup_working_order(ctrl)

        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0.01'),
            entry_price=Decimal('50000'),
            avg_cost_basis=Decimal('50000'),
        )
        cost_basis = _prime_open_position_capital(
            ctrl, 'strat_001', Decimal('0.01'), Decimal('50000'),
        )
        pre_position_notional = ctrl._state.position_notional
        pre_deployed = ctrl._state.per_strategy_deployed['strat_001']

        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.FILLED,
            timestamp=_now(),
            fill_size=Decimal('0.01'),
            fill_price=Decimal('49000'),
            fill_notional=Decimal('490'),
            actual_fees=Decimal('1'),
        )

        result = proc.process(outcome, _exit_context())
        assert result.success is True

        strategy_risk = state.risk.per_strategy['strat_001']
        # FINAL-MAJOR-08: realized_pnl is NET of exit fees.
        # gross loss = (49000 - 50000) * 0.01 = -10; minus actual_fees=1 -> -11
        # rolling_loss tracks abs(net loss).
        expected_loss = Decimal('11')
        assert strategy_risk.rolling_loss_24h == expected_loss
        assert strategy_risk.rolling_loss_7d == expected_loss
        assert strategy_risk.rolling_loss_30d == expected_loss
        assert ctrl._state.position_notional == pre_position_notional - cost_basis
        assert ctrl._state.per_strategy_deployed['strat_001'] == pre_deployed - cost_basis
        assert cost_basis == Decimal('500')

    def test_exit_fill_updates_strategy_realized_pnl(self) -> None:
        proc, ctrl, state, _, _tmp = _make_processor()
        _setup_working_order(ctrl)

        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0.01'),
            entry_price=Decimal('50000'),
            avg_cost_basis=Decimal('50000'),
        )
        cost_basis = _prime_open_position_capital(
            ctrl, 'strat_001', Decimal('0.01'), Decimal('50000'),
        )
        pre_position_notional = ctrl._state.position_notional
        pre_deployed = ctrl._state.per_strategy_deployed['strat_001']

        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.FILLED,
            timestamp=_now(),
            fill_size=Decimal('0.01'),
            fill_price=Decimal('51000'),
            fill_notional=Decimal('510'),
            actual_fees=Decimal('1'),
        )

        result = proc.process(outcome, _exit_context())
        assert result.success is True

        strategy_risk = state.risk.per_strategy['strat_001']
        # FINAL-MAJOR-08: realized_pnl is NET of exit fees.
        # gross = (51000 - 50000) * 0.01 = 10; minus actual_fees=1 -> 9
        expected_pnl = Decimal('9')
        assert strategy_risk.strategy_realized_pnl == expected_pnl
        assert ctrl._state.position_notional == pre_position_notional - cost_basis
        assert ctrl._state.per_strategy_deployed['strat_001'] == pre_deployed - cost_basis

    def test_exit_fill_updates_instance_cumulative_realized_pnl(self) -> None:
        proc, ctrl, state, _, _tmp = _make_processor()
        _setup_working_order(ctrl)

        state.risk.starting_capital = _POOL

        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0.01'),
            entry_price=Decimal('50000'),
            avg_cost_basis=Decimal('50000'),
        )
        cost_basis = _prime_open_position_capital(
            ctrl, 'strat_001', Decimal('0.01'), Decimal('50000'),
        )
        pre_position_notional = ctrl._state.position_notional
        pre_deployed = ctrl._state.per_strategy_deployed['strat_001']

        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.FILLED,
            timestamp=_now(),
            fill_size=Decimal('0.01'),
            fill_price=Decimal('51000'),
            fill_notional=Decimal('510'),
            actual_fees=Decimal('1'),
        )

        result = proc.process(outcome, _exit_context())
        assert result.success is True

        # FINAL-MAJOR-08: cumulative_realized_pnl is NET of exit fees.
        # gross = (51000 - 50000) * 0.01 = 10; minus actual_fees=1 -> 9
        expected_pnl = Decimal('9')
        assert state.risk.cumulative_realized_pnl == expected_pnl
        assert ctrl._state.position_notional == pre_position_notional - cost_basis
        assert ctrl._state.per_strategy_deployed['strat_001'] == pre_deployed - cost_basis

    def test_exit_fill_triggers_drawdown_recompute(self) -> None:
        proc, ctrl, state, _, _tmp = _make_processor()
        _setup_working_order(ctrl)

        state.risk.starting_capital = _POOL

        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0.01'),
            entry_price=Decimal('50000'),
            avg_cost_basis=Decimal('50000'),
        )
        cost_basis = _prime_open_position_capital(
            ctrl, 'strat_001', Decimal('0.01'), Decimal('50000'),
        )
        pre_position_notional = ctrl._state.position_notional
        pre_deployed = ctrl._state.per_strategy_deployed['strat_001']

        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.FILLED,
            timestamp=_now(),
            fill_size=Decimal('0.01'),
            fill_price=Decimal('51000'),
            fill_notional=Decimal('510'),
            actual_fees=Decimal('1'),
        )

        result = proc.process(outcome, _exit_context())
        assert result.success is True

        # FINAL-MAJOR-08: realized_pnl is NET of exit fees, so equity
        # adds the net (gross 10 - fees 1 = 9), not the gross.
        expected_equity = _POOL + Decimal('9')
        assert state.risk.equity == expected_equity
        assert state.risk.equity_hwm == expected_equity
        assert ctrl._state.position_notional == pre_position_notional - cost_basis
        assert ctrl._state.per_strategy_deployed['strat_001'] == pre_deployed - cost_basis

    def test_profitable_exit_does_not_add_to_rolling_losses(self) -> None:
        proc, ctrl, state, _, _tmp = _make_processor()
        _setup_working_order(ctrl)

        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0.01'),
            entry_price=Decimal('50000'),
            avg_cost_basis=Decimal('50000'),
        )
        cost_basis = _prime_open_position_capital(
            ctrl, 'strat_001', Decimal('0.01'), Decimal('50000'),
        )
        pre_position_notional = ctrl._state.position_notional
        pre_deployed = ctrl._state.per_strategy_deployed['strat_001']

        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.FILLED,
            timestamp=_now(),
            fill_size=Decimal('0.01'),
            fill_price=Decimal('51000'),
            fill_notional=Decimal('510'),
            actual_fees=Decimal('1'),
        )

        result = proc.process(outcome, _exit_context())
        assert result.success is True

        strategy_risk = state.risk.per_strategy['strat_001']
        assert strategy_risk.rolling_loss_24h == _ZERO
        assert strategy_risk.rolling_loss_7d == _ZERO
        assert strategy_risk.rolling_loss_30d == _ZERO
        assert ctrl._state.position_notional == pre_position_notional - cost_basis
        assert ctrl._state.per_strategy_deployed['strat_001'] == pre_deployed - cost_basis

    def test_multiple_strategies_isolated(self) -> None:
        proc, ctrl, state, _, _tmp = _make_processor()
        _setup_working_order(ctrl)

        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0.01'),
            entry_price=Decimal('50000'),
            avg_cost_basis=Decimal('50000'),
        )
        cost_basis = _prime_open_position_capital(
            ctrl, 'strat_001', Decimal('0.01'), Decimal('50000'),
        )
        pre_position_notional = ctrl._state.position_notional
        pre_deployed = ctrl._state.per_strategy_deployed['strat_001']

        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.FILLED,
            timestamp=_now(),
            fill_size=Decimal('0.01'),
            fill_price=Decimal('49000'),
            fill_notional=Decimal('490'),
            actual_fees=Decimal('1'),
        )

        result = proc.process(outcome, _exit_context())
        assert result.success is True

        assert 'strat_001' in state.risk.per_strategy
        assert 'strat_002' not in state.risk.per_strategy

        strat1_risk = state.risk.per_strategy['strat_001']
        # FINAL-MAJOR-08: rolling_loss tracks abs(net loss).
        # gross loss 10 + actual_fees 1 = net loss 11.
        assert strat1_risk.rolling_loss_24h == Decimal('11')
        assert ctrl._state.position_notional == pre_position_notional - cost_basis
        assert ctrl._state.per_strategy_deployed['strat_001'] == pre_deployed - cost_basis

    def test_strategy_event_appended_to_wal_on_exit_fill(self) -> None:
        proc, ctrl, state, store, _tmp = _make_processor()
        _setup_working_order(ctrl)

        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0.01'),
            entry_price=Decimal('50000'),
            avg_cost_basis=Decimal('50000'),
        )
        cost_basis = _prime_open_position_capital(
            ctrl, 'strat_001', Decimal('0.01'), Decimal('50000'),
        )
        pre_position_notional = ctrl._state.position_notional
        pre_deployed = ctrl._state.per_strategy_deployed['strat_001']

        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.FILLED,
            timestamp=_now(),
            fill_size=Decimal('0.01'),
            fill_price=Decimal('51000'),
            fill_notional=Decimal('510'),
            actual_fees=Decimal('1'),
        )

        result = proc.process(outcome, _exit_context())
        assert result.success is True

        entries = store._wal.read_all()
        event_entries = [e for e in entries if e.entry_type.name == 'STRATEGY_EVENT']
        assert len(event_entries) == 1
        assert ctrl._state.position_notional == pre_position_notional - cost_basis
        assert ctrl._state.per_strategy_deployed['strat_001'] == pre_deployed - cost_basis

    def test_short_position_pnl_sign_correct(self) -> None:
        proc, ctrl, state, _, _tmp = _make_processor()
        _setup_working_order(ctrl)

        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.SELL,
            size=Decimal('0.01'),
            entry_price=Decimal('50000'),
            avg_cost_basis=Decimal('50000'),
        )
        cost_basis = _prime_open_position_capital(
            ctrl, 'strat_001', Decimal('0.01'), Decimal('50000'),
        )
        pre_position_notional = ctrl._state.position_notional
        pre_deployed = ctrl._state.per_strategy_deployed['strat_001']

        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.FILLED,
            timestamp=_now(),
            fill_size=Decimal('0.01'),
            fill_price=Decimal('49000'),
            fill_notional=Decimal('490'),
            actual_fees=Decimal('1'),
        )

        result = proc.process(outcome, _exit_context())
        assert result.success is True

        strategy_risk = state.risk.per_strategy['strat_001']
        # FINAL-MAJOR-08: realized_pnl is NET of exit fees.
        # SHORT: gross = -(49000 - 50000) * 0.01 = +10; minus actual_fees=1 -> 9
        expected_pnl = Decimal('9')
        assert strategy_risk.strategy_realized_pnl == expected_pnl
        assert strategy_risk.rolling_loss_24h == _ZERO
        assert ctrl._state.position_notional == pre_position_notional - cost_basis
        assert ctrl._state.per_strategy_deployed['strat_001'] == pre_deployed - cost_basis


class TestCapitalConservationOnExit:
    '''Round-trip conservation: every entry FILL adds
    `fill_notional + actual_fees` to `position_notional` (via
    `CapitalController.order_fill`); each matching exit FILL removes
    `position.avg_cost_basis * fill_size` (via
    `CapitalController.order_exit`). The aggregates return to zero
    once a position is fully closed.

    `Position.avg_cost_basis` is maintained by
    `OutcomeProcessor._grow_position` as a VWAP-with-fees so the
    cost-basis-released on partial exits is proportional to the size
    closed.
    '''

    def test_full_round_trip_returns_aggregates_to_zero(self) -> None:
        proc, ctrl, state, _, _tmp = _make_processor()

        # ENTER cycle: reserve → send_order → ack → fill
        _setup_working_order(ctrl, order_id='cmd_enter')
        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0'),
            entry_price=Decimal('50000'),
        )
        entry_outcome = TradeOutcome(
            outcome_id='out_enter',
            command_id='cmd_enter',
            outcome_type=TradeOutcomeType.FILLED,
            timestamp=_now(),
            fill_size=Decimal('0.002'),
            fill_price=Decimal('50000'),
            fill_notional=Decimal('100'),
            actual_fees=Decimal('1'),
        )
        entry_ctx = OrderContext(
            command_id='cmd_enter',
            strategy_id='strat_001',
            trade_id='trade_001',
            side=OrderSide.BUY,
            order_size=Decimal('0.002'),
            order_notional=Decimal('100'),
            estimated_fees=Decimal('1'),
            is_entry=True,
        )
        proc.process(entry_outcome, entry_ctx)

        # After entry: position_notional should be 101 (notional + fees)
        assert ctrl._state.position_notional == Decimal('101')
        assert ctrl._state.per_strategy_deployed['strat_001'] == Decimal('101')

        # avg_cost_basis on the position should reflect VWAP-with-fees
        position = state.positions['trade_001']
        assert position.size == Decimal('0.002')
        assert position.avg_cost_basis == Decimal('50500')  # 101 / 0.002

        # EXIT FILL: full close at 51000 (gross profit 2)
        exit_outcome = TradeOutcome(
            outcome_id='out_exit',
            command_id='cmd_exit',
            outcome_type=TradeOutcomeType.FILLED,
            timestamp=_now(),
            fill_size=Decimal('0.002'),
            fill_price=Decimal('51000'),
            fill_notional=Decimal('102'),
            actual_fees=Decimal('1'),
        )
        exit_ctx = OrderContext(
            command_id='cmd_exit',
            strategy_id='strat_001',
            trade_id='trade_001',
            side=OrderSide.SELL,
            order_size=Decimal('0.002'),
            order_notional=Decimal('102'),
            estimated_fees=Decimal('1'),
            is_entry=False,
        )
        result = proc.process(exit_outcome, exit_ctx)
        assert result.success is True

        assert ctrl._state.position_notional == _ZERO
        assert 'strat_001' not in ctrl._state.per_strategy_deployed
        assert 'trade_001' not in state.positions

    def test_partial_exit_decrements_proportionally(self) -> None:
        proc, ctrl, state, _, _tmp = _make_processor()

        _setup_working_order(ctrl, order_id='cmd_enter')
        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0'),
            entry_price=Decimal('50000'),
        )
        entry_outcome = TradeOutcome(
            outcome_id='out_enter',
            command_id='cmd_enter',
            outcome_type=TradeOutcomeType.FILLED,
            timestamp=_now(),
            fill_size=Decimal('0.002'),
            fill_price=Decimal('50000'),
            fill_notional=Decimal('100'),
            actual_fees=Decimal('1'),
        )
        entry_ctx = OrderContext(
            command_id='cmd_enter',
            strategy_id='strat_001',
            trade_id='trade_001',
            side=OrderSide.BUY,
            order_size=Decimal('0.002'),
            order_notional=Decimal('100'),
            estimated_fees=Decimal('1'),
            is_entry=True,
        )
        proc.process(entry_outcome, entry_ctx)

        # Half-close
        half_exit = TradeOutcome(
            outcome_id='out_partial',
            command_id='cmd_exit',
            outcome_type=TradeOutcomeType.PARTIAL,
            timestamp=_now(),
            fill_size=Decimal('0.001'),
            fill_price=Decimal('51000'),
            fill_notional=Decimal('51'),
            actual_fees=Decimal('0.5'),
        )
        exit_ctx = OrderContext(
            command_id='cmd_exit',
            strategy_id='strat_001',
            trade_id='trade_001',
            side=OrderSide.SELL,
            order_size=Decimal('0.002'),
            order_notional=Decimal('102'),
            estimated_fees=Decimal('1'),
            is_entry=False,
        )
        proc.process(half_exit, exit_ctx)

        # cost_basis_released = 50500 * 0.001 = 50.5
        # position_notional was 101 → 101 - 50.5 = 50.5
        assert ctrl._state.position_notional == Decimal('50.5')
        assert ctrl._state.per_strategy_deployed['strat_001'] == Decimal('50.5')
        assert state.positions['trade_001'].size == Decimal('0.001')

    def test_avg_cost_basis_vwap_across_two_entry_fills(self) -> None:
        '''Two PARTIAL entry fills at the same price-and-fee-rate; verify
        avg_cost_basis accumulates correctly across fills (capital math
        forces uniform fees across fills to avoid proportional-fee
        deficits against fee_reserve).'''

        proc, ctrl, state, _, _tmp = _make_processor()

        reservation = ctrl.check_and_reserve(
            strategy_id='strat_001',
            order_notional=Decimal('200'),
            estimated_fees=Decimal('2'),
            strategy_budget=Decimal('5000'),
        )
        assert reservation.reservation is not None
        ctrl.send_order(reservation.reservation.reservation_id, 'cmd_enter')
        ctrl.order_ack('cmd_enter')

        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0'),
            entry_price=Decimal('50000'),
        )
        entry_ctx = OrderContext(
            command_id='cmd_enter',
            strategy_id='strat_001',
            trade_id='trade_001',
            side=OrderSide.BUY,
            order_size=Decimal('0.004'),
            order_notional=Decimal('200'),
            estimated_fees=Decimal('2'),
            is_entry=True,
        )

        # First PARTIAL: 0.002 @ 50000 = 100 notional + 1 fee
        result1 = proc.process(
            TradeOutcome(
                outcome_id='out_p1',
                command_id='cmd_enter',
                outcome_type=TradeOutcomeType.PARTIAL,
                timestamp=_now(),
                fill_size=Decimal('0.002'),
                fill_price=Decimal('50000'),
                fill_notional=Decimal('100'),
                actual_fees=Decimal('1'),
            ),
            entry_ctx,
        )
        assert result1.success is True
        # After first: avg_cost_basis = 101 / 0.002 = 50500
        assert state.positions['trade_001'].size == Decimal('0.002')
        assert state.positions['trade_001'].avg_cost_basis == Decimal('50500')

        # Second FILLED: 0.002 @ 50000 = 100 notional + 1 fee
        result2 = proc.process(
            TradeOutcome(
                outcome_id='out_p2',
                command_id='cmd_enter',
                outcome_type=TradeOutcomeType.FILLED,
                timestamp=_now(),
                fill_size=Decimal('0.002'),
                fill_price=Decimal('50000'),
                fill_notional=Decimal('100'),
                actual_fees=Decimal('1'),
            ),
            entry_ctx,
        )
        assert result2.success is True
        # After second: total cost = 101 + 101 = 202, total size = 0.004
        # avg_cost_basis = 202 / 0.004 = 50500 (same since uniform)
        assert state.positions['trade_001'].size == Decimal('0.004')
        assert state.positions['trade_001'].avg_cost_basis == Decimal('50500')

    def test_avg_cost_basis_vwap_pure_unit_test(self) -> None:
        '''Direct unit test on _grow_position math with two fills at
        different prices — bypasses CapitalController's proportional-fee
        machinery to focus on the VWAP-with-fees calculation.'''

        proc, _, state, _, _tmp = _make_processor()

        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0'),
            entry_price=Decimal('50000'),
        )
        ctx = OrderContext(
            command_id='cmd_enter',
            strategy_id='strat_001',
            trade_id='trade_001',
            side=OrderSide.BUY,
            order_size=Decimal('0.004'),
            order_notional=Decimal('200'),
            estimated_fees=Decimal('2'),
            is_entry=True,
        )

        # First fill: 0.002 @ 50000 = 100 notional, 1 fee → cost basis 101
        proc._grow_position(
            TradeOutcome(
                outcome_id='out_p1',
                command_id='cmd_enter',
                outcome_type=TradeOutcomeType.PARTIAL,
                timestamp=_now(),
                fill_size=Decimal('0.002'),
                fill_price=Decimal('50000'),
                fill_notional=Decimal('100'),
                actual_fees=Decimal('1'),
            ),
            ctx,
        )
        assert state.positions['trade_001'].size == Decimal('0.002')
        assert state.positions['trade_001'].avg_cost_basis == Decimal('50500')

        # Second fill: 0.002 @ 51000 = 102 notional, 1 fee → cost basis 103
        proc._grow_position(
            TradeOutcome(
                outcome_id='out_p2',
                command_id='cmd_enter',
                outcome_type=TradeOutcomeType.FILLED,
                timestamp=_now(),
                fill_size=Decimal('0.002'),
                fill_price=Decimal('51000'),
                fill_notional=Decimal('102'),
                actual_fees=Decimal('1'),
            ),
            ctx,
        )
        # Total cost: 101 + 103 = 204; total size: 0.004
        # avg_cost_basis = 204 / 0.004 = 51000
        assert state.positions['trade_001'].size == Decimal('0.004')
        assert state.positions['trade_001'].avg_cost_basis == Decimal('51000')
        # entry_price tracks fill-price VWAP (excludes fees)
        # = (0.002*50000 + 0.002*51000) / 0.004 = 50500
        assert state.positions['trade_001'].entry_price == Decimal('50500')

    def test_eight_round_trips_no_aggregate_leak(self) -> None:
        '''Pre-fix: eight ENTER/EXIT cycles at 10% each would trip the
        per-strategy budget cap on the second ENTER. Post-fix: aggregates
        return to zero after each round-trip and no denial fires.'''

        proc, ctrl, state, _, _tmp = _make_processor()

        for i in range(8):
            tid = f'trade_{i:03d}'
            cid_enter = f'cmd_enter_{i:03d}'
            cid_exit = f'cmd_exit_{i:03d}'

            _setup_working_order(ctrl, order_id=cid_enter)
            state.positions[tid] = Position(
                trade_id=tid,
                strategy_id='strat_001',
                symbol='BTCUSD',
                side=OrderSide.BUY,
                size=Decimal('0'),
                entry_price=Decimal('50000'),
            )
            proc.process(
                TradeOutcome(
                    outcome_id=f'out_e{i:03d}',
                    command_id=cid_enter,
                    outcome_type=TradeOutcomeType.FILLED,
                    timestamp=_now(),
                    fill_size=Decimal('0.002'),
                    fill_price=Decimal('50000'),
                    fill_notional=Decimal('100'),
                    actual_fees=Decimal('1'),
                ),
                OrderContext(
                    command_id=cid_enter,
                    strategy_id='strat_001',
                    trade_id=tid,
                    side=OrderSide.BUY,
                    order_size=Decimal('0.002'),
                    order_notional=Decimal('100'),
                    estimated_fees=Decimal('1'),
                    is_entry=True,
                ),
            )
            proc.process(
                TradeOutcome(
                    outcome_id=f'out_x{i:03d}',
                    command_id=cid_exit,
                    outcome_type=TradeOutcomeType.FILLED,
                    timestamp=_now(),
                    fill_size=Decimal('0.002'),
                    fill_price=Decimal('50000'),
                    fill_notional=Decimal('100'),
                    actual_fees=Decimal('1'),
                ),
                OrderContext(
                    command_id=cid_exit,
                    strategy_id='strat_001',
                    trade_id=tid,
                    side=OrderSide.SELL,
                    order_size=Decimal('0.002'),
                    order_notional=Decimal('100'),
                    estimated_fees=Decimal('1'),
                    is_entry=False,
                ),
            )
            assert ctrl._state.position_notional == _ZERO, (
                f'cycle {i}: position_notional leaked to {ctrl._state.position_notional}'
            )
            assert 'strat_001' not in ctrl._state.per_strategy_deployed, (
                f'cycle {i}: per_strategy_deployed leaked'
            )

    def test_order_exit_invariant_breach_when_release_exceeds_pool(self) -> None:
        '''Defensive guard: order_exit refuses to drive position_notional negative.'''

        proc, ctrl, state, _, _tmp = _make_processor()

        _setup_working_order(ctrl, order_id='cmd_enter')
        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0'),
            entry_price=Decimal('50000'),
        )
        # Tiny entry fill so position_notional is small
        proc.process(
            TradeOutcome(
                outcome_id='out_e',
                command_id='cmd_enter',
                outcome_type=TradeOutcomeType.FILLED,
                timestamp=_now(),
                fill_size=Decimal('0.001'),
                fill_price=Decimal('50000'),
                fill_notional=Decimal('50'),
                actual_fees=Decimal('0.5'),
            ),
            OrderContext(
                command_id='cmd_enter',
                strategy_id='strat_001',
                trade_id='trade_001',
                side=OrderSide.BUY,
                order_size=Decimal('0.001'),
                order_notional=Decimal('50'),
                estimated_fees=Decimal('0.5'),
                is_entry=True,
            ),
        )
        assert ctrl._state.position_notional == Decimal('50.5')

        # Manually inflate avg_cost_basis to force a release > position_notional
        state.positions['trade_001'].avg_cost_basis = Decimal('1000000')

        result = proc.process(
            TradeOutcome(
                outcome_id='out_x',
                command_id='cmd_exit',
                outcome_type=TradeOutcomeType.FILLED,
                timestamp=_now(),
                fill_size=Decimal('0.001'),
                fill_price=Decimal('50000'),
                fill_notional=Decimal('50'),
                actual_fees=Decimal('0.5'),
            ),
            OrderContext(
                command_id='cmd_exit',
                strategy_id='strat_001',
                trade_id='trade_001',
                side=OrderSide.SELL,
                order_size=Decimal('0.001'),
                order_notional=Decimal('50'),
                estimated_fees=Decimal('0.5'),
                is_entry=False,
            ),
        )
        assert result.success is False
        assert result.error_reason is not None
        assert 'order_exit failed' in result.error_reason

    def test_per_strategy_attribution_isolated_across_strategies(self) -> None:
        '''A strategy with multiple open positions exits one; only that
        strategy's deployed total decrements, and only by the exited
        position's cost basis. A second strategy's deployed total is
        untouched.'''

        proc, ctrl, state, _, _tmp = _make_processor()

        # strat_a: open two positions (trade_a1, trade_a2)
        for tid, oid in (('trade_a1', 'cmd_a1'), ('trade_a2', 'cmd_a2')):
            res = ctrl.check_and_reserve(
                strategy_id='strat_a',
                order_notional=Decimal('100'),
                estimated_fees=Decimal('1'),
                strategy_budget=Decimal('5000'),
            )
            assert res.reservation is not None
            ctrl.send_order(res.reservation.reservation_id, oid)
            ctrl.order_ack(oid)
            state.positions[tid] = Position(
                trade_id=tid,
                strategy_id='strat_a',
                symbol='BTCUSD',
                side=OrderSide.BUY,
                size=Decimal('0'),
                entry_price=Decimal('50000'),
            )
            proc.process(
                TradeOutcome(
                    outcome_id=f'out_e_{oid}',
                    command_id=oid,
                    outcome_type=TradeOutcomeType.FILLED,
                    timestamp=_now(),
                    fill_size=Decimal('0.002'),
                    fill_price=Decimal('50000'),
                    fill_notional=Decimal('100'),
                    actual_fees=Decimal('1'),
                ),
                OrderContext(
                    command_id=oid,
                    strategy_id='strat_a',
                    trade_id=tid,
                    side=OrderSide.BUY,
                    order_size=Decimal('0.002'),
                    order_notional=Decimal('100'),
                    estimated_fees=Decimal('1'),
                    is_entry=True,
                ),
            )

        # strat_b: open one position (trade_b1)
        res = ctrl.check_and_reserve(
            strategy_id='strat_b',
            order_notional=Decimal('100'),
            estimated_fees=Decimal('1'),
            strategy_budget=Decimal('5000'),
        )
        assert res.reservation is not None
        ctrl.send_order(res.reservation.reservation_id, 'cmd_b1')
        ctrl.order_ack('cmd_b1')
        state.positions['trade_b1'] = Position(
            trade_id='trade_b1',
            strategy_id='strat_b',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0'),
            entry_price=Decimal('50000'),
        )
        proc.process(
            TradeOutcome(
                outcome_id='out_e_b1',
                command_id='cmd_b1',
                outcome_type=TradeOutcomeType.FILLED,
                timestamp=_now(),
                fill_size=Decimal('0.002'),
                fill_price=Decimal('50000'),
                fill_notional=Decimal('100'),
                actual_fees=Decimal('1'),
            ),
            OrderContext(
                command_id='cmd_b1',
                strategy_id='strat_b',
                trade_id='trade_b1',
                side=OrderSide.BUY,
                order_size=Decimal('0.002'),
                order_notional=Decimal('100'),
                estimated_fees=Decimal('1'),
                is_entry=True,
            ),
        )

        # After all entries: strat_a deployed = 202, strat_b deployed = 101
        assert ctrl._state.per_strategy_deployed['strat_a'] == Decimal('202')
        assert ctrl._state.per_strategy_deployed['strat_b'] == Decimal('101')
        assert ctrl._state.position_notional == Decimal('303')

        # Exit ONE of strat_a's positions (trade_a1)
        proc.process(
            TradeOutcome(
                outcome_id='out_x_a1',
                command_id='cmd_x_a1',
                outcome_type=TradeOutcomeType.FILLED,
                timestamp=_now(),
                fill_size=Decimal('0.002'),
                fill_price=Decimal('50000'),
                fill_notional=Decimal('100'),
                actual_fees=Decimal('1'),
            ),
            OrderContext(
                command_id='cmd_x_a1',
                strategy_id='strat_a',
                trade_id='trade_a1',
                side=OrderSide.SELL,
                order_size=Decimal('0.002'),
                order_notional=Decimal('100'),
                estimated_fees=Decimal('1'),
                is_entry=False,
            ),
        )

        # Only strat_a's deployed should decrement, by exactly trade_a1's cost basis (101)
        assert ctrl._state.per_strategy_deployed['strat_a'] == Decimal('101'), (
            'strat_a deployed should drop by trade_a1 cost basis only'
        )
        assert ctrl._state.per_strategy_deployed['strat_b'] == Decimal('101'), (
            'strat_b deployed must be untouched by strat_a exit'
        )
        assert ctrl._state.position_notional == Decimal('202')
        # trade_a1 closed; trade_a2 and trade_b1 remain
        assert 'trade_a1' not in state.positions
        assert 'trade_a2' in state.positions
        assert 'trade_b1' in state.positions


class TestExitFillCapitalGuardOrdering:
    '''Capital `order_exit` is called BEFORE `_reduce_position` mutates
    the position so an `INVARIANT_BREACH` (cost basis would drive
    `position_notional` negative) returns `success=False` without
    leaving the position deleted while capital aggregates still carry
    the closed position's cost basis.

    Reachable on every post-crash EXIT when `_reconcile_capital`
    rebuilt `position_notional` from `qty * avg_cost_basis` (fee-
    inclusive) but the value stored falls below `cost_basis_released`
    due to other adjustments. Pre-fix the position was already
    reduced/deleted at the time of the breach, leaving capital and
    position state divergent.
    '''

    def test_invariant_breach_does_not_mutate_position(self) -> None:
        proc, ctrl, state, _, _tmp = _make_processor()

        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0.01'),
            entry_price=Decimal('50000'),
            avg_cost_basis=Decimal('1000000'),
        )
        ctrl._state.position_notional = Decimal('50')

        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.FILLED,
            timestamp=_now(),
            fill_size=Decimal('0.01'),
            fill_price=Decimal('50000'),
            fill_notional=Decimal('500'),
            actual_fees=Decimal('1'),
        )

        result = proc.process(outcome, _exit_context())

        assert result.success is False
        assert result.error_reason is not None
        assert 'order_exit failed' in result.error_reason
        assert 'trade_001' in state.positions
        assert state.positions['trade_001'].size == Decimal('0.01')
        assert ctrl._state.position_notional == Decimal('50')

    def test_overfill_does_not_decrement_capital_before_reduce_raises(
        self,
    ) -> None:
        '''Overfill EXIT (`fill_size > position.size`) must leave
        `CapitalState` untouched. `_reduce_position` raises
        `RuntimeError` on overfill; if `_compute_exit_cost_basis` did
        not gate this case, `order_exit` would already have decremented
        `position_notional` and `per_strategy_deployed` by the time the
        raise happened, leaving capital aggregates and positions
        divergent (capital missing the closed position's cost basis
        while the position still holds its full size).
        '''

        proc, ctrl, state, _, _tmp = _make_processor()

        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0.01'),
            entry_price=Decimal('50000'),
            avg_cost_basis=Decimal('50000'),
            pending_exit=Decimal('0.01'),
        )
        ctrl._state.position_notional = Decimal('500')
        ctrl._state.per_strategy_deployed['strat_001'] = Decimal('500')

        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.FILLED,
            timestamp=_now(),
            fill_size=Decimal('0.02'),
            fill_price=Decimal('51000'),
            fill_notional=Decimal('1020'),
            actual_fees=Decimal('1'),
        )

        with pytest.raises(RuntimeError, match='exceeds position size'):
            proc.process(outcome, _exit_context())

        assert ctrl._state.position_notional == Decimal('500')
        assert ctrl._state.per_strategy_deployed['strat_001'] == Decimal('500')
        assert 'trade_001' in state.positions
        assert state.positions['trade_001'].size == Decimal('0.01')

    def test_strategy_id_mismatch_skips_capital_decrement(self) -> None:
        '''If `context.strategy_id` diverges from `position.strategy_id`
        (bad context wiring or an EXIT referencing another strategy's
        trade), the EXIT must NOT decrement the wrong
        `per_strategy_deployed` bucket. `_compute_exit_cost_basis`
        returns `None` and logs WARNING; the position is still
        reduced (the position layer is single-source-of-truth on
        `position.strategy_id`) but capital aggregates are left to a
        later boot reconcile rather than corrupted now.
        '''

        proc, ctrl, state, _, _tmp = _make_processor()

        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_owner',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0.01'),
            entry_price=Decimal('50000'),
            avg_cost_basis=Decimal('50000'),
            pending_exit=Decimal('0.01'),
        )
        ctrl._state.position_notional = Decimal('500')
        ctrl._state.per_strategy_deployed['strat_owner'] = Decimal('500')

        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.FILLED,
            timestamp=_now(),
            fill_size=Decimal('0.01'),
            fill_price=Decimal('51000'),
            fill_notional=Decimal('510'),
            actual_fees=Decimal('1'),
        )
        wrong_strategy_ctx = OrderContext(
            command_id='cmd_001',
            strategy_id='strat_intruder',
            trade_id='trade_001',
            side=OrderSide.SELL,
            order_size=Decimal('0.01'),
            order_notional=Decimal('510'),
            estimated_fees=Decimal('1'),
            is_entry=False,
        )

        result = proc.process(outcome, wrong_strategy_ctx)

        assert result.success is True
        assert result.capital_updated is False
        assert ctrl._state.position_notional == Decimal('500')
        assert ctrl._state.per_strategy_deployed['strat_owner'] == Decimal('500')
        assert 'strat_intruder' not in ctrl._state.per_strategy_deployed


class TestExitRejectClearsPendingExit:
    '''EXIT REJECT/CANCEL must clear `pending_exit` even when
    `CapitalController.order_reject` / `order_cancel` fails because
    EXIT orders are not tracked in `_orders` (they bypass
    `bridge_to_capital`; the actual failure category is
    INVARIANT_BREACH for `order_reject` and EXPECTED_MISS for
    `order_cancel`). Pre-fix the failure short-circuited the handler
    and left `pending_exit` stuck, so a subsequent retry was denied
    with `INTAKE_EXIT_SIZE_EXCEEDS_REMAINING`.
    '''

    def test_reject_clears_pending_exit_when_capital_op_fails(self) -> None:
        proc, _ctrl, state, _, _tmp = _make_processor()

        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0.01'),
            entry_price=Decimal('50000'),
            pending_exit=Decimal('0.01'),
        )

        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_unknown',
            outcome_type=TradeOutcomeType.REJECTED,
            timestamp=_now(),
            reject_reason='venue_reject',
        )

        result = proc.process(
            outcome,
            OrderContext(
                command_id='cmd_unknown',
                strategy_id='strat_001',
                trade_id='trade_001',
                side=OrderSide.SELL,
                order_size=Decimal('0.01'),
                order_notional=Decimal('100'),
                estimated_fees=Decimal('1'),
                is_entry=False,
            ),
        )

        assert result.success is True
        assert result.position_updated is True
        assert result.capital_updated is False
        assert state.positions['trade_001'].pending_exit == _ZERO

    def test_cancel_clears_pending_exit_when_capital_op_fails(self) -> None:
        proc, _ctrl, state, _, _tmp = _make_processor()

        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0.01'),
            entry_price=Decimal('50000'),
            pending_exit=Decimal('0.005'),
        )

        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_unknown',
            outcome_type=TradeOutcomeType.CANCELED,
            timestamp=_now(),
            remaining_size=Decimal('0.005'),
        )

        result = proc.process(
            outcome,
            OrderContext(
                command_id='cmd_unknown',
                strategy_id='strat_001',
                trade_id='trade_001',
                side=OrderSide.SELL,
                order_size=Decimal('0.005'),
                order_notional=Decimal('50'),
                estimated_fees=Decimal('0.5'),
                is_entry=False,
            ),
        )

        assert result.success is True
        assert result.position_updated is True
        assert result.capital_updated is False
        assert state.positions['trade_001'].pending_exit == _ZERO

    def test_entry_reject_still_fails_when_capital_op_fails(self) -> None:
        '''Regression — ENTER REJECT with no tracked order must still
        propagate failure (no pending_exit to clear; the failure is real).'''

        proc, _, state, _, _tmp = _make_processor()
        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0.01'),
            entry_price=Decimal('50000'),
        )

        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.REJECTED,
            timestamp=_now(),
            reject_reason='venue_reject',
        )

        result = proc.process(outcome, _entry_context())
        assert result.success is False
        assert result.error_reason is not None
        assert 'order_reject failed' in result.error_reason


class _CountingLock:
    '''threading.Lock-shaped wrapper that counts acquires.

    Used to verify `OutcomeProcessor` actually wraps its
    `state.positions` mutations with the provided lock — a structural
    check that survives without depending on a flaky race window.
    '''

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.enter_count = 0

    def __enter__(self) -> _CountingLock:
        self._lock.acquire()
        self.enter_count += 1
        return self

    def __exit__(self, *_args: object) -> None:
        self._lock.release()


class TestPositionsLockHonoredByWriter:
    '''MAJOR-J: `OutcomeProcessor._grow_position` and `_reduce_position`
    must wrap their `state.positions` mutations with the
    `positions_lock` passed at construction time. Pre-fix the lock
    was never threaded into `OutcomeProcessor`; mutations ran
    unguarded on the OutcomeLoop thread while PredictLoop's
    `_build_strategy_context` reader iterated `state.positions.values()`
    under the lock — a concurrent `del` mid-iteration could fire
    `RuntimeError: dictionary changed size during iteration`,
    swallowed by PredictLoop's top-level except → silently dropped
    strategy ticks.
    '''

    def test_grow_position_acquires_positions_lock(self) -> None:
        lock = _CountingLock()
        capital_state = CapitalState(capital_pool=_POOL)
        instance_state = InstanceState(capital=capital_state)
        controller = CapitalController(capital_state)
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp))
            proc = OutcomeProcessor(
                controller, instance_state, store, positions_lock=lock,
            )
            res = controller.check_and_reserve(
                strategy_id='strat_001',
                order_notional=Decimal('250'),
                estimated_fees=Decimal('1'),
                strategy_budget=Decimal('5000'),
            )
            assert res.reservation is not None
            controller.send_order(res.reservation.reservation_id, 'cmd_001')
            controller.order_ack('cmd_001')
            instance_state.positions['trade_001'] = Position(
                trade_id='trade_001',
                strategy_id='strat_001',
                symbol='BTCUSD',
                side=OrderSide.BUY,
                size=Decimal('0.005'),
                entry_price=Decimal('50000'),
                avg_cost_basis=Decimal('50000'),
            )

            outcome = TradeOutcome(
                outcome_id='out_001',
                command_id='cmd_001',
                outcome_type=TradeOutcomeType.FILLED,
                timestamp=_now(),
                fill_size=Decimal('0.005'),
                fill_price=Decimal('50000'),
                fill_notional=Decimal('250'),
                actual_fees=Decimal('0'),
            )

            result = proc.process(outcome, _entry_context())

            assert result.success is True
            assert lock.enter_count >= 1

    def test_reduce_position_acquires_positions_lock(self) -> None:
        lock = _CountingLock()
        capital_state = CapitalState(capital_pool=_POOL)
        instance_state = InstanceState(capital=capital_state)
        controller = CapitalController(capital_state)
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp))
            proc = OutcomeProcessor(
                controller, instance_state, store, positions_lock=lock,
            )
            instance_state.positions['trade_001'] = Position(
                trade_id='trade_001',
                strategy_id='strat_001',
                symbol='BTCUSD',
                side=OrderSide.BUY,
                size=Decimal('0.01'),
                entry_price=Decimal('50000'),
                avg_cost_basis=Decimal('50000'),
                pending_exit=Decimal('0.01'),
            )
            controller._state.position_notional = Decimal('500')
            controller._state.per_strategy_deployed['strat_001'] = Decimal('500')

            outcome = TradeOutcome(
                outcome_id='out_001',
                command_id='cmd_001',
                outcome_type=TradeOutcomeType.FILLED,
                timestamp=_now(),
                fill_size=Decimal('0.01'),
                fill_price=Decimal('51000'),
                fill_notional=Decimal('510'),
                actual_fees=Decimal('1'),
            )

            proc.process(outcome, _exit_context())

            assert lock.enter_count >= 1
            assert 'trade_001' not in instance_state.positions

    def test_concurrent_iteration_with_writer_does_not_raise(self) -> None:
        '''End-to-end: a foreground thread iterates `state.positions.values()`
        under the lock; a worker thread runs a tight loop of grow/reduce
        cycles via `OutcomeProcessor`. With the lock plumbed through, the
        reader sees no `RuntimeError: dictionary changed size during
        iteration`. Pre-fix this would race occasionally; post-fix the
        invariant holds.
        '''

        real_lock = threading.Lock()
        capital_state = CapitalState(capital_pool=_POOL)
        instance_state = InstanceState(capital=capital_state)
        controller = CapitalController(capital_state)
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp))
            proc = OutcomeProcessor(
                controller, instance_state, store, positions_lock=real_lock,
            )

            stop_event = threading.Event()
            iteration_failures: list[Exception] = []

            def reader() -> None:
                try:
                    while not stop_event.is_set():
                        with real_lock:
                            for _ in instance_state.positions.values():
                                pass
                except Exception as exc:
                    iteration_failures.append(exc)

            def writer() -> None:
                try:
                    for i in range(200):
                        trade_id = f'trade_{i}'
                        order_id = f'cmd_{i}'
                        with real_lock:
                            instance_state.positions[trade_id] = Position(
                                trade_id=trade_id,
                                strategy_id='strat_001',
                                symbol='BTCUSD',
                                side=OrderSide.BUY,
                                size=Decimal('0.001'),
                                entry_price=Decimal('50000'),
                                avg_cost_basis=Decimal('50000'),
                                pending_exit=Decimal('0.001'),
                            )
                            controller._state.position_notional = Decimal('50')
                            controller._state.per_strategy_deployed['strat_001'] = Decimal('50')

                        outcome = TradeOutcome(
                            outcome_id=f'out_{i}',
                            command_id=order_id,
                            outcome_type=TradeOutcomeType.FILLED,
                            timestamp=_now(),
                            fill_size=Decimal('0.001'),
                            fill_price=Decimal('50000'),
                            fill_notional=Decimal('50'),
                            actual_fees=Decimal('0'),
                        )
                        ctx = OrderContext(
                            command_id=order_id,
                            strategy_id='strat_001',
                            trade_id=trade_id,
                            side=OrderSide.SELL,
                            order_size=Decimal('0.001'),
                            order_notional=Decimal('50'),
                            estimated_fees=Decimal('0'),
                            is_entry=False,
                        )
                        proc.process(outcome, ctx)
                finally:
                    stop_event.set()

            r = threading.Thread(target=reader, daemon=True)
            w = threading.Thread(target=writer, daemon=True)
            r.start()
            w.start()
            w.join(timeout=15)
            stop_event.set()
            r.join(timeout=15)

            assert not iteration_failures, (
                f'reader observed exceptions during iteration: '
                f'{iteration_failures[:3]}'
            )


class TestFinalMajor02RiskLockCoverage:
    '''FINAL-MAJOR-02: state.risk.per_strategy and StrategyRiskState
    fields are mutated by OutcomeProcessor on the OutcomeLoop thread
    while the validator's to_risk_check_metrics (PredictLoop /
    TimerLoop) and HealthLoop's state_store.refresh_rolling_losses
    (daemon timer) iterate the dict. Pre-fix the writer's first-fill
    insert at line 491 could fire RuntimeError: dictionary changed
    size during iteration on either reader; the field-level += ops
    could lose increments via torn read-modify-write.

    Post-fix all three call sites acquire state.risk.lock (set by the
    launcher to the shared positions_lock).
    '''

    def test_writer_vs_refresher_no_iteration_error(self) -> None:
        '''Writer thread tight-loops _update_strategy_risk_state for
        new strategy_ids while the foreground thread tight-loops
        StateStore.refresh_rolling_losses(state). Pre-fix the dict
        insert during iteration would raise; post-fix the lock
        serialises and all writes land.
        '''

        capital_state = CapitalState(capital_pool=Decimal('100000'))
        instance_state = InstanceState(capital=capital_state)
        controller = CapitalController(capital_state)

        risk_lock = threading.Lock()
        instance_state.risk.lock = risk_lock

        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp))
            proc = OutcomeProcessor(controller, instance_state, store)

            stop_event = threading.Event()
            iteration_failures: list[Exception] = []

            def writer() -> None:
                try:
                    for i in range(500):
                        proc._update_strategy_risk_state(
                            f'strat_{i}', Decimal('-1.0'),
                        )
                finally:
                    stop_event.set()

            def refresher() -> None:
                try:
                    while not stop_event.is_set():
                        store.refresh_rolling_losses(instance_state)
                except Exception as exc:
                    iteration_failures.append(exc)

            r = threading.Thread(target=refresher, daemon=True)
            w = threading.Thread(target=writer, daemon=True)
            r.start()
            w.start()
            w.join(timeout=15)
            stop_event.set()
            r.join(timeout=15)

            assert not iteration_failures, (
                f'refresher observed exceptions during iteration: '
                f'{iteration_failures[:3]}'
            )
            assert len(instance_state.risk.per_strategy) == 500, (
                f'writer did not complete: '
                f'len={len(instance_state.risk.per_strategy)}'
            )

    def test_writer_vs_validator_metrics_no_iteration_error(self) -> None:
        '''Writer thread tight-loops _update_strategy_risk_state for
        new strategy_ids while a reader thread tight-loops
        state.risk.to_risk_check_metrics() (the validator's path).
        Pre-fix the property iterators (rolling_loss_24h/7d/30d sum
        comprehensions) raced with the dict insert; post-fix
        to_risk_check_metrics acquires state.risk.lock and the reader
        sees a consistent snapshot.
        '''

        capital_state = CapitalState(capital_pool=Decimal('100000'))
        instance_state = InstanceState(capital=capital_state)
        controller = CapitalController(capital_state)

        risk_lock = threading.Lock()
        instance_state.risk.lock = risk_lock

        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp))
            proc = OutcomeProcessor(controller, instance_state, store)

            stop_event = threading.Event()
            reader_failures: list[Exception] = []

            def writer() -> None:
                try:
                    for i in range(500):
                        proc._update_strategy_risk_state(
                            f'strat_{i}', Decimal('-2.0'),
                        )
                finally:
                    stop_event.set()

            def reader() -> None:
                try:
                    while not stop_event.is_set():
                        instance_state.risk.to_risk_check_metrics()
                except Exception as exc:
                    reader_failures.append(exc)

            r = threading.Thread(target=reader, daemon=True)
            w = threading.Thread(target=writer, daemon=True)
            r.start()
            w.start()
            w.join(timeout=15)
            stop_event.set()
            r.join(timeout=15)

            assert not reader_failures, (
                f'reader observed exceptions during property iteration: '
                f'{reader_failures[:3]}'
            )
            assert len(instance_state.risk.per_strategy) == 500


class TestFinalMajor08RealizedPnlIsNetOfFees:
    '''FINAL-MAJOR-08: pre-fix `_reduce_position` produced GROSS
    realized_pnl `(fill_price - entry_price) * fill_size` and ignored
    `outcome.actual_fees`. The gross value flowed verbatim into
    `strategy_realized_pnl`, `cumulative_realized_pnl`, equity,
    equity_hwm, and the rolling-loss windows. Rolling-loss / drawdown
    gates therefore fired LATER than they should by the cumulative
    fee total. On testnet `fee_rate=0` so unobserved at MMVP today;
    mainnet-fatal once Praxis TD-030 flips fee_rate non-zero.

    Post-fix `_reduce_position` returns `gross_pnl - actual_fees` so
    the net value flows through the entire risk derivation chain.
    '''

    def test_winning_exit_realized_pnl_is_net_of_fees(self) -> None:
        proc, ctrl, state, _, _tmp = _make_processor()
        _setup_working_order(ctrl)
        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('1'),
            entry_price=Decimal('100'),
            avg_cost_basis=Decimal('100'),
        )
        _prime_open_position_capital(
            ctrl, 'strat_001', Decimal('1'), Decimal('100'),
        )

        # gross win = (110 - 100) * 1 = 10; fees 0.5 -> net 9.5
        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.FILLED,
            timestamp=_now(),
            fill_size=Decimal('1'),
            fill_price=Decimal('110'),
            fill_notional=Decimal('110'),
            actual_fees=Decimal('0.5'),
        )
        result = proc.process(outcome, _exit_context())
        assert result.success is True

        srs = state.risk.per_strategy['strat_001']
        assert srs.strategy_realized_pnl == Decimal('9.5')

    def test_losing_exit_rolling_loss_includes_exit_fee(self) -> None:
        '''Rolling-loss windows track abs(net loss). When a position
        exits at a loss AND incurs an exit fee, the rolling-loss
        bucket grows by gross_loss + fee. Pre-fix the fee was lost
        from the rolling-loss accounting.
        '''

        proc, ctrl, state, _, _tmp = _make_processor()
        _setup_working_order(ctrl)
        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('1'),
            entry_price=Decimal('100'),
            avg_cost_basis=Decimal('100'),
        )
        _prime_open_position_capital(
            ctrl, 'strat_001', Decimal('1'), Decimal('100'),
        )

        # gross loss = (90 - 100) * 1 = -10; fees 1 -> net -11; rolling = 11
        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.FILLED,
            timestamp=_now(),
            fill_size=Decimal('1'),
            fill_price=Decimal('90'),
            fill_notional=Decimal('90'),
            actual_fees=Decimal('1'),
        )
        result = proc.process(outcome, _exit_context())
        assert result.success is True

        srs = state.risk.per_strategy['strat_001']
        assert srs.strategy_realized_pnl == Decimal('-11')
        assert srs.rolling_loss_24h == Decimal('11')
        assert srs.rolling_loss_7d == Decimal('11')
        assert srs.rolling_loss_30d == Decimal('11')

    def test_zero_fee_exit_unchanged_from_pre_fix(self) -> None:
        '''Sanity: when fees are zero the result equals gross PnL,
        i.e. behaviour matches pre-M08 on testnet (fee_rate=0).
        '''

        proc, ctrl, state, _, _tmp = _make_processor()
        _setup_working_order(ctrl)
        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('1'),
            entry_price=Decimal('100'),
            avg_cost_basis=Decimal('100'),
        )
        _prime_open_position_capital(
            ctrl, 'strat_001', Decimal('1'), Decimal('100'),
        )

        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.FILLED,
            timestamp=_now(),
            fill_size=Decimal('1'),
            fill_price=Decimal('105'),
            fill_notional=Decimal('105'),
            actual_fees=Decimal('0'),
        )
        result = proc.process(outcome, _exit_context())
        assert result.success is True

        srs = state.risk.per_strategy['strat_001']
        assert srs.strategy_realized_pnl == Decimal('5')


class TestExitFillWalAppendFailureRollback:
    '''PR #55 round-10 review: if `state_store.append_event` raises
    inside `_handle_fill`'s critical section (transient I/O / WAL
    validation failure), the per-strategy and instance-level risk
    fields must NOT have been mutated. Pre-fix the order was
    mutate-then-append, leaving in-memory state inconsistent with WAL
    until restart — `refresh_rolling_losses` only rebuilds rolling-loss
    windows, NOT `strategy_realized_pnl` / `cumulative_realized_pnl`,
    so the inconsistency persisted until next boot.

    Post-fix the order is append-then-mutate inside the same
    `state.risk.lock_cm()` acquisition. Append raise → no in-memory
    change, caller sees the exception, in-memory + WAL stay in sync.
    The single lock acquisition still closes the round-7 race because
    a refresher cannot interleave between the append and the mutation
    (it would block on the lock).
    '''

    def test_append_event_raise_leaves_risk_state_unchanged(self) -> None:
        '''Monkey-patch `StateStore.append_event` to raise on the
        EXIT FILL. Assert: (a) `process` returns success=False or
        raises, (b) `strategy_realized_pnl` is still _ZERO,
        (c) `rolling_loss_*` are still _ZERO,
        (d) `cumulative_realized_pnl` is still _ZERO.
        '''

        from unittest.mock import patch

        proc, ctrl, state, store, _tmp = _make_processor()

        _setup_working_order(ctrl, order_id='cmd_enter')
        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0'),
            entry_price=Decimal('50000'),
        )
        entry_outcome = TradeOutcome(
            outcome_id='out_enter',
            command_id='cmd_enter',
            outcome_type=TradeOutcomeType.FILLED,
            timestamp=_now(),
            fill_size=Decimal('0.002'),
            fill_price=Decimal('50000'),
            fill_notional=Decimal('100'),
            actual_fees=Decimal('1'),
        )
        entry_ctx = OrderContext(
            command_id='cmd_enter',
            strategy_id='strat_001',
            trade_id='trade_001',
            side=OrderSide.BUY,
            order_size=Decimal('0.002'),
            order_notional=Decimal('100'),
            estimated_fees=Decimal('1'),
            is_entry=True,
        )
        proc.process(entry_outcome, entry_ctx)

        srs_pre = state.risk.per_strategy.get('strat_001')
        pre_realized_pnl = (
            srs_pre.strategy_realized_pnl if srs_pre is not None else _ZERO
        )
        pre_rolling_24h = (
            srs_pre.rolling_loss_24h if srs_pre is not None else _ZERO
        )
        pre_cumulative = state.risk.cumulative_realized_pnl

        exit_outcome = TradeOutcome(
            outcome_id='out_exit',
            command_id='cmd_exit',
            outcome_type=TradeOutcomeType.FILLED,
            timestamp=_now(),
            fill_size=Decimal('0.002'),
            fill_price=Decimal('51000'),
            fill_notional=Decimal('102'),
            actual_fees=Decimal('1'),
        )
        exit_ctx = OrderContext(
            command_id='cmd_exit',
            strategy_id='strat_001',
            trade_id='trade_001',
            side=OrderSide.SELL,
            order_size=Decimal('0.002'),
            order_notional=Decimal('102'),
            estimated_fees=Decimal('1'),
            is_entry=False,
        )

        injected_failure = OSError('disk full')
        with (
            patch.object(store, 'append_event', side_effect=injected_failure),
            pytest.raises(OSError, match='disk full'),
        ):
            proc.process(exit_outcome, exit_ctx)

        srs_post = state.risk.per_strategy.get('strat_001')
        post_realized_pnl = (
            srs_post.strategy_realized_pnl if srs_post is not None else _ZERO
        )
        post_rolling_24h = (
            srs_post.rolling_loss_24h if srs_post is not None else _ZERO
        )
        post_cumulative = state.risk.cumulative_realized_pnl

        assert post_realized_pnl == pre_realized_pnl, (
            f'PR #55 round-10: WAL append failure must NOT mutate '
            f'strategy_realized_pnl. pre={pre_realized_pnl} '
            f'post={post_realized_pnl}'
        )
        assert post_rolling_24h == pre_rolling_24h, (
            f'PR #55 round-10: WAL append failure must NOT mutate '
            f'rolling_loss_24h. pre={pre_rolling_24h} '
            f'post={post_rolling_24h}'
        )
        assert post_cumulative == pre_cumulative, (
            f'PR #55 round-10: WAL append failure must NOT mutate '
            f'cumulative_realized_pnl. pre={pre_cumulative} '
            f'post={post_cumulative}'
        )


class TestOutcomeIdIdempotency:
    '''Round-18 MAJOR-004: process() must dedup by outcome_id so that
    Praxis's delivery retry / boot-replay paths can call it more than
    once without double-mutating capital or position state. The dedup
    is in-memory; cross-restart safety comes from Praxis-side
    OutcomeAcked tracking that filters un-acked outcomes at boot.
    '''

    def test_duplicate_ack_returns_no_op_success(self) -> None:
        '''Second call with same outcome_id returns success without
        re-incrementing capital aggregates.'''

        proc, ctrl, _, _, _tmp = _make_processor()
        _setup_in_flight_order(ctrl)

        outcome = TradeOutcome(
            outcome_id='out_dedup_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.ACK,
            timestamp=_now(),
        )

        first = proc.process(outcome, _entry_context())
        assert first.success is True
        assert first.capital_updated is True

        snapshot_in_flight = ctrl._state.in_flight_order_notional
        snapshot_working = ctrl._state.working_order_notional

        second = proc.process(outcome, _entry_context())

        assert second.success is True
        assert second.capital_updated is False
        assert second.position_updated is False
        assert ctrl._state.in_flight_order_notional == snapshot_in_flight
        assert ctrl._state.working_order_notional == snapshot_working

    def test_duplicate_fill_does_not_double_mutate_capital_or_position(
        self,
    ) -> None:
        '''Same fill outcome processed twice must not double-decrement
        capital aggregates or double-update the position size.'''

        proc, ctrl, state, _, _tmp = _make_processor()
        _setup_working_order(ctrl)
        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0.01'),
            entry_price=Decimal('50000'),
        )

        outcome = TradeOutcome(
            outcome_id='out_dedup_002',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.FILLED,
            timestamp=_now(),
            fill_size=Decimal('0.01'),
            fill_price=Decimal('50000'),
            fill_notional=Decimal('100'),
            actual_fees=Decimal('1'),
        )

        first = proc.process(outcome, _entry_context())
        assert first.success is True

        snapshot_position_notional = ctrl._state.position_notional
        snapshot_working = ctrl._state.working_order_notional
        snapshot_position_size = state.positions['trade_001'].size

        second = proc.process(outcome, _entry_context())

        assert second.success is True
        assert second.capital_updated is False
        assert second.position_updated is False
        assert ctrl._state.position_notional == snapshot_position_notional
        assert ctrl._state.working_order_notional == snapshot_working
        assert state.positions['trade_001'].size == snapshot_position_size

    def test_failed_first_attempt_does_not_poison_dedup(self) -> None:
        '''A previously failed process attempt must NOT add the
        outcome_id to the dedup set — the caller may legitimately
        retry with a fixed-up context and the second attempt should
        run normally.'''

        proc, ctrl, _, _, _tmp = _make_processor()
        _setup_in_flight_order(ctrl)

        outcome = TradeOutcome(
            outcome_id='out_dedup_003',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.ACK,
            timestamp=_now(),
        )
        bad_ctx = OrderContext(
            command_id='cmd_other',
            strategy_id='strat_001',
            trade_id=None,
            side=OrderSide.BUY,
            order_size=Decimal('0.01'),
            order_notional=Decimal('100'),
            estimated_fees=Decimal('1'),
            is_entry=True,
        )

        first = proc.process(outcome, bad_ctx)
        assert first.success is False

        second = proc.process(outcome, _entry_context())

        assert second.success is True
        assert second.capital_updated is True

    def test_dedup_is_per_outcome_id_not_per_command_id(self) -> None:
        '''Two outcomes with the same command_id but different
        outcome_ids (e.g., ACK then FILL on the same order) must
        BOTH be processed.'''

        proc, ctrl, state, _, _tmp = _make_processor()
        _setup_in_flight_order(ctrl)
        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0.01'),
            entry_price=Decimal('50000'),
        )

        ack = TradeOutcome(
            outcome_id='out_dedup_004_ack',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.ACK,
            timestamp=_now(),
        )
        fill = TradeOutcome(
            outcome_id='out_dedup_004_fill',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.FILLED,
            timestamp=_now(),
            fill_size=Decimal('0.01'),
            fill_price=Decimal('50000'),
            fill_notional=Decimal('100'),
            actual_fees=Decimal('1'),
        )

        ack_result = proc.process(ack, _entry_context())
        fill_result = proc.process(fill, _entry_context())

        assert ack_result.success is True
        assert fill_result.success is True
        assert fill_result.position_updated is True


def _full_close_exit_context(
    trade_id: str = 'trade_001',
    order_size: Decimal = Decimal('0.01'),
) -> OrderContext:
    return OrderContext(
        command_id='cmd_001',
        strategy_id='strat_001',
        trade_id=trade_id,
        side=OrderSide.SELL,
        order_size=order_size,
        order_notional=Decimal('100'),
        estimated_fees=Decimal('1'),
        is_entry=False,
        intended_full_close=True,
    )


class TestDustClose:
    '''Dust handling via `intended_full_close` and `close_as_dust`.

    Covers Vaquum/Nexus#82 — sub-lot residue from snapped EXIT fills
    must move to `state.account_dust`, and intake-rejected full-close
    EXITs must close via `close_as_dust`.
    '''

    def test_full_close_exit_fill_with_residue_moves_to_account_dust(self) -> None:
        proc, ctrl, state, _, _tmp = _make_processor()
        _setup_working_order(ctrl)

        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0.01000842'),
            entry_price=Decimal('50000'),
            pending_exit=Decimal('0.01000842'),
        )

        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.FILLED,
            timestamp=_now(),
            fill_size=Decimal('0.01000000'),
            fill_price=Decimal('51000'),
            fill_notional=Decimal('100'),
            actual_fees=Decimal('1'),
        )

        proc.process(outcome, _full_close_exit_context())

        assert 'trade_001' not in state.positions
        assert state.account_dust['BTCUSD'] == Decimal('0.00000842')

    def test_full_close_exit_fill_no_residue_no_dust(self) -> None:
        proc, ctrl, state, _, _tmp = _make_processor()
        _setup_working_order(ctrl)

        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0.01'),
            entry_price=Decimal('50000'),
            pending_exit=Decimal('0.01'),
        )

        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.FILLED,
            timestamp=_now(),
            fill_size=Decimal('0.01'),
            fill_price=Decimal('51000'),
            fill_notional=Decimal('100'),
            actual_fees=Decimal('1'),
        )

        proc.process(outcome, _full_close_exit_context())

        assert 'trade_001' not in state.positions
        assert state.account_dust == {}

    def test_partial_exit_fill_does_not_move_residue_to_dust(self) -> None:
        proc, ctrl, state, _, _tmp = _make_processor()
        _setup_working_order(ctrl)

        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0.02'),
            entry_price=Decimal('50000'),
            pending_exit=Decimal('0.01'),
        )

        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.FILLED,
            timestamp=_now(),
            fill_size=Decimal('0.01'),
            fill_price=Decimal('51000'),
            fill_notional=Decimal('100'),
            actual_fees=Decimal('1'),
        )

        proc.process(outcome, _exit_context())

        assert state.positions['trade_001'].size == Decimal('0.01')
        assert state.account_dust == {}

    def test_close_as_dust_removes_position_and_credits_dust(self) -> None:
        proc, _ctrl, state, _, _tmp = _make_processor()

        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0.00000842'),
            entry_price=Decimal('50000'),
            avg_cost_basis=Decimal('50050'),
        )

        closed = proc.close_as_dust(
            trade_id='trade_001',
            reason='INTAKE_BELOW_MIN_QTY qty=0.00000842 lot_min=0.00001',
            dust_close_id='dust-cmd_001',
        )

        assert closed is True
        assert 'trade_001' not in state.positions
        assert state.account_dust['BTCUSD'] == Decimal('0.00000842')

    def test_close_as_dust_idempotent_under_same_dedup_key(self) -> None:
        proc, _ctrl, state, _, _tmp = _make_processor()

        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0.00000842'),
            entry_price=Decimal('50000'),
            avg_cost_basis=Decimal('50050'),
        )

        first = proc.close_as_dust(
            trade_id='trade_001',
            reason='r',
            dust_close_id='dust-cmd_001',
        )

        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0.00009999'),
            entry_price=Decimal('50000'),
            avg_cost_basis=Decimal('50050'),
        )

        second = proc.close_as_dust(
            trade_id='trade_001',
            reason='r',
            dust_close_id='dust-cmd_001',
        )

        assert first is True
        assert second is False
        assert 'trade_001' in state.positions
        assert state.account_dust['BTCUSD'] == Decimal('0.00000842')

    def test_close_as_dust_missing_position_returns_false_no_op(self) -> None:
        proc, _ctrl, state, _, _tmp = _make_processor()

        closed = proc.close_as_dust(
            trade_id='trade_missing',
            reason='r',
            dust_close_id='dust-cmd_missing',
        )

        assert closed is False
        assert state.account_dust == {}

    def test_close_as_dust_accumulates_residue_per_symbol(self) -> None:
        proc, _ctrl, state, _, _tmp = _make_processor()

        state.positions['trade_001'] = Position(
            trade_id='trade_001',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0.00000842'),
            entry_price=Decimal('50000'),
            avg_cost_basis=Decimal('50050'),
        )

        proc.close_as_dust(
            trade_id='trade_001',
            reason='r',
            dust_close_id='dust-cmd_001',
        )

        state.positions['trade_002'] = Position(
            trade_id='trade_002',
            strategy_id='strat_001',
            symbol='BTCUSD',
            side=OrderSide.BUY,
            size=Decimal('0.00000300'),
            entry_price=Decimal('50000'),
            avg_cost_basis=Decimal('50050'),
        )

        proc.close_as_dust(
            trade_id='trade_002',
            reason='r',
            dust_close_id='dust-cmd_002',
        )

        assert state.account_dust['BTCUSD'] == Decimal('0.00001142')
