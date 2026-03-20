'''Verify CapitalController check-and-reserve, release, and lifecycle transitions.'''

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from nexus.core.capital_controller.capital_controller import (
    CapitalController,
    MAX_ALLOCATION_PER_TRADE_PCT,
    MAX_CAPITAL_UTILIZATION_PCT,
)
from nexus.core.capital_controller.reservation import Reservation, ReservationResult
from nexus.core.capital_controller.tracked_order import OrderLifecycleState
from nexus.core.domain.capital_state import CapitalState

_POOL = Decimal('10000')
_ZERO = Decimal(0)


def _make_controller(**overrides: Decimal) -> CapitalController:
    cs = CapitalState(capital_pool=_POOL, **overrides)
    return CapitalController(cs)


def _reserve(
    ctrl: CapitalController,
    notional: str = '100',
    fees: str = '1',
    budget: str = '5000',
    deployed: str = '0',
) -> ReservationResult:
    return ctrl.check_and_reserve(
        strategy_id='strat_a',
        order_notional=Decimal(notional),
        estimated_fees=Decimal(fees),
        strategy_budget=Decimal(budget),
        strategy_deployed=Decimal(deployed),
    )


class TestSuccessfulReservation:
    def test_granted_result(self) -> None:
        ctrl = _make_controller()
        result = _reserve(ctrl)
        assert result.granted is True
        assert result.reservation is not None
        assert result.reservation.strategy_id == 'strat_a'
        assert result.reservation.notional == Decimal('100')
        assert result.reservation.estimated_fees == Decimal('1')

    def test_reservation_notional_updated(self) -> None:
        ctrl = _make_controller()
        _reserve(ctrl, notional='200', fees='5')
        assert ctrl._state.reservation_notional == Decimal('205')

    def test_sequential_reservations_accumulate(self) -> None:
        ctrl = _make_controller()
        _reserve(ctrl, notional='100', fees='1')
        _reserve(ctrl, notional='200', fees='2')
        assert ctrl._state.reservation_notional == Decimal('303')


class TestPerTradeAllocationCheck:
    def test_exceeds_allocation_limit(self) -> None:
        ctrl = _make_controller()
        limit = _POOL * MAX_ALLOCATION_PER_TRADE_PCT
        result = _reserve(ctrl, notional=str(limit + 1))
        assert result.granted is False
        assert result.denial_reason is not None
        assert 'allocation' in result.denial_reason.lower()

    def test_at_allocation_limit_passes(self) -> None:
        ctrl = _make_controller()
        limit = _POOL * MAX_ALLOCATION_PER_TRADE_PCT
        result = _reserve(ctrl, notional=str(limit))
        assert result.granted is True


class TestStrategyBudgetCheck:
    def test_exceeds_strategy_budget(self) -> None:
        ctrl = _make_controller()
        result = _reserve(ctrl, notional='500', deployed='4600', budget='5000')
        assert result.granted is False
        assert result.denial_reason is not None
        assert 'budget' in result.denial_reason.lower()

    def test_at_strategy_budget_passes(self) -> None:
        ctrl = _make_controller()
        result = _reserve(
            ctrl, notional='999', fees='1', deployed='4000', budget='5000'
        )
        assert result.granted is True

    def test_exhausted_budget_denied(self) -> None:
        ctrl = _make_controller()
        result = _reserve(ctrl, notional='100', budget='0')
        assert result.granted is False
        assert result.denial_reason is not None
        assert 'budget' in result.denial_reason.lower()

    def test_negative_budget_denied(self) -> None:
        ctrl = _make_controller()
        result = _reserve(ctrl, notional='100', budget='-50')
        assert result.granted is False
        assert result.denial_reason is not None
        assert 'budget' in result.denial_reason.lower()


class TestAvailableCapitalCheck:
    def test_insufficient_available(self) -> None:
        ctrl = _make_controller(position_notional=Decimal('9950'))
        result = _reserve(ctrl, notional='100', fees='1')
        assert result.granted is False
        assert result.denial_reason is not None
        assert 'insufficient' in result.denial_reason.lower()

    def test_exactly_available_passes(self) -> None:
        ctrl = _make_controller(position_notional=Decimal('7000'))
        result = _reserve(ctrl, notional='999', fees='1', budget=str(_POOL))
        assert result.granted is True


