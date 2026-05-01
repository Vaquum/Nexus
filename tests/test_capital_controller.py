'''Verify CapitalController check-and-reserve, release, and lifecycle transitions.'''

from __future__ import annotations

import heapq
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from nexus.core.capital_controller.capital_controller import (
    CapitalController,
    MAX_ALLOCATION_PER_TRADE_PCT,
    MAX_CAPITAL_UTILIZATION_PCT,
)
from nexus.core.capital_controller.lifecycle_result import FailureCategory
from nexus.core.capital_controller.reservation import Reservation, ReservationResult
from nexus.core.capital_controller.tracked_order import OrderLifecycleState
from nexus.core.domain.capital_state import CapitalState
from nexus.core.domain.enums import OrderSide
from nexus.core.domain.position import Position

_POOL = Decimal('10000')
_ZERO = Decimal(0)


def _make_controller(**overrides: Any) -> CapitalController:
    cs = CapitalState(capital_pool=_POOL, **overrides)
    return CapitalController(cs)


def _reserve(
    ctrl: CapitalController,
    strategy_id: str = 'strat_a',
    notional: str = '100',
    fees: str = '1',
    budget: str = '5000',
) -> ReservationResult:
    return ctrl.check_and_reserve(
        strategy_id=strategy_id,
        order_notional=Decimal(notional),
        estimated_fees=Decimal(fees),
        strategy_budget=Decimal(budget),
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

    def test_strategy_deployed_updates_on_success(self) -> None:
        ctrl = _make_controller()
        _reserve(ctrl, notional='100', fees='1')
        assert ctrl._state.per_strategy_deployed['strat_a'] == Decimal('101')


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


class TestMaxAllocationPerTradePctOverride:
    def test_default_matches_module_constant(self) -> None:
        cs = CapitalState(capital_pool=_POOL)
        ctrl = CapitalController(cs)
        limit = _POOL * MAX_ALLOCATION_PER_TRADE_PCT
        denied = ctrl.check_and_reserve(
            strategy_id='strat_a',
            order_notional=Decimal(str(limit + 1)),
            estimated_fees=Decimal('1'),
            strategy_budget=Decimal('100000'),
        )
        assert denied.granted is False

    def test_higher_override_admits_previously_denied(self) -> None:
        cs = CapitalState(capital_pool=_POOL)
        ctrl = CapitalController(
            cs,
            max_allocation_per_trade_pct=Decimal('0.50'),
        )
        prior_limit = _POOL * MAX_ALLOCATION_PER_TRADE_PCT
        result = ctrl.check_and_reserve(
            strategy_id='strat_a',
            order_notional=Decimal(str(prior_limit + 1)),
            estimated_fees=Decimal('1'),
            strategy_budget=Decimal('100000'),
        )
        assert result.granted is True

    def test_lower_override_denies_previously_admitted(self) -> None:
        cs = CapitalState(capital_pool=_POOL)
        ctrl = CapitalController(
            cs,
            max_allocation_per_trade_pct=Decimal('0.05'),
        )
        notional_at_default = _POOL * MAX_ALLOCATION_PER_TRADE_PCT
        result = ctrl.check_and_reserve(
            strategy_id='strat_a',
            order_notional=notional_at_default,
            estimated_fees=Decimal('1'),
            strategy_budget=Decimal('100000'),
        )
        assert result.granted is False

    def test_denial_reason_carries_override(self) -> None:
        cs = CapitalState(capital_pool=_POOL)
        ctrl = CapitalController(
            cs,
            max_allocation_per_trade_pct=Decimal('0.05'),
        )
        result = ctrl.check_and_reserve(
            strategy_id='strat_a',
            order_notional=Decimal('600'),
            estimated_fees=Decimal('1'),
            strategy_budget=Decimal('100000'),
        )
        assert result.granted is False
        assert result.denial_reason is not None
        assert '0.05' in result.denial_reason

    def test_non_decimal_override_raises(self) -> None:
        cs = CapitalState(capital_pool=_POOL)
        with pytest.raises(TypeError, match='must be Decimal'):
            CapitalController(cs, max_allocation_per_trade_pct=0.5)  # type: ignore[arg-type]

    def test_zero_override_raises(self) -> None:
        cs = CapitalState(capital_pool=_POOL)
        with pytest.raises(ValueError, match='must be > 0'):
            CapitalController(cs, max_allocation_per_trade_pct=Decimal('0'))

    def test_negative_override_raises(self) -> None:
        cs = CapitalState(capital_pool=_POOL)
        with pytest.raises(ValueError, match='must be > 0'):
            CapitalController(
                cs, max_allocation_per_trade_pct=Decimal('-0.1'),
            )

    def test_nan_override_raises(self) -> None:
        cs = CapitalState(capital_pool=_POOL)
        with pytest.raises(ValueError, match='finite Decimal'):
            CapitalController(
                cs, max_allocation_per_trade_pct=Decimal('NaN'),
            )

    def test_infinity_override_raises(self) -> None:
        cs = CapitalState(capital_pool=_POOL)
        with pytest.raises(ValueError, match='finite Decimal'):
            CapitalController(
                cs, max_allocation_per_trade_pct=Decimal('Infinity'),
            )

    def test_negative_infinity_override_raises(self) -> None:
        cs = CapitalState(capital_pool=_POOL)
        with pytest.raises(ValueError, match='finite Decimal'):
            CapitalController(
                cs, max_allocation_per_trade_pct=Decimal('-Infinity'),
            )


class TestStrategyBudgetCheck:
    def test_unknown_per_strategy_deployment_in_non_flat_state_denied(self) -> None:
        ctrl = _make_controller(position_notional=Decimal('1000'))
        result = _reserve(ctrl, notional='100', fees='1', budget='5000')
        assert result.granted is False
        assert result.denial_reason is not None
        assert 'unknown' in result.denial_reason.lower()

    def test_per_strategy_deployment_mismatch_in_non_flat_state_denied(self) -> None:
        ctrl = _make_controller(
            position_notional=Decimal('1000'),
            per_strategy_deployed={'strat_a': Decimal('900')},
        )
        result = _reserve(ctrl, notional='100', fees='1', budget='5000')
        assert result.granted is False
        assert result.denial_reason is not None
        assert 'mismatch' in result.denial_reason.lower()

    def test_per_strategy_deployment_mismatch_in_flat_state_denied(self) -> None:
        ctrl = _make_controller(
            per_strategy_deployed={'strat_a': Decimal('1')},
        )
        result = _reserve(ctrl, notional='100', fees='1', budget='5000')
        assert result.granted is False
        assert result.denial_reason is not None
        assert 'mismatch' in result.denial_reason.lower()
        assert 'flat state' in result.denial_reason.lower()

    def test_exceeds_strategy_budget(self) -> None:
        ctrl = _make_controller(
            position_notional=Decimal('4600'),
            per_strategy_deployed={'strat_a': Decimal('4600')},
        )
        result = _reserve(ctrl, notional='500', budget='5000')
        assert result.granted is False
        assert result.denial_reason is not None
        assert 'budget' in result.denial_reason.lower()

    def test_at_strategy_budget_passes(self) -> None:
        ctrl = _make_controller(
            position_notional=Decimal('4000'),
            per_strategy_deployed={'strat_a': Decimal('4000')},
        )
        result = _reserve(ctrl, notional='999', fees='1', budget='5000')
        assert result.granted is True

    def test_exhausted_budget_denied(self) -> None:
        ctrl = _make_controller()
        result = _reserve(ctrl, notional='100', budget='0')
        assert result.granted is False
        assert result.denial_reason is not None
        assert 'budget' in result.denial_reason.lower()

    def test_budget_check_uses_normalized_strategy_id(self) -> None:
        ctrl = _make_controller(
            position_notional=Decimal('4900'),
            per_strategy_deployed={'strat_a': Decimal('4900')},
        )
        result = _reserve(
            ctrl,
            strategy_id=' strat_a ',
            notional='200',
            fees='1',
            budget='5000',
        )

        assert result.granted is False
        assert result.denial_reason is not None
        assert 'budget' in result.denial_reason.lower()

    def test_successful_reservation_stores_normalized_strategy_id(self) -> None:
        ctrl = _make_controller()
        result = _reserve(
            ctrl,
            strategy_id=' strat_a ',
            notional='100',
            fees='1',
            budget='5000',
        )

        assert result.granted is True
        assert 'strat_a' in ctrl._state.per_strategy_deployed
        assert ' strat_a ' not in ctrl._state.per_strategy_deployed

    def test_negative_budget_denied(self) -> None:
        ctrl = _make_controller()
        result = _reserve(ctrl, notional='100', budget='-50')
        assert result.granted is False
        assert result.denial_reason is not None
        assert 'budget' in result.denial_reason.lower()


class TestComputeStrategyBudget:
    def test_base_budget_from_capital_pct(self) -> None:
        ctrl = _make_controller()
        budget = ctrl.compute_strategy_budget('strat_a', Decimal('25'))
        assert budget == Decimal('2500')

    def test_auto_compound_adds_realized_pnl(self) -> None:
        ctrl = _make_controller()
        budget = ctrl.compute_strategy_budget(
            'strat_a',
            Decimal('25'),
            auto_compound=True,
            strategy_realized_pnl=Decimal('150'),
        )
        assert budget == Decimal('2650')

    def test_auto_compound_applies_negative_realized_pnl(self) -> None:
        ctrl = _make_controller()
        budget = ctrl.compute_strategy_budget(
            'strat_a',
            Decimal('25'),
            auto_compound=True,
            strategy_realized_pnl=Decimal('-300'),
        )
        assert budget == Decimal('2200')

    def test_invalid_capital_pct_rejected(self) -> None:
        ctrl = _make_controller()
        with pytest.raises(ValueError, match='capital_pct'):
            ctrl.compute_strategy_budget('strat_a', Decimal('0'))

    def test_invalid_strategy_realized_pnl_rejected(self) -> None:
        ctrl = _make_controller()
        with pytest.raises(ValueError, match='strategy_realized_pnl'):
            ctrl.compute_strategy_budget(
                'strat_a',
                Decimal('25'),
                auto_compound=True,
                strategy_realized_pnl=Decimal('NaN'),
            )

    def test_non_compound_ignores_strategy_realized_pnl_validation(self) -> None:
        ctrl = _make_controller()
        budget = ctrl.compute_strategy_budget(
            'strat_a',
            Decimal('25'),
            auto_compound=False,
            strategy_realized_pnl=Decimal('NaN'),
        )
        assert budget == Decimal('2500')


class TestAvailableCapitalCheck:
    def test_insufficient_available(self) -> None:
        ctrl = _make_controller(
            position_notional=Decimal('9950'),
            per_strategy_deployed={'strat_a': Decimal('9950')},
        )
        result = _reserve(ctrl, notional='100', fees='1', budget='20000')
        assert result.granted is False
        assert result.denial_reason is not None
        assert 'insufficient' in result.denial_reason.lower()

    def test_exactly_available_passes(self) -> None:
        ctrl = _make_controller(
            position_notional=Decimal('7000'),
            per_strategy_deployed={'strat_a': Decimal('7000')},
        )
        result = _reserve(ctrl, notional='999', fees='1', budget=str(_POOL))
        assert result.granted is True


class TestTotalUtilizationCheck:
    def test_exceeds_utilization_limit(self) -> None:
        deployed = _POOL * MAX_CAPITAL_UTILIZATION_PCT
        ctrl = _make_controller(
            position_notional=deployed,
            per_strategy_deployed={'strat_a': deployed},
        )
        result = _reserve(ctrl, notional='1', fees='0', budget=str(_POOL))
        assert result.granted is False
        assert result.denial_reason is not None
        assert 'utilization' in result.denial_reason.lower()

    def test_at_utilization_limit_passes(self) -> None:
        deployed = _POOL * MAX_CAPITAL_UTILIZATION_PCT - Decimal('100')
        ctrl = _make_controller(
            position_notional=deployed,
            per_strategy_deployed={'strat_a': deployed},
        )
        result = _reserve(ctrl, notional='100', fees='0', budget=str(_POOL))
        assert result.granted is True


class TestReleaseReservation:
    def test_release_returns_capital(self) -> None:
        ctrl = _make_controller()
        result = _reserve(ctrl, notional='500', fees='5')
        assert ctrl._state.reservation_notional == Decimal('505')
        assert ctrl._state.per_strategy_deployed['strat_a'] == Decimal('505')
        assert result.reservation is not None

        released = ctrl.release_reservation(result.reservation.reservation_id)
        assert released.success is True
        assert ctrl._state.reservation_notional == _ZERO
        assert 'strat_a' not in ctrl._state.per_strategy_deployed

    def test_release_unknown_id(self) -> None:
        ctrl = _make_controller()
        assert ctrl.release_reservation('nonexistent').success is False

    def test_double_release(self) -> None:
        ctrl = _make_controller()
        result = _reserve(ctrl, notional='100', fees='1')
        assert result.reservation is not None
        rid = result.reservation.reservation_id

        assert ctrl.release_reservation(rid).success is True
        assert ctrl.release_reservation(rid).success is False


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
        heapq.heappush(ctrl._expiry_heap, (expired_res.expires_at, 'expired_001'))
        ctrl._state.reservation_notional = Decimal('505')
        ctrl._state.per_strategy_deployed['strat_a'] = Decimal('505')

        _reserve(ctrl, notional='100', fees='1', budget=str(_POOL))
        assert ctrl._state.reservation_notional == Decimal('101')
        assert ctrl._state.per_strategy_deployed['strat_a'] == Decimal('101')

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
        heapq.heappush(ctrl._expiry_heap, (expired_res.expires_at, 'expired_002'))
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
            heapq.heappush(ctrl._expiry_heap, (res.expires_at, f'expired_{i}'))
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
            )

    def test_zero_ttl_rejected(self) -> None:
        ctrl = _make_controller()
        with pytest.raises(ValueError, match='ttl_seconds'):
            ctrl.check_and_reserve(
                strategy_id='strat_a',
                order_notional=Decimal('100'),
                estimated_fees=Decimal('1'),
                strategy_budget=Decimal('5000'),
                ttl_seconds=0,
            )


