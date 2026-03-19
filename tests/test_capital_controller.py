'''Verify CapitalController check-and-reserve and release under lock.'''

from __future__ import annotations

import threading
from datetime import timedelta
from decimal import Decimal

import pytest

from nexus.core.capital_controller.capital_controller import (
    CapitalController,
    MAX_ALLOCATION_PER_TRADE_PCT,
    MAX_CAPITAL_UTILIZATION_PCT,
)
from nexus.core.capital_controller.reservation import Reservation, ReservationResult
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
        ctrl = _make_controller()
        result = _reserve(ctrl, notional='500', fees='5', budget=str(_POOL))
        assert result.granted is True
        assert ctrl._state.reservation_notional == Decimal('505')

        import time

        ctrl2 = CapitalController(ctrl._state)
        ctrl2._reservations = dict(ctrl._reservations)

        for rid, r in ctrl2._reservations.items():
            expired = Reservation(
                reservation_id=r.reservation_id,
                strategy_id=r.strategy_id,
                notional=r.notional,
                estimated_fees=r.estimated_fees,
                created_at=r.created_at,
                expires_at=r.created_at + timedelta(seconds=1),
            )
            ctrl2._reservations[rid] = expired

        time.sleep(1.1)

        _reserve(ctrl2, notional='100', fees='1', budget=str(_POOL))
        assert ctrl2._state.reservation_notional == Decimal('101')


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