class TestTotalUtilizationCheck:
    def test_exceeds_utilization_limit(self) -> None:
        deployed = _POOL * MAX_CAPITAL_UTILIZATION_PCT
        ctrl = _make_controller(position_notional=deployed)
        result = _reserve(ctrl, notional='1', fees='0', budget=str(_POOL))
        assert result.granted is False
        assert result.denial_reason is not None
        assert 'utilization' in result.denial_reason.lower()

    def test_at_utilization_limit_passes(self) -> None:
        deployed = _POOL * MAX_CAPITAL_UTILIZATION_PCT - Decimal('100')
        ctrl = _make_controller(position_notional=deployed)
        result = _reserve(ctrl, notional='100', fees='0', budget=str(_POOL))
        assert result.granted is True


class TestReleaseReservation:
    def test_release_returns_capital(self) -> None:
        ctrl = _make_controller()
        result = _reserve(ctrl, notional='500', fees='5')
        assert ctrl._state.reservation_notional == Decimal('505')
        assert result.reservation is not None

        released = ctrl.release_reservation(result.reservation.reservation_id)
        assert released is True
        assert ctrl._state.reservation_notional == _ZERO

    def test_release_unknown_id(self) -> None:
        ctrl = _make_controller()
        assert ctrl.release_reservation('nonexistent') is False

    def test_double_release(self) -> None:
        ctrl = _make_controller()
        result = _reserve(ctrl, notional='100', fees='1')
        assert result.reservation is not None
        rid = result.reservation.reservation_id

        assert ctrl.release_reservation(rid) is True
        assert ctrl.release_reservation(rid) is False