class TestSendOrder:
    def test_send_order_success(self) -> None:
        ctrl = _make_controller()
        result = _reserve(ctrl, notional='100', fees='1')
        assert result.reservation is not None

        sent = ctrl.send_order(result.reservation.reservation_id, 'ORD-001')
        assert sent.success is True
        assert ctrl._state.reservation_notional == _ZERO
        assert ctrl._state.in_flight_order_notional == Decimal('101')
        assert 'ORD-001' in ctrl._orders
        assert ctrl._orders['ORD-001'].state == OrderLifecycleState.IN_FLIGHT

    def test_send_order_reservation_not_found(self) -> None:
        ctrl = _make_controller()
        sent = ctrl.send_order('nonexistent', 'ORD-001')
        assert sent.success is False
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
        heapq.heappush(ctrl._expiry_heap, (expired.expires_at, 'expired_001'))
        ctrl._state.reservation_notional = Decimal('101')

        sent = ctrl.send_order('expired_001', 'ORD-001')
        assert sent.success is False
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


class TestSendOrderBoundaryRace:
    '''MAJOR-K + TD-S: the reservation TTL and `send_command` timeout
    were both 30s. A `send_command` blocking the full timeout could
    return only at `t == expires_at`, then the launcher's subsequent
    `send_order` would call `_purge_expired(now)` BEFORE
    `_reservations.pop(...)` — at boundary equality the heap pop fired
    first, the reservation was gone, EXPECTED_MISS returned, OrderContext
    never registered, capital permanently stuck in `in_flight_order_notional`
    until next boot.

    Post-fix: `_reservations.pop` runs first (so boundary equality is
    captured), an explicit `now > expires_at` check rejects truly-expired
    reservations and releases capital, then `_purge_expired` runs as
    housekeeping for OTHER reservations. `DEFAULT_TTL_SECONDS` also bumped
    to 60s so TTL > timeout invariant holds.
    '''

    def test_default_ttl_seconds_exceeds_send_command_timeout(self) -> None:
        '''Structural: TTL > send_command timeout (30s) so a slow
        send_command cannot consume the entire reservation window.
        '''

        from nexus.core.capital_controller.capital_controller import DEFAULT_TTL_SECONDS
        from nexus.infrastructure.praxis_connector.praxis_outbound import _DEFAULT_TIMEOUT

        assert DEFAULT_TTL_SECONDS > _DEFAULT_TIMEOUT

    def test_send_order_at_boundary_equality_succeeds(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        '''A reservation with `expires_at == now` at `send_order` call
        time is captured via the `_reservations.pop` reorder. Pre-fix
        `_purge_expired` (which uses `<= now`) ran first and removed
        the reservation; the subsequent pop returned None → EXPECTED_MISS.
        Post-fix the pop returns the reservation; `now > expires_at` is
        False; the reservation is consumed.

        `datetime.now` is patched in the `capital_controller` module so
        `send_order`'s internal `now` equals `expires_at` exactly —
        without the patch, `expires_at = anchor + 60s` would leave `now`
        well before expiry and the test would pass even if the
        pop-before-purge reorder were reverted.
        '''

        ctrl = _make_controller()
        anchor = datetime.now(tz=timezone.utc)
        boundary = Reservation(
            reservation_id='boundary_001',
            strategy_id='strat_a',
            notional=Decimal('100'),
            estimated_fees=Decimal('1'),
            created_at=anchor - timedelta(seconds=60),
            expires_at=anchor,
        )
        ctrl._reservations['boundary_001'] = boundary
        heapq.heappush(ctrl._expiry_heap, (boundary.expires_at, 'boundary_001'))
        ctrl._state.reservation_notional = Decimal('101')
        ctrl._state.per_strategy_deployed['strat_a'] = Decimal('101')

        class _FrozenDatetime(datetime):

            @classmethod
            def now(cls, tz: Any = None) -> datetime:  # noqa: ARG003
                return anchor

        monkeypatch.setattr(
            'nexus.core.capital_controller.capital_controller.datetime',
            _FrozenDatetime,
        )

        sent = ctrl.send_order('boundary_001', 'ORD-001')

        assert sent.success is True
        assert ctrl._state.reservation_notional == _ZERO
        assert ctrl._state.in_flight_order_notional == Decimal('101')
        assert 'ORD-001' in ctrl._orders

    def test_send_order_just_past_expiry_releases_capital(self) -> None:
        '''A reservation popped after its expires_at must release the
        capital aggregates (reservation_notional, per_strategy_deployed)
        so the strategy budget returns to available. Pre-fix
        `_purge_expired` did this on its own (because pop returned None);
        post-fix the pop succeeds and the explicit `now > expires_at`
        check does the same release before returning EXPECTED_MISS.
        '''

        ctrl = _make_controller()
        past = datetime.now(tz=timezone.utc) - timedelta(seconds=10)
        expired = Reservation(
            reservation_id='past_001',
            strategy_id='strat_a',
            notional=Decimal('100'),
            estimated_fees=Decimal('1'),
            created_at=past - timedelta(seconds=60),
            expires_at=past,
        )
        ctrl._reservations['past_001'] = expired
        heapq.heappush(ctrl._expiry_heap, (expired.expires_at, 'past_001'))
        ctrl._state.reservation_notional = Decimal('101')
        ctrl._state.per_strategy_deployed['strat_a'] = Decimal('101')

        sent = ctrl.send_order('past_001', 'ORD-001')

        assert sent.success is False
        assert sent.category == FailureCategory.EXPECTED_MISS
        assert ctrl._state.reservation_notional == _ZERO
        assert ctrl._state.in_flight_order_notional == _ZERO
        assert 'strat_a' not in ctrl._state.per_strategy_deployed
        assert 'ORD-001' not in ctrl._orders


class TestOrderAck:
    def test_order_ack_success(self) -> None:
        ctrl = _make_controller()
        result = _reserve(ctrl, notional='100', fees='1')
        assert result.reservation is not None
        ctrl.send_order(result.reservation.reservation_id, 'ORD-001')

        acked = ctrl.order_ack('ORD-001')
        assert acked.success is True
        assert ctrl._state.in_flight_order_notional == _ZERO
        assert ctrl._state.working_order_notional == Decimal('101')
        assert ctrl._orders['ORD-001'].state == OrderLifecycleState.WORKING

    def test_order_ack_not_found(self) -> None:
        ctrl = _make_controller()
        acked = ctrl.order_ack('nonexistent')
        assert acked.success is False

    def test_order_ack_wrong_state(self) -> None:
        ctrl = _make_controller()
        result = _reserve(ctrl, notional='100', fees='1')
        assert result.reservation is not None
        ctrl.send_order(result.reservation.reservation_id, 'ORD-001')
        ctrl.order_ack('ORD-001')

        acked_again = ctrl.order_ack('ORD-001')
        assert acked_again.success is False


class TestOrderReject:
    def test_order_reject_success(self) -> None:
        ctrl = _make_controller()
        result = _reserve(ctrl, notional='100', fees='1')
        assert result.reservation is not None
        assert ctrl._state.per_strategy_deployed['strat_a'] == Decimal('101')
        ctrl.send_order(result.reservation.reservation_id, 'ORD-001')
        assert ctrl._state.in_flight_order_notional == Decimal('101')

        rejected = ctrl.order_reject('ORD-001')
        assert rejected.success is True
        assert ctrl._state.in_flight_order_notional == _ZERO
        assert 'strat_a' not in ctrl._state.per_strategy_deployed
        assert 'ORD-001' not in ctrl._orders

    def test_order_reject_not_found(self) -> None:
        ctrl = _make_controller()
        rejected = ctrl.order_reject('nonexistent')
        assert rejected.success is False

    def test_order_reject_after_ack_succeeds_and_releases_working(self) -> None:
        '''PT-FIX-40: a REJECTED outcome can race past an ACK, leaving
        the tracked order in WORKING state when the reject arrives.
        Pre-fix `order_reject` rejected anything not in IN_FLIGHT,
        leaving `working_order_notional` parked permanently (TTL
        eviction only covers `_reservations`, not `_orders`).
        Post-fix WORKING is also accepted; the order is removed and
        the working notional is released.'''

        ctrl = _make_controller()
        result = _reserve(ctrl, notional='100', fees='1')
        assert result.reservation is not None
        ctrl.send_order(result.reservation.reservation_id, 'ORD-001')
        ctrl.order_ack('ORD-001')
        assert ctrl._state.working_order_notional == Decimal('101')
        assert ctrl._state.in_flight_order_notional == _ZERO

        rejected = ctrl.order_reject('ORD-001')

        assert rejected.success is True
        assert ctrl._state.working_order_notional == _ZERO
        assert ctrl._state.in_flight_order_notional == _ZERO
        assert 'ORD-001' not in ctrl._orders
        assert 'strat_a' not in ctrl._state.per_strategy_deployed


class TestOrderFill:
    def test_order_fill_full(self) -> None:
        ctrl = _make_controller()
        result = _reserve(ctrl, notional='100', fees='1')
        assert result.reservation is not None
        ctrl.send_order(result.reservation.reservation_id, 'ORD-001')
        ctrl.order_ack('ORD-001')

        filled = ctrl.order_fill('ORD-001', Decimal('100'), Decimal('1'))
        assert filled.success is True
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
        assert filled.success is True
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
        assert filled.success is False
        assert ctrl._state.working_order_notional == Decimal('101')
        assert ctrl._state.position_notional == _ZERO

    def test_order_fill_not_found(self) -> None:
        ctrl = _make_controller()
        filled = ctrl.order_fill('nonexistent', Decimal('100'), Decimal('1'))
        assert filled.success is False

    def test_order_fill_wrong_state(self) -> None:
        ctrl = _make_controller()
        result = _reserve(ctrl, notional='100', fees='1')
        assert result.reservation is not None
        ctrl.send_order(result.reservation.reservation_id, 'ORD-001')

        filled = ctrl.order_fill('ORD-001', Decimal('100'), Decimal('1'))
        assert filled.success is False

    def test_order_fill_invalid_notional(self) -> None:
        ctrl = _make_controller()
        result = _reserve(ctrl, notional='100', fees='1')
        assert result.reservation is not None
        ctrl.send_order(result.reservation.reservation_id, 'ORD-001')
        ctrl.order_ack('ORD-001')

        with pytest.raises(ValueError, match='positive'):
            ctrl.order_fill('ORD-001', Decimal('0'), Decimal('0'))

    def test_order_fill_invalid_actual_fees(self) -> None:
        ctrl = _make_controller()
        result = _reserve(ctrl, notional='100', fees='1')
        assert result.reservation is not None
        ctrl.send_order(result.reservation.reservation_id, 'ORD-001')
        ctrl.order_ack('ORD-001')

        with pytest.raises(ValueError, match='non-negative'):
            ctrl.order_fill('ORD-001', Decimal('100'), Decimal('-1'))

    def test_order_fill_fee_reserve_insufficiency_rejected(self) -> None:
        ctrl = _make_controller()
        result = _reserve(ctrl, notional='100', fees='1')
        assert result.reservation is not None
        ctrl.send_order(result.reservation.reservation_id, 'ORD-001')
        ctrl.order_ack('ORD-001')

        pre_working = ctrl._state.working_order_notional
        pre_position = ctrl._state.position_notional
        pre_reserve = ctrl._state.fee_reserve

        filled = ctrl.order_fill('ORD-001', Decimal('100'), Decimal('10'))
        assert filled.success is False
        assert ctrl._state.working_order_notional == pre_working
        assert ctrl._state.position_notional == pre_position
        assert ctrl._state.fee_reserve == pre_reserve

    def test_order_fill_adjusts_per_strategy_deployed_on_fee_surplus(self) -> None:
        ctrl = _make_controller()
        result = _reserve(ctrl, notional='100', fees='10')
        assert result.reservation is not None
        ctrl.send_order(result.reservation.reservation_id, 'ORD-001')
        ctrl.order_ack('ORD-001')

        pre_deployed = ctrl._state.per_strategy_deployed.get('strat_a', Decimal(0))

        ctrl.order_fill('ORD-001', Decimal('100'), Decimal('5'))

        post_deployed = ctrl._state.per_strategy_deployed.get('strat_a', Decimal(0))
        assert post_deployed == pre_deployed - Decimal('5')

    def test_order_fill_adjusts_per_strategy_deployed_on_fee_deficit(self) -> None:
        ctrl = _make_controller()
        result = _reserve(ctrl, notional='100', fees='5')
        assert result.reservation is not None
        ctrl.send_order(result.reservation.reservation_id, 'ORD-001')
        ctrl.order_ack('ORD-001')

        ctrl._state.fee_reserve = Decimal('10')
        pre_deployed = ctrl._state.per_strategy_deployed.get('strat_a', Decimal(0))

        ctrl.order_fill('ORD-001', Decimal('100'), Decimal('8'))

        post_deployed = ctrl._state.per_strategy_deployed.get('strat_a', Decimal(0))
        assert post_deployed == pre_deployed + Decimal('3')


class TestOrderCancel:
    def test_order_cancel_success(self) -> None:
        ctrl = _make_controller()
        result = _reserve(ctrl, notional='100', fees='1')
        assert result.reservation is not None
        assert ctrl._state.per_strategy_deployed['strat_a'] == Decimal('101')
        ctrl.send_order(result.reservation.reservation_id, 'ORD-001')
        ctrl.order_ack('ORD-001')

        canceled = ctrl.order_cancel('ORD-001')
        assert canceled.success is True
        assert ctrl._state.working_order_notional == _ZERO
        assert 'strat_a' not in ctrl._state.per_strategy_deployed
        assert 'ORD-001' not in ctrl._orders

    def test_order_cancel_after_partial_fill(self) -> None:
        ctrl = _make_controller()
        result = _reserve(ctrl, notional='1000', fees='10')
        assert result.reservation is not None
        ctrl.send_order(result.reservation.reservation_id, 'ORD-001')
        ctrl.order_ack('ORD-001')
        ctrl.order_fill('ORD-001', Decimal('400'), Decimal('4'))

        canceled = ctrl.order_cancel('ORD-001')
        assert canceled.success is True
        assert ctrl._state.working_order_notional == _ZERO
        assert ctrl._state.position_notional == Decimal('404')

    def test_order_cancel_not_found(self) -> None:
        ctrl = _make_controller()
        canceled = ctrl.order_cancel('nonexistent')
        assert canceled.success is False

    def test_order_cancel_in_flight_succeeds_and_releases_in_flight(self) -> None:
        '''PT-FIX-43: an EXPIRED or CANCELED outcome can arrive from the
        venue for an order that never received an ACK (still IN_FLIGHT).
        Pre-fix `order_cancel` rejected anything not WORKING, leaving
        `in_flight_order_notional` parked permanently. Mirrors the
        PT-FIX-40 fix for `order_reject`. Post-fix IN_FLIGHT is also
        accepted; the order is removed and the in-flight notional is
        released.'''

        ctrl = _make_controller()
        result = _reserve(ctrl, notional='100', fees='1')
        assert result.reservation is not None
        ctrl.send_order(result.reservation.reservation_id, 'ORD-001')
        assert ctrl._state.in_flight_order_notional == Decimal('101')
        assert ctrl._state.working_order_notional == _ZERO

        canceled = ctrl.order_cancel('ORD-001')

        assert canceled.success is True
        assert ctrl._state.in_flight_order_notional == _ZERO
        assert ctrl._state.working_order_notional == _ZERO
        assert 'ORD-001' not in ctrl._orders
        assert 'strat_a' not in ctrl._state.per_strategy_deployed


class TestRecoverOrphanedOrder:
    '''Defense-in-depth helper for FINAL-MAJOR-01: when the launcher's
    `process_outcome` hits the no-OrderContext terminal cleanup branch
    (because `_build_order_context` returned None, or — pre-FINAL-MAJOR-01
    — a registry race dropped the registration), `_orders[command_id]`
    was already populated by `send_order` and capital aggregates remain
    inflated. `recover_orphaned_order` releases the aggregate and pops
    the tracked order. Idempotent.
    '''

    def test_recover_in_flight_orphan_releases_aggregates(self) -> None:
        ctrl = _make_controller()
        result = _reserve(ctrl, notional='100', fees='1')
        assert result.reservation is not None
        ctrl.send_order(result.reservation.reservation_id, 'ORD-001')
        assert ctrl._state.in_flight_order_notional == Decimal('101')
        assert ctrl._state.per_strategy_deployed['strat_a'] == Decimal('101')

        recovered = ctrl.recover_orphaned_order('ORD-001', 'REJECTED')

        assert recovered.success is True
        assert ctrl._state.in_flight_order_notional == _ZERO
        assert 'strat_a' not in ctrl._state.per_strategy_deployed
        assert 'ORD-001' not in ctrl._orders

    def test_recover_working_orphan_releases_aggregates(self) -> None:
        ctrl = _make_controller()
        result = _reserve(ctrl, notional='100', fees='1')
        assert result.reservation is not None
        ctrl.send_order(result.reservation.reservation_id, 'ORD-001')
        ctrl.order_ack('ORD-001')
        assert ctrl._state.working_order_notional == Decimal('101')
        assert ctrl._state.in_flight_order_notional == _ZERO

        recovered = ctrl.recover_orphaned_order('ORD-001', 'CANCELED')

        assert recovered.success is True
        assert ctrl._state.working_order_notional == _ZERO
        assert 'strat_a' not in ctrl._state.per_strategy_deployed
        assert 'ORD-001' not in ctrl._orders

    def test_recover_unknown_order_is_idempotent(self) -> None:
        '''The launcher may not know whether `send_order` reached this
        controller (e.g., `send_order` returned failure earlier and the
        registry was cleaned up); calling `recover_orphaned_order` on a
        non-existent order returns success without mutating state.
        '''

        ctrl = _make_controller()
        pre_in_flight = ctrl._state.in_flight_order_notional
        pre_working = ctrl._state.working_order_notional
        pre_deployed = dict(ctrl._state.per_strategy_deployed)

        recovered = ctrl.recover_orphaned_order('nonexistent', 'EXPIRED')

        assert recovered.success is True
        assert ctrl._state.in_flight_order_notional == pre_in_flight
        assert ctrl._state.working_order_notional == pre_working
        assert ctrl._state.per_strategy_deployed == pre_deployed

    def test_recover_after_partial_fill_releases_remaining_working(self) -> None:
        '''Partial fill moved some capital to position_notional; the
        remaining working_order_notional is the aggregate at risk for
        the orphan; recovery releases only that remainder.
        '''

        ctrl = _make_controller()
        result = _reserve(ctrl, notional='1000', fees='10')
        assert result.reservation is not None
        ctrl.send_order(result.reservation.reservation_id, 'ORD-001')
        ctrl.order_ack('ORD-001')
        ctrl.order_fill('ORD-001', Decimal('400'), Decimal('4'))
        assert ctrl._state.position_notional == Decimal('404')
        assert ctrl._state.working_order_notional == Decimal('606')

        recovered = ctrl.recover_orphaned_order('ORD-001', 'CANCELED')

        assert recovered.success is True
        assert ctrl._state.position_notional == Decimal('404')
        assert ctrl._state.working_order_notional == _ZERO
        assert 'ORD-001' not in ctrl._orders


class TestOrderExitInvariants:
    '''`order_exit` must reject when `cost_basis_released` exceeds either
    `position_notional` OR `per_strategy_deployed[strategy_id]`. Pre-fix
    only the global aggregate was checked; a stale / missing per-strategy
    bucket entry could be driven negative or dropped, leaving
    `position_notional` decremented while attribution was corrupted.
    The next `check_and_reserve` would then fire the
    `'Per-strategy deployed attribution mismatch for non-flat state'`
    denial with no recovery path until a reboot.
    '''

    def test_order_exit_blocks_when_per_strategy_bucket_missing(self) -> None:
        ctrl = _make_controller(
            position_notional=Decimal('500'),
            per_strategy_deployed={},
        )

        result = ctrl.order_exit('strat_orphan', Decimal('100'))

        assert result.success is False
        assert result.category == FailureCategory.INVARIANT_BREACH
        assert result.reason is not None
        assert 'per_strategy_deployed[strat_orphan]' in result.reason
        assert ctrl._state.position_notional == Decimal('500')

    def test_order_exit_blocks_when_per_strategy_bucket_too_small(self) -> None:
        ctrl = _make_controller(
            position_notional=Decimal('500'),
            per_strategy_deployed={'strat_a': Decimal('40')},
        )

        result = ctrl.order_exit('strat_a', Decimal('100'))

        assert result.success is False
        assert result.category == FailureCategory.INVARIANT_BREACH
        assert ctrl._state.position_notional == Decimal('500')
        assert ctrl._state.per_strategy_deployed['strat_a'] == Decimal('40')

    def test_order_exit_succeeds_when_both_aggregates_have_room(self) -> None:
        ctrl = _make_controller(
            position_notional=Decimal('500'),
            per_strategy_deployed={'strat_a': Decimal('500')},
        )

        result = ctrl.order_exit('strat_a', Decimal('100'))

        assert result.success is True
        assert ctrl._state.position_notional == Decimal('400')
        assert ctrl._state.per_strategy_deployed['strat_a'] == Decimal('400')

    def test_order_exit_rejects_empty_strategy_id(self) -> None:
        ctrl = _make_controller()

        for invalid in ('', '   ', '\t\n'):
            with pytest.raises(ValueError, match='strategy_id must be a non-empty string'):
                ctrl.order_exit(invalid, Decimal('1'))

    def test_order_exit_strips_whitespace_from_strategy_id(self) -> None:
        ctrl = _make_controller(
            position_notional=Decimal('500'),
            per_strategy_deployed={'strat_a': Decimal('500')},
        )

        result = ctrl.order_exit('  strat_a  ', Decimal('100'))

        assert result.success is True
        assert ctrl._state.per_strategy_deployed['strat_a'] == Decimal('400')


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
        assert ctrl._state.per_strategy_deployed['strat_a'] == Decimal('505')
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
        result = _reserve(ctrl, notional='3', fees='1')
        assert result.reservation is not None
        ctrl.send_order(result.reservation.reservation_id, 'ORD-001')
        ctrl.order_ack('ORD-001')

        ctrl.order_fill(
            'ORD-001', Decimal('1'), Decimal('0.333333333333333333333333333')
        )
        ctrl.order_fill(
            'ORD-001', Decimal('1'), Decimal('0.333333333333333333333333333')
        )
        ctrl.order_fill(
            'ORD-001', Decimal('1'), Decimal('0.333333333333333333333333334')
        )

        assert ctrl._state.working_order_notional == _ZERO
        assert ctrl._state.position_notional == Decimal('4')

    def test_partial_fill_then_cancel_no_residual(self) -> None:
        ctrl = _make_controller()
        initial_available = ctrl._state.available
        result = _reserve(ctrl, notional='3', fees='1')
        assert result.reservation is not None
        ctrl.send_order(result.reservation.reservation_id, 'ORD-001')
        ctrl.order_ack('ORD-001')

        ctrl.order_fill(
            'ORD-001', Decimal('1'), Decimal('0.333333333333333333333333333')
        )
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
                )
                if res.granted and res.reservation:
                    sent = ctrl.send_order(res.reservation.reservation_id, f'ORD-{idx}')
                    if sent.success:
                        acked = ctrl.order_ack(f'ORD-{idx}')
                        filled = ctrl.order_fill(
                            f'ORD-{idx}', Decimal('500'), Decimal('5')
                        )
                        if not acked.success or not filled.success:
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


