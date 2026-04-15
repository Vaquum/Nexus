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