class TestConcurrency:
    def test_no_over_allocation_under_contention(self) -> None:
        ctrl = _make_controller()
        results: list[ReservationResult] = []
        barrier = threading.Barrier(10)

        def try_reserve() -> None:
            barrier.wait()
            r = ctrl.check_and_reserve(
                strategy_id='strat_a',
                order_notional=Decimal('1000'),
                estimated_fees=Decimal('10'),
                strategy_budget=_POOL,
                strategy_deployed=_ZERO,
            )
            results.append(r)

        threads = [threading.Thread(target=try_reserve) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        granted = [r for r in results if r.granted]
        total_reserved = sum(
            r.reservation.total for r in granted if r.reservation is not None
        )
        assert total_reserved <= _POOL
        assert ctrl._state.reservation_notional == total_reserved


class TestExpiredPurge:
    def test_expired_reservations_purged_on_reserve(self) -> None:
        past = datetime(2020, 1, 1, tzinfo=timezone.utc)

        ctrl = _make_controller()
        expired_res = Reservation(
            reservation_id='expired_001',
            strategy_id='strat_a',
            notional=Decimal('500'),
            estimated_fees=Decimal('5'),
            created_at=past,
            expires_at=past + timedelta(seconds=1),
        )
        ctrl._reservations['expired_001'] = expired_res
        ctrl._state.reservation_notional = Decimal('505')

        _reserve(ctrl, notional='100', fees='1', budget=str(_POOL))
        assert ctrl._state.reservation_notional == Decimal('101')

    def test_expired_reservation_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        past = datetime(2020, 1, 1, tzinfo=timezone.utc)

        ctrl = _make_controller()
        expired_res = Reservation(
            reservation_id='expired_002',
            strategy_id='strat_b',
            notional=Decimal('200'),
            estimated_fees=Decimal('2'),
            created_at=past,
            expires_at=past + timedelta(seconds=30),
        )
        ctrl._reservations['expired_002'] = expired_res
        ctrl._state.reservation_notional = Decimal('202')

        with caplog.at_level('WARNING'):
            _reserve(ctrl)

        assert 'Reservation expired' in caplog.text
        assert 'expired_002' in caplog.text
        assert 'strat_b' in caplog.text
        assert 'total=202' in caplog.text
        assert 'held=' in caplog.text

    def test_multiple_expired_reservations_log_each(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        past = datetime(2020, 1, 1, tzinfo=timezone.utc)

        ctrl = _make_controller()
        for i in range(3):
            res = Reservation(
                reservation_id=f'expired_{i}',
                strategy_id=f'strat_{i}',
                notional=Decimal('100'),
                estimated_fees=Decimal('1'),
                created_at=past,
                expires_at=past + timedelta(seconds=30),
            )
            ctrl._reservations[f'expired_{i}'] = res
        ctrl._state.reservation_notional = Decimal('303')

        with caplog.at_level('WARNING'):
            _reserve(ctrl)

        assert caplog.text.count('Reservation expired') == 3
        for i in range(3):
            assert f'expired_{i}' in caplog.text


class TestInputValidation:
    def test_nan_notional_rejected(self) -> None:
        ctrl = _make_controller()
        with pytest.raises(ValueError, match='order_notional'):
            _reserve(ctrl, notional='NaN')

    def test_negative_notional_rejected(self) -> None:
        ctrl = _make_controller()
        with pytest.raises(ValueError, match='non-negative'):
            _reserve(ctrl, notional='-1')

    def test_nan_strategy_budget_rejected(self) -> None:
        ctrl = _make_controller()
        with pytest.raises(ValueError, match='strategy_budget'):
            _reserve(ctrl, budget='NaN')

    def test_empty_strategy_id_rejected(self) -> None:
        ctrl = _make_controller()
        with pytest.raises(ValueError, match='strategy_id'):
            ctrl.check_and_reserve(
                strategy_id='',
                order_notional=Decimal('100'),
                estimated_fees=Decimal('1'),
                strategy_budget=Decimal('5000'),
                strategy_deployed=Decimal('0'),
            )

    def test_negative_deployed_rejected(self) -> None:
        ctrl = _make_controller()
        with pytest.raises(ValueError, match='strategy_deployed'):
            _reserve(ctrl, deployed='-1')

    def test_zero_ttl_rejected(self) -> None:
        ctrl = _make_controller()
        with pytest.raises(ValueError, match='ttl_seconds'):
            ctrl.check_and_reserve(
                strategy_id='strat_a',
                order_notional=Decimal('100'),
                estimated_fees=Decimal('1'),
                strategy_budget=Decimal('5000'),
                strategy_deployed=Decimal('0'),
                ttl_seconds=0,
            )


class TestSendOrder:
    def test_send_order_success(self) -> None:
        ctrl = _make_controller()
        result = _reserve(ctrl, notional='100', fees='1')
        assert result.reservation is not None

        sent = ctrl.send_order(result.reservation.reservation_id, 'ORD-001')
        assert sent is True
        assert ctrl._state.reservation_notional == _ZERO
        assert ctrl._state.in_flight_order_notional == Decimal('101')
        assert 'ORD-001' in ctrl._orders
        assert ctrl._orders['ORD-001'].state == OrderLifecycleState.IN_FLIGHT

    def test_send_order_reservation_not_found(self) -> None:
        ctrl = _make_controller()
        sent = ctrl.send_order('nonexistent', 'ORD-001')
        assert sent is False
        assert ctrl._state.in_flight_order_notional == _ZERO

    def test_send_order_expired_reservation(self) -> None:
        ctrl = _make_controller()
        past = datetime(2020, 1, 1, tzinfo=timezone.utc)
        expired = Reservation(
            reservation_id='expired_001',
            strategy_id='strat_a',
            notional=Decimal('100'),
            estimated_fees=Decimal('1'),
            created_at=past,
            expires_at=past + timedelta(seconds=1),
        )
        ctrl._reservations['expired_001'] = expired
        ctrl._state.reservation_notional = Decimal('101')

        sent = ctrl.send_order('expired_001', 'ORD-001')
        assert sent is False
        assert ctrl._state.reservation_notional == _ZERO
        assert ctrl._state.in_flight_order_notional == _ZERO

    def test_send_order_empty_order_id_rejected(self) -> None:
        ctrl = _make_controller()
        result = _reserve(ctrl)
        assert result.reservation is not None
        with pytest.raises(ValueError, match='order_id'):
            ctrl.send_order(result.reservation.reservation_id, '')

    def test_send_order_duplicate_order_id_rejected(self) -> None:
        ctrl = _make_controller()
        res1 = _reserve(ctrl, notional='100', fees='1')
        res2 = _reserve(ctrl, notional='100', fees='1')
        assert res1.reservation is not None
        assert res2.reservation is not None

        ctrl.send_order(res1.reservation.reservation_id, 'ORD-DUP')
        with pytest.raises(ValueError, match='already tracked'):
            ctrl.send_order(res2.reservation.reservation_id, 'ORD-DUP')


class TestOrderAck:
    def test_order_ack_success(self) -> None:
        ctrl = _make_controller()
        result = _reserve(ctrl, notional='100', fees='1')
        assert result.reservation is not None
        ctrl.send_order(result.reservation.reservation_id, 'ORD-001')

        acked = ctrl.order_ack('ORD-001')
        assert acked is True
        assert ctrl._state.in_flight_order_notional == _ZERO
        assert ctrl._state.working_order_notional == Decimal('101')
        assert ctrl._orders['ORD-001'].state == OrderLifecycleState.WORKING

    def test_order_ack_not_found(self) -> None:
        ctrl = _make_controller()
        acked = ctrl.order_ack('nonexistent')
        assert acked is False

    def test_order_ack_wrong_state(self) -> None:
        ctrl = _make_controller()
        result = _reserve(ctrl, notional='100', fees='1')
        assert result.reservation is not None
        ctrl.send_order(result.reservation.reservation_id, 'ORD-001')
        ctrl.order_ack('ORD-001')

        acked_again = ctrl.order_ack('ORD-001')
        assert acked_again is False


class TestOrderReject:
    def test_order_reject_success(self) -> None:
        ctrl = _make_controller()
        result = _reserve(ctrl, notional='100', fees='1')
        assert result.reservation is not None
        ctrl.send_order(result.reservation.reservation_id, 'ORD-001')
        assert ctrl._state.in_flight_order_notional == Decimal('101')

        rejected = ctrl.order_reject('ORD-001')
        assert rejected is True
        assert ctrl._state.in_flight_order_notional == _ZERO
        assert 'ORD-001' not in ctrl._orders

    def test_order_reject_not_found(self) -> None:
        ctrl = _make_controller()
        rejected = ctrl.order_reject('nonexistent')
        assert rejected is False

    def test_order_reject_wrong_state(self) -> None:
        ctrl = _make_controller()
        result = _reserve(ctrl, notional='100', fees='1')
        assert result.reservation is not None
        ctrl.send_order(result.reservation.reservation_id, 'ORD-001')
        ctrl.order_ack('ORD-001')

        rejected = ctrl.order_reject('ORD-001')
        assert rejected is False


class TestOrderFill:
    def test_order_fill_full(self) -> None:
        ctrl = _make_controller()
        result = _reserve(ctrl, notional='100', fees='1')
        assert result.reservation is not None
        ctrl.send_order(result.reservation.reservation_id, 'ORD-001')
        ctrl.order_ack('ORD-001')

        filled = ctrl.order_fill('ORD-001', Decimal('100'), Decimal('1'))
        assert filled is True
        assert ctrl._state.working_order_notional == _ZERO
        assert ctrl._state.position_notional == Decimal('101')
        assert 'ORD-001' not in ctrl._orders

    def test_order_fill_partial(self) -> None:
        ctrl = _make_controller()
        result = _reserve(ctrl, notional='1000', fees='10')
        assert result.reservation is not None
        ctrl.send_order(result.reservation.reservation_id, 'ORD-001')
        ctrl.order_ack('ORD-001')

        filled = ctrl.order_fill('ORD-001', Decimal('400'), Decimal('4'))
        assert filled is True
        assert ctrl._state.working_order_notional == Decimal('606')
        assert ctrl._state.position_notional == Decimal('404')
        assert 'ORD-001' in ctrl._orders
        assert ctrl._orders['ORD-001'].remaining_notional == Decimal('600')

    def test_order_fill_overfill_rejected(self) -> None:
        ctrl = _make_controller()
        result = _reserve(ctrl, notional='100', fees='1')
        assert result.reservation is not None
        ctrl.send_order(result.reservation.reservation_id, 'ORD-001')
        ctrl.order_ack('ORD-001')

        filled = ctrl.order_fill('ORD-001', Decimal('200'), Decimal('2'))
        assert filled is False
        assert ctrl._state.working_order_notional == Decimal('101')
        assert ctrl._state.position_notional == _ZERO

    def test_order_fill_not_found(self) -> None:
        ctrl = _make_controller()
        filled = ctrl.order_fill('nonexistent', Decimal('100'), Decimal('1'))
        assert filled is False

    def test_order_fill_wrong_state(self) -> None:
        ctrl = _make_controller()
        result = _reserve(ctrl, notional='100', fees='1')
        assert result.reservation is not None
        ctrl.send_order(result.reservation.reservation_id, 'ORD-001')

        filled = ctrl.order_fill('ORD-001', Decimal('100'), Decimal('1'))
        assert filled is False

    def test_order_fill_invalid_notional(self) -> None:
        ctrl = _make_controller()
        result = _reserve(ctrl, notional='100', fees='1')
        assert result.reservation is not None
        ctrl.send_order(result.reservation.reservation_id, 'ORD-001')
        ctrl.order_ack('ORD-001')

        with pytest.raises(ValueError, match='positive'):
            ctrl.order_fill('ORD-001', Decimal('0'), Decimal('0'))


class TestOrderCancel:
    def test_order_cancel_success(self) -> None:
        ctrl = _make_controller()
        result = _reserve(ctrl, notional='100', fees='1')
        assert result.reservation is not None
        ctrl.send_order(result.reservation.reservation_id, 'ORD-001')
        ctrl.order_ack('ORD-001')

        canceled = ctrl.order_cancel('ORD-001')
        assert canceled is True
        assert ctrl._state.working_order_notional == _ZERO
        assert 'ORD-001' not in ctrl._orders

    def test_order_cancel_after_partial_fill(self) -> None:
        ctrl = _make_controller()
        result = _reserve(ctrl, notional='1000', fees='10')
        assert result.reservation is not None
        ctrl.send_order(result.reservation.reservation_id, 'ORD-001')
        ctrl.order_ack('ORD-001')
        ctrl.order_fill('ORD-001', Decimal('400'), Decimal('4'))

        canceled = ctrl.order_cancel('ORD-001')
        assert canceled is True
        assert ctrl._state.working_order_notional == _ZERO
        assert ctrl._state.position_notional == Decimal('404')

    def test_order_cancel_not_found(self) -> None:
        ctrl = _make_controller()
        canceled = ctrl.order_cancel('nonexistent')
        assert canceled is False

    def test_order_cancel_wrong_state(self) -> None:
        ctrl = _make_controller()
        result = _reserve(ctrl, notional='100', fees='1')
        assert result.reservation is not None
        ctrl.send_order(result.reservation.reservation_id, 'ORD-001')

        canceled = ctrl.order_cancel('ORD-001')
        assert canceled is False


class TestLifecycleHappyPath:
    def test_reservation_to_position(self) -> None:
        ctrl = _make_controller()
        initial_available = ctrl._state.available

        result = _reserve(ctrl, notional='500', fees='5')
        assert result.reservation is not None
        assert ctrl._state.reservation_notional == Decimal('505')

        ctrl.send_order(result.reservation.reservation_id, 'ORD-001')
        assert ctrl._state.reservation_notional == _ZERO
        assert ctrl._state.in_flight_order_notional == Decimal('505')

        ctrl.order_ack('ORD-001')
        assert ctrl._state.in_flight_order_notional == _ZERO
        assert ctrl._state.working_order_notional == Decimal('505')

        ctrl.order_fill('ORD-001', Decimal('500'), Decimal('5'))
        assert ctrl._state.working_order_notional == _ZERO
        assert ctrl._state.position_notional == Decimal('505')
        assert ctrl._state.available == initial_available - Decimal('505')


class TestLifecycleRejectPath:
    def test_reservation_to_reject(self) -> None:
        ctrl = _make_controller()
        initial_available = ctrl._state.available

        result = _reserve(ctrl, notional='500', fees='5')
        assert result.reservation is not None
        ctrl.send_order(result.reservation.reservation_id, 'ORD-001')
        assert ctrl._state.available == initial_available - Decimal('505')

        ctrl.order_reject('ORD-001')
        assert ctrl._state.in_flight_order_notional == _ZERO
        assert ctrl._state.available == initial_available


class TestLifecycleCancelPath:
    def test_reservation_to_cancel(self) -> None:
        ctrl = _make_controller()
        initial_available = ctrl._state.available

        result = _reserve(ctrl, notional='500', fees='5')
        assert result.reservation is not None
        ctrl.send_order(result.reservation.reservation_id, 'ORD-001')
        ctrl.order_ack('ORD-001')
        assert ctrl._state.available == initial_available - Decimal('505')

        ctrl.order_cancel('ORD-001')
        assert ctrl._state.working_order_notional == _ZERO
        assert ctrl._state.available == initial_available


class TestNonTerminatingFeeRatio:
    def test_multi_fill_no_residual(self) -> None:
        ctrl = _make_controller()
        result = _reserve(ctrl, notional='100', fees='10')
        assert result.reservation is not None
        ctrl.send_order(result.reservation.reservation_id, 'ORD-001')
        ctrl.order_ack('ORD-001')

        ctrl.order_fill('ORD-001', Decimal('25'), Decimal('2.5'))
        ctrl.order_fill('ORD-001', Decimal('25'), Decimal('2.5'))
        ctrl.order_fill('ORD-001', Decimal('50'), Decimal('5'))

        assert ctrl._state.working_order_notional == _ZERO
        assert ctrl._state.position_notional == Decimal('110')

    def test_partial_fill_then_cancel_no_residual(self) -> None:
        ctrl = _make_controller()
        initial_available = ctrl._state.available
        result = _reserve(ctrl, notional='100', fees='10')
        assert result.reservation is not None
        ctrl.send_order(result.reservation.reservation_id, 'ORD-001')
        ctrl.order_ack('ORD-001')

        ctrl.order_fill('ORD-001', Decimal('25'), Decimal('2.5'))
        ctrl.order_cancel('ORD-001')

        assert ctrl._state.working_order_notional == _ZERO
        position_plus_available = ctrl._state.position_notional + ctrl._state.available
        assert position_plus_available == initial_available


class TestLifecycleConcurrency:
    def test_no_double_counting_under_contention(self) -> None:
        ctrl = _make_controller()
        successes: list[bool] = []
        errors: list[Exception] = []
        barrier = threading.Barrier(10)

        def lifecycle_race(idx: int) -> None:
            try:
                barrier.wait(timeout=5)
                res = ctrl.check_and_reserve(
                    strategy_id='strat_a',
                    order_notional=Decimal('500'),
                    estimated_fees=Decimal('5'),
                    strategy_budget=_POOL,
                    strategy_deployed=_ZERO,
                )
                if res.granted and res.reservation:
                    sent = ctrl.send_order(res.reservation.reservation_id, f'ORD-{idx}')
                    if sent:
                        acked = ctrl.order_ack(f'ORD-{idx}')
                        filled = ctrl.order_fill(f'ORD-{idx}', Decimal('500'), Decimal('5'))
                        if not acked or not filled:
                            msg = (
                                f'Lifecycle failure ORD-{idx}: '
                                f'acked={acked}, filled={filled}'
                            )
                            raise AssertionError(msg)
                        successes.append(True)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=lifecycle_race, args=(i,)) for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        for t in threads:
            assert not t.is_alive(), 'Thread did not finish within timeout'

        assert not errors, f'Thread errors: {errors}'
        assert len(successes) >= 1, 'At least one lifecycle must succeed'

        total_committed = (
            ctrl._state.reservation_notional
            + ctrl._state.in_flight_order_notional
            + ctrl._state.working_order_notional
            + ctrl._state.position_notional
        )
        assert ctrl._state.available + total_committed == _POOL