class TestPerStrategyIsolation:
    def test_budget_check_isolated_per_strategy(self) -> None:
        ctrl = _make_controller(
            position_notional=Decimal('4900'),
            per_strategy_deployed={
                'strat_a': Decimal('4900'),
                'strat_b': Decimal('0'),
            },
        )

        denied = _reserve(
            ctrl,
            strategy_id='strat_a',
            notional='200',
            fees='1',
            budget='5000',
        )
        allowed = _reserve(
            ctrl,
            strategy_id='strat_b',
            notional='200',
            fees='1',
            budget='5000',
        )

        assert denied.granted is False
        assert allowed.granted is True

    def test_deployed_map_updates_by_strategy_id(self) -> None:
        ctrl = _make_controller()

        _reserve(ctrl, strategy_id='strat_a', notional='100', fees='1')
        _reserve(ctrl, strategy_id='strat_b', notional='200', fees='2')

        assert ctrl._state.per_strategy_deployed['strat_a'] == Decimal('101')
        assert ctrl._state.per_strategy_deployed['strat_b'] == Decimal('202')


class TestPerStrategyDeployedInvariants:
    def test_sum_per_strategy_deployed_equals_committed_capital(self) -> None:
        ctrl = _make_controller()

        res_a = _reserve(ctrl, strategy_id='strat_a', notional='300', fees='3')
        res_b = _reserve(ctrl, strategy_id='strat_b', notional='200', fees='2')
        assert res_a.reservation is not None
        assert res_b.reservation is not None

        ctrl.send_order(res_a.reservation.reservation_id, 'ORD-A')
        ctrl.order_ack('ORD-A')
        ctrl.order_fill('ORD-A', Decimal('150'), Decimal('1.5'))
        ctrl.order_cancel('ORD-A')

        ctrl.send_order(res_b.reservation.reservation_id, 'ORD-B')
        ctrl.order_reject('ORD-B')

        committed = (
            ctrl._state.reservation_notional
            + ctrl._state.in_flight_order_notional
            + ctrl._state.working_order_notional
            + ctrl._state.position_notional
        )
        per_strategy_total = sum(ctrl._state.per_strategy_deployed.values(), _ZERO)

        assert per_strategy_total == committed
        assert ctrl._state.per_strategy_deployed == {'strat_a': Decimal('151.5')}

    def test_underflow_logs_warning_and_removes_strategy(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        ctrl = _make_controller(per_strategy_deployed={'strat_a': Decimal('1')})

        with caplog.at_level('WARNING'):
            ctrl._adjust_strategy_deployed('strat_a', Decimal('-2'))

        assert 'Per-strategy deployed underflow' in caplog.text
        assert 'strat_a' not in ctrl._state.per_strategy_deployed


class TestReconcileAtBoot:
    '''PT-FIX-35: stranded `in_flight_order_notional` /
    `working_order_notional` / `reservation_notional` carried over from
    a crashed prior boot must be reset to zero so capital is available
    for the freshly-booting strategy.

    `_orders` and `_reservations` are in-memory only and rebuilt fresh
    per `CapitalController` construction. The persisted aggregates
    have no corresponding TrackedOrders to release them via the
    normal `order_ack` / `order_fill` / `order_reject` /
    `order_cancel` lifecycle.

    PT-FIX-30 added Praxis-side orphan reconciliation that synthesizes
    `TradeOutcome(REJECTED, reason='boot_orphan_command')` for spine
    `CommandAccepted` events with no follow-up `OrderSubmitIntent`.
    Those outcomes flow through the launcher's per-account queue, but
    the launcher's `command_contexts` is empty for orphan command_ids,
    so the OutcomeProcessor is bypassed. Even if it weren't bypassed,
    `CapitalController._orders` is empty so `order_reject` would
    return `success=False`. This boot-time aggregate reset is the
    pragmatic recovery path.
    '''

    def test_no_op_when_aggregates_are_zero(self) -> None:
        ctrl = _make_controller()

        ctrl.reconcile_at_boot()

        assert ctrl._state.reservation_notional == _ZERO
        assert ctrl._state.in_flight_order_notional == _ZERO
        assert ctrl._state.working_order_notional == _ZERO

    def test_resets_stranded_in_flight_aggregate(self) -> None:
        ctrl = _make_controller(in_flight_order_notional=Decimal('250'))

        ctrl.reconcile_at_boot()

        assert ctrl._state.in_flight_order_notional == _ZERO

    def test_resets_stranded_working_aggregate(self) -> None:
        ctrl = _make_controller(working_order_notional=Decimal('500'))

        ctrl.reconcile_at_boot()

        assert ctrl._state.working_order_notional == _ZERO

    def test_resets_stranded_reservation_aggregate(self) -> None:
        ctrl = _make_controller(reservation_notional=Decimal('120'))

        ctrl.reconcile_at_boot()

        assert ctrl._state.reservation_notional == _ZERO

    def test_resets_all_three_simultaneously(self) -> None:
        ctrl = _make_controller(
            reservation_notional=Decimal('10'),
            in_flight_order_notional=Decimal('20'),
            working_order_notional=Decimal('30'),
        )

        ctrl.reconcile_at_boot()

        assert ctrl._state.reservation_notional == _ZERO
        assert ctrl._state.in_flight_order_notional == _ZERO
        assert ctrl._state.working_order_notional == _ZERO

    def test_does_not_touch_position_or_per_strategy_aggregates(self) -> None:
        '''Position notional and per_strategy_deployed are unaffected —
        positions are real (in `state.positions`) and per_strategy
        deployment is derived from positions.'''

        ctrl = _make_controller(
            position_notional=Decimal('1000'),
            per_strategy_deployed={'strat_a': Decimal('500')},
            in_flight_order_notional=Decimal('100'),
        )

        ctrl.reconcile_at_boot()

        assert ctrl._state.position_notional == Decimal('1000')
        assert ctrl._state.per_strategy_deployed == {'strat_a': Decimal('500')}

    def test_logs_warning_for_each_reset_aggregate(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        ctrl = _make_controller(
            reservation_notional=Decimal('10'),
            in_flight_order_notional=Decimal('20'),
            working_order_notional=Decimal('30'),
        )

        with caplog.at_level('WARNING'):
            ctrl.reconcile_at_boot()

        assert 'reservation_notional' in caplog.text
        assert 'in_flight_order_notional' in caplog.text
        assert 'working_order_notional' in caplog.text

    def test_raises_when_called_after_orders_tracked(self) -> None:
        '''reconcile_at_boot is a boot-time invariant. Calling it after
        any reservation or send_order has been processed indicates a
        misuse — raise loudly rather than silently corrupt state.'''

        ctrl = _make_controller()
        result = _reserve(ctrl)
        assert result.granted is True

        with pytest.raises(RuntimeError, match='reconcile_at_boot called after'):
            ctrl.reconcile_at_boot()


def _position(
    trade_id: str,
    strategy_id: str | None,
    *,
    size: Decimal = Decimal('1'),
    entry_price: Decimal = Decimal('100'),
    avg_cost_basis: Decimal | None = None,
) -> Position:
    return Position(
        trade_id=trade_id,
        strategy_id=strategy_id or 'placeholder',
        symbol='BTCUSDT',
        side=OrderSide.BUY,
        size=size,
        entry_price=entry_price,
        avg_cost_basis=entry_price if avg_cost_basis is None else avg_cost_basis,
    )


class TestReconcileAtBootRebuildsPerStrategyDeployed:
    '''PT-FIX-41: PT-FIX-35's `reconcile_at_boot` zeros stranded
    reservation/in_flight/working aggregates but pre-fix did NOT
    touch `per_strategy_deployed`. The persisted attribution map
    still summed to the pre-crash total (position + working +
    in_flight + reservation), but `total_deployed` after reconcile
    only includes `position_notional`. The next `check_and_reserve`
    fires the attribution-mismatch denial and permanently blocks
    new ENTERs.

    Post-fix: `reconcile_at_boot(positions=...)` rebuilds
    `per_strategy_deployed` from live positions so it sums to
    `position_notional` exactly.'''

    def test_rebuilds_per_strategy_deployed_from_positions(self) -> None:
        ctrl = _make_controller(
            position_notional=Decimal('100'),
            working_order_notional=Decimal('50'),
            per_strategy_deployed={'strat_a': Decimal('150')},
        )
        positions = [_position('t-1', 'strat_a', size=Decimal('1'), entry_price=Decimal('100'))]

        ctrl.reconcile_at_boot(positions=positions)

        assert ctrl._state.working_order_notional == _ZERO
        assert ctrl._state.per_strategy_deployed == {'strat_a': Decimal('100')}

    def test_zero_avg_cost_basis_falls_back_to_entry_price(self) -> None:
        '''Non-flat position with `avg_cost_basis == 0` (legacy /
        partial-recovery path or placeholder accidentally reused as
        real) must NOT under-attribute `per_strategy_deployed`. Pre-fix
        the rebuild used `pos.size * 0 = 0` and the very next
        `check_and_reserve` fired the attribution-mismatch denial.
        Post-fix `entry_price` is the fallback and a WARNING surfaces
        the placeholder so it can be investigated.
        '''

        ctrl = _make_controller(
            position_notional=Decimal('100'),
            per_strategy_deployed={'strat_a': Decimal('100')},
        )
        positions = [_position(
            't-1', 'strat_a',
            size=Decimal('1'),
            entry_price=Decimal('100'),
            avg_cost_basis=Decimal('0'),
        )]

        ctrl.reconcile_at_boot(positions=positions)

        assert ctrl._state.per_strategy_deployed == {'strat_a': Decimal('100')}

    def test_zero_avg_cost_basis_and_zero_entry_price_under_attributes(self) -> None:
        '''When both `avg_cost_basis` and `entry_price` are zero the
        rebuild has no good fallback; deployed capital is understated
        and a WARNING is logged. This case is not normally reachable
        (Position invariants reject zero `entry_price` on construct)
        but the defense still lands when the field is mutated post-
        construction.
        '''

        ctrl = _make_controller(
            position_notional=_ZERO,
            per_strategy_deployed={'strat_a': _ZERO},
        )
        pos = _position(
            't-1', 'strat_a',
            size=Decimal('1'),
            entry_price=Decimal('100'),
            avg_cost_basis=Decimal('0'),
        )
        pos.entry_price = Decimal('0')

        ctrl.reconcile_at_boot(positions=[pos])

        assert ctrl._state.per_strategy_deployed == {'strat_a': _ZERO}

    def test_first_check_and_reserve_after_reconcile_passes_attribution(self) -> None:
        '''The actual fix-validation: after a crash-recovery boot with
        stranded aggregates and stale per_strategy_deployed, calling
        `check_and_reserve` for a fresh ENTER must NOT hit the
        attribution-mismatch denial.'''

        ctrl = _make_controller(
            position_notional=Decimal('100'),
            in_flight_order_notional=Decimal('50'),
            per_strategy_deployed={'strat_a': Decimal('150')},
        )
        positions = [_position('t-1', 'strat_a', size=Decimal('1'), entry_price=Decimal('100'))]

        ctrl.reconcile_at_boot(positions=positions)

        result = ctrl.check_and_reserve(
            strategy_id='strat_b',
            order_notional=Decimal('100'),
            estimated_fees=Decimal('1'),
            strategy_budget=Decimal('5000'),
        )

        assert result.granted is True
        assert result.denial_reason is None

    def test_omitting_positions_preserves_existing_per_strategy(self) -> None:
        '''Backwards compatibility: callers (e.g., tests) that pass no
        `positions` get only the aggregate reset (PT-FIX-35 behavior).
        `per_strategy_deployed` is left unchanged.'''

        ctrl = _make_controller(
            in_flight_order_notional=Decimal('50'),
            per_strategy_deployed={'strat_a': Decimal('50')},
        )

        ctrl.reconcile_at_boot()

        assert ctrl._state.in_flight_order_notional == _ZERO
        assert ctrl._state.per_strategy_deployed == {'strat_a': Decimal('50')}

    def test_empty_positions_clears_per_strategy(self) -> None:
        '''A flat-state recovery (no open positions) with stale per-
        strategy entries clears the map entirely.'''

        ctrl = _make_controller(
            in_flight_order_notional=Decimal('50'),
            per_strategy_deployed={'strat_a': Decimal('50')},
        )

        ctrl.reconcile_at_boot(positions=[])

        assert ctrl._state.in_flight_order_notional == _ZERO
        assert ctrl._state.per_strategy_deployed == {}

    def test_multi_strategy_attribution_summed_correctly(self) -> None:
        ctrl = _make_controller(
            position_notional=Decimal('500'),
            working_order_notional=Decimal('100'),
            per_strategy_deployed={
                'strat_a': Decimal('200'),
                'strat_b': Decimal('400'),
            },
        )
        positions = [
            _position('t-1', 'strat_a', size=Decimal('1'), entry_price=Decimal('100')),
            _position('t-2', 'strat_a', size=Decimal('1'), entry_price=Decimal('100')),
            _position('t-3', 'strat_b', size=Decimal('3'), entry_price=Decimal('100')),
        ]

        ctrl.reconcile_at_boot(positions=positions)

        assert ctrl._state.per_strategy_deployed == {
            'strat_a': Decimal('200'),
            'strat_b': Decimal('300'),
        }

    def test_logs_warning_when_per_strategy_changes(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        ctrl = _make_controller(
            position_notional=Decimal('100'),
            per_strategy_deployed={'strat_a': Decimal('150')},
        )
        positions = [_position('t-1', 'strat_a', size=Decimal('1'), entry_price=Decimal('100'))]

        with caplog.at_level('WARNING'):
            ctrl.reconcile_at_boot(positions=positions)

        assert 'per_strategy_deployed' in caplog.text


class TestReconcileAtBootResetsPendingExit:
    '''MAJOR-R: a persisted Position with `pending_exit > 0` (e.g., from
    a crash mid-EXIT before the terminal arrived, or from a boot-orphan
    REJECTED that never reached `_clear_pending_exit`) must be reset to
    zero with WARN at boot. Pre-fix the stuck value made the next-boot
    intake deny the next EXIT with `INTAKE_EXIT_SIZE_EXCEEDS_REMAINING`.
    Defense-in-depth: shutdown-time MAJOR-I now also clears pending_exit
    via OutcomeProcessor routing, but boot-time reset closes any
    remaining gap (orphan REJECTED path; future leak paths).
    '''

    def test_pending_exit_reset_to_zero_on_boot(self) -> None:
        ctrl = _make_controller(
            position_notional=Decimal('100'),
            per_strategy_deployed={'strat_a': Decimal('100')},
        )
        pos = _position('t-1', 'strat_a', size=Decimal('1'), entry_price=Decimal('100'))
        pos.pending_exit = Decimal('0.5')

        ctrl.reconcile_at_boot(positions=[pos])

        assert pos.pending_exit == _ZERO

    def test_pending_exit_zero_no_warning_logged(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        ctrl = _make_controller(
            position_notional=Decimal('100'),
            per_strategy_deployed={'strat_a': Decimal('100')},
        )
        pos = _position('t-1', 'strat_a', size=Decimal('1'), entry_price=Decimal('100'))

        with caplog.at_level('WARNING'):
            ctrl.reconcile_at_boot(positions=[pos])

        assert 'pending_exit' not in caplog.text

    def test_pending_exit_nonzero_logs_warning(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        ctrl = _make_controller(
            position_notional=Decimal('100'),
            per_strategy_deployed={'strat_a': Decimal('100')},
        )
        pos = _position('t-1', 'strat_a', size=Decimal('1'), entry_price=Decimal('100'))
        pos.pending_exit = Decimal('0.3')

        with caplog.at_level('WARNING'):
            ctrl.reconcile_at_boot(positions=[pos])

        assert 'stranded pending_exit' in caplog.text

    def test_pending_exit_reset_only_when_positions_provided(self) -> None:
        '''When `positions` is omitted (legacy-test invocation),
        pending_exit reset doesn't run because there are no positions
        to iterate. The reset only fires through the `positions` path.
        '''

        ctrl = _make_controller(position_notional=Decimal('100'))
        pos = _position('t-1', 'strat_a', size=Decimal('1'), entry_price=Decimal('100'))
        pos.pending_exit = Decimal('0.5')

        ctrl.reconcile_at_boot()

        assert pos.pending_exit == Decimal('0.5')


class TestFinalMajor06ExitBoundaryTolerance:
    '''FINAL-MAJOR-06: `_compute_exit_cost_basis` round-trips
    `(N/q) * q` and can overshoot `position_notional` by sub-ULP at
    default Decimal precision (28 digits). Pre-fix `order_exit`'s
    strict `>` boundary at line 692 would falsely reject the EXIT
    FILL with INVARIANT_BREACH; `_handle_fill` then returns
    success=False BEFORE `_reduce_position`, so the venue closed the
    position but Nexus did not — `position_notional` and
    `per_strategy_deployed[sid]` stay inflated, the strategy is
    locked out of new ENTERs by its own budget until the next boot's
    `_reconcile_capital` heals it.

    Post-fix `order_exit` uses an `_EXIT_BOUNDARY_TOLERANCE`
    (1E-12 quote) on the `>` checks and clamps the actual decrement
    to the available aggregate so neither `position_notional` nor
    `per_strategy_deployed` can be driven negative. Beyond-tolerance
    overshoots still hard-fail with INVARIANT_BREACH.
    '''

    def test_n5_q3_repro_succeeds_post_fix(self) -> None:
        '''The verified single-fill repro from R17-C addendum §1:
        avg_cost_basis = 5/3 = 1.666...666...7 (last digit rounds up
        per ROUND_HALF_EVEN); cost_basis_released = avg_cost_basis * 3
        = 5.000...001 (overshoots position_notional=5 by 1E-27).
        Pre-fix returns INVARIANT_BREACH; post-fix succeeds, position
        and strategy aggregates clamp to zero, no negative values.
        '''

        position_notional = Decimal('5')
        avg_cost_basis = position_notional / Decimal('3')
        cost_basis_released = avg_cost_basis * Decimal('3')
        assert cost_basis_released > position_notional, (
            'pre-condition: the repro expression must overshoot'
        )

        ctrl = _make_controller(
            position_notional=position_notional,
            per_strategy_deployed={'strat_a': position_notional},
        )

        result = ctrl.order_exit('strat_a', cost_basis_released)

        assert result.success is True, (
            f'post-fix order_exit should succeed despite sub-ULP '
            f'overshoot; got: {result.reason}'
        )
        assert ctrl._state.position_notional == _ZERO
        assert 'strat_a' not in ctrl._state.per_strategy_deployed

    def test_within_tolerance_overshoot_clamps_aggregates_to_zero(self) -> None:
        position_notional = Decimal('100')
        cost_basis_released = position_notional + Decimal('1E-15')
        ctrl = _make_controller(
            position_notional=position_notional,
            per_strategy_deployed={'strat_a': position_notional},
        )

        result = ctrl.order_exit('strat_a', cost_basis_released)

        assert result.success is True
        assert ctrl._state.position_notional == _ZERO
        assert 'strat_a' not in ctrl._state.per_strategy_deployed

    def test_beyond_tolerance_overshoot_still_rejects(self) -> None:
        '''A real overshoot (a satoshi-scale or larger excess) must
        still fail with INVARIANT_BREACH; the tolerance only absorbs
        sub-ULP rounding noise, not material breaches.
        '''

        position_notional = Decimal('100')
        cost_basis_released = position_notional + Decimal('1E-6')
        ctrl = _make_controller(
            position_notional=position_notional,
            per_strategy_deployed={'strat_a': position_notional},
        )

        result = ctrl.order_exit('strat_a', cost_basis_released)

        assert result.success is False
        assert result.category == FailureCategory.INVARIANT_BREACH
        assert ctrl._state.position_notional == position_notional
        assert ctrl._state.per_strategy_deployed['strat_a'] == position_notional

    def test_sweep_full_triggering_grid_all_succeed(self) -> None:
        '''R17-C addendum §1 documented 155 distinct triggering
        integer pairs in `notional in [1..200] x size in [2..20]`.
        Sweep the full 200x19 = 3800-pair grid, identify every pair
        whose `(N/q) * q` overshoots `N`, and assert each one's
        `order_exit` succeeds post-fix. Pre-fix every triggering pair
        would return INVARIANT_BREACH.
        '''

        triggering_pairs = [
            (Decimal(n), Decimal(q))
            for n in range(1, 201)
            for q in range(2, 21)
            if (Decimal(n) / Decimal(q)) * Decimal(q) > Decimal(n)
        ]
        assert len(triggering_pairs) > 100, (
            f'sweep should surface >100 triggering pairs at default Decimal '
            f'precision; got {len(triggering_pairs)}'
        )

        failures: list[str] = []
        for notional, size in triggering_pairs:
            avg_cost_basis = notional / size
            released = avg_cost_basis * size
            ctrl = _make_controller(
                position_notional=notional,
                per_strategy_deployed={'strat_a': notional},
            )

            result = ctrl.order_exit('strat_a', released)

            if not result.success:
                failures.append(
                    f'(N={notional}, q={size}): {result.reason}'
                )

        assert not failures, (
            f'{len(failures)} of {len(triggering_pairs)} triggering pairs '
            f'failed; first 5: {failures[:5]}'
        )


class TestFinalMajor09OrderFillAttributionLockstep:
    '''FINAL-MAJOR-09: pre-fix `order_fill` computes
    `proportional_estimated` and `fill_with_estimated` via a
    `pre_fill_remaining - updated.remaining_total` round trip through
    the `(remaining_notional * estimated_fees) / notional` formula.
    The audit (R17-C MAJOR-ND) flagged this as cumulative per-partial
    drift that could leave `per_strategy_deployed[sid]` out of step
    with `position_notional` after several scale-ins, eventually
    tripping the per-strategy attribution-mismatch denial in
    `check_and_reserve` (capital_controller.py:333).

    Empirical: with default Decimal precision (28 sig digits), the
    round-trip subtraction has cancellation properties that keep
    attribution in lockstep with total_deployed across realistic
    multi-partial scale-ins. The strict equality denial does not
    fire. The audit's reachable failure mode is the order_exit
    boundary trip (FINAL-MAJOR-06), which is closed by the
    `_EXIT_BOUNDARY_TOLERANCE` clamp.

    These tests pin the lockstep + no-residue properties so a future
    "fix" cannot regress them.
    '''

    def test_seven_partial_scale_in_settles_zero_residue(self) -> None:
        '''7 equal partials of 1 unit each on an awkward
        notional=7, estimated_fees=1 ratio (no clean Decimal
        terminator). Working settles to zero; attribution stays
        in lockstep with total deployed.
        '''

        ctrl = _make_controller()
        result = _reserve(ctrl, notional='7', fees='1', budget='100')
        assert result.reservation is not None
        ctrl.send_order(result.reservation.reservation_id, 'ORD-001')
        ctrl.order_ack('ORD-001')

        # 7 partials with realistic actual_fees ~1/7 = 0.142857...
        actual_fee_per_fill = Decimal('1') / Decimal('7')
        for _ in range(7):
            ctrl.order_fill(
                'ORD-001', Decimal('1'), actual_fee_per_fill,
            )

        assert ctrl._state.working_order_notional == _ZERO, (
            f'working_order_notional residue: '
            f'{ctrl._state.working_order_notional}'
        )

        total_deployed = (
            ctrl._state.position_notional
            + ctrl._state.working_order_notional
            + ctrl._state.in_flight_order_notional
            + ctrl._state.reservation_notional
        )
        attributed = sum(
            ctrl._state.per_strategy_deployed.values(), _ZERO,
        )
        assert attributed == total_deployed, (
            f'attribution mismatch: per_strategy_total={attributed} '
            f'total_deployed={total_deployed} '
            f'mismatch={attributed - total_deployed}'
        )

    def test_one_hundred_partial_scale_in_attribution_lockstep(self) -> None:
        '''100 partials at notional=100, fee=0.07 (representative
        Binance maker fee on a 100-unit order). Asserts attribution
        stays exactly equal to total_deployed across all 100 fills.
        Pre-fix this was the worry; post-fix verifies it does not
        manifest under default Decimal precision.
        '''

        ctrl = _make_controller()
        result = _reserve(
            ctrl, notional='100', fees='0.07', budget='1000',
        )
        assert result.reservation is not None
        ctrl.send_order(result.reservation.reservation_id, 'ORD-001')
        ctrl.order_ack('ORD-001')

        actual_fee_per_fill = Decimal('0.07') / Decimal('100')
        for _ in range(100):
            ctrl.order_fill(
                'ORD-001', Decimal('1'), actual_fee_per_fill,
            )
            total_deployed = (
                ctrl._state.position_notional
                + ctrl._state.working_order_notional
                + ctrl._state.in_flight_order_notional
                + ctrl._state.reservation_notional
            )
            attributed = sum(
                ctrl._state.per_strategy_deployed.values(), _ZERO,
            )
            assert attributed == total_deployed, (
                f'mid-fill attribution mismatch after fill: '
                f'attributed={attributed} total={total_deployed}'
            )

        assert ctrl._state.working_order_notional == _ZERO
