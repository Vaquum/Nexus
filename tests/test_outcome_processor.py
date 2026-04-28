from __future__ import annotations

import tempfile
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
        )

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
        expected_loss = Decimal('10')
        assert strategy_risk.rolling_loss_24h == expected_loss
        assert strategy_risk.rolling_loss_7d == expected_loss
        assert strategy_risk.rolling_loss_30d == expected_loss

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
        )

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
        expected_pnl = Decimal('10')
        assert strategy_risk.strategy_realized_pnl == expected_pnl

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
        )

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

        expected_pnl = Decimal('10')
        assert state.risk.cumulative_realized_pnl == expected_pnl

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
        )

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

        expected_equity = _POOL + Decimal('10')
        assert state.risk.equity == expected_equity
        assert state.risk.equity_hwm == expected_equity

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
        )

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
        )

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
        assert strat1_risk.rolling_loss_24h == Decimal('10')

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
        )

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
        )

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
        expected_pnl = Decimal('10')
        assert strategy_risk.strategy_realized_pnl == expected_pnl
        assert strategy_risk.rolling_loss_24h == _ZERO


class TestCapitalConservationOnExit:
    '''BLOCKER-A: `position_notional` and `per_strategy_deployed` must
    decrement on EXIT FILL by the cost basis of the closed portion.

    Pre-fix: `_handle_fill` called `capital.order_fill` only when
    `context.is_entry`. The EXIT path went straight to `_reduce_position`
    which mutated only `position.size` / `position.pending_exit`; no
    capital aggregate was touched. After ~1.5 round-trips a
    `capital_pct=10` strategy hit per-strategy-deployed denial; after
    ~7 round-trips total utilization hit the 80% cap and every new
    ENTER was denied with no operator-recoverable code path.

    Post-fix: `_grow_position` maintains `Position.avg_cost_basis` as
    VWAP-with-fees on entry FILLs. `_reduce_position` returns
    `cost_basis_released = avg_cost_basis * fill_size`. `_handle_fill`
    calls `capital.order_exit(strategy_id, cost_basis_released)` which
    decrements both aggregates by that amount. Round-trip conservation
    holds: every entry FILL adds `fill_notional + actual_fees` to
    `position_notional`; the matching exit FILLs collectively remove
    the same amount.
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

        # Round-trip conservation: aggregates back to zero
        assert ctrl._state.position_notional == _ZERO
        assert 'strat_001' not in ctrl._state.per_strategy_deployed
        # Position fully closed → removed from state
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
