from datetime import datetime, timezone
from decimal import Decimal

from nexus.core.capital_controller.capital_controller import CapitalController
from nexus.core.domain.capital_state import CapitalState
from nexus.core.domain.enums import OrderSide
from nexus.core.domain.instance_state import InstanceState
from nexus.core.domain.position import Position
from nexus.infrastructure.praxis_connector.order_context import OrderContext
from nexus.infrastructure.praxis_connector.outcome_processor import OutcomeProcessor
from nexus.infrastructure.praxis_connector.trade_outcome import TradeOutcome
from nexus.infrastructure.praxis_connector.trade_outcome_type import TradeOutcomeType

_POOL = Decimal('10000')
_ZERO = Decimal(0)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _make_processor() -> tuple[OutcomeProcessor, CapitalController, InstanceState]:
    capital_state = CapitalState(capital_pool=_POOL)
    instance_state = InstanceState(capital=capital_state)
    controller = CapitalController(capital_state)
    processor = OutcomeProcessor(controller, instance_state)
    return processor, controller, instance_state


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
        proc, ctrl, _ = _make_processor()
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
        proc, _, _ = _make_processor()

        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='nonexistent',
            outcome_type=TradeOutcomeType.ACK,
            timestamp=_now(),
        )

        result = proc.process(outcome, _entry_context())
        assert result.success is False
        assert result.error_reason is not None
        assert 'order_ack failed' in result.error_reason


class TestOutcomeProcessorFill:
    def test_fill_success(self) -> None:
        proc, ctrl, state = _make_processor()
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
        proc, ctrl, state = _make_processor()

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
        proc, _, _ = _make_processor()

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

        result = proc.process(outcome, _entry_context())
        assert result.success is False
        assert result.error_reason is not None
        assert 'order_fill failed' in result.error_reason


class TestOutcomeProcessorReject:
    def test_reject_entry_order(self) -> None:
        proc, ctrl, _ = _make_processor()
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
        proc, ctrl, state = _make_processor()
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
        proc, ctrl, _ = _make_processor()
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
        proc, ctrl, _ = _make_processor()
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
        proc, ctrl, state = _make_processor()
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
        proc, ctrl, state = _make_processor()
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
        proc, ctrl, state = _make_processor()
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

    def test_entry_fill_no_position_returns_false(self) -> None:
        proc, ctrl, _ = _make_processor()
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

        result = proc.process(outcome, _entry_context())
        assert result.success is True
        assert result.position_updated is False


class TestPositionReduction:
    def test_exit_fill_reduces_position(self) -> None:
        proc, ctrl, state = _make_processor()
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
        proc, ctrl, state = _make_processor()
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
