'''Verify Reservation and ReservationResult construction and validation.'''

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from nexus.core.capital_controller.reservation import Reservation, ReservationResult

_NOW = datetime(2026, 3, 19, 12, 0, 0, tzinfo=timezone.utc)
_LATER = _NOW + timedelta(seconds=30)


def _make_reservation(**overrides: Any) -> Reservation:
    defaults: dict[str, Any] = {
        'reservation_id': 'res_001',
        'strategy_id': 'strat_a',
        'notional': Decimal('500'),
        'estimated_fees': Decimal('1.50'),
        'created_at': _NOW,
        'expires_at': _LATER,
    }
    defaults.update(overrides)
    return Reservation(**defaults)


class TestValidConstruction:
    def test_fields_stored(self) -> None:
        r = _make_reservation()
        assert r.reservation_id == 'res_001'
        assert r.strategy_id == 'strat_a'
        assert r.notional == Decimal('500')
        assert r.estimated_fees == Decimal('1.50')
        assert r.created_at == _NOW
        assert r.expires_at == _LATER

    def test_zero_notional_accepted(self) -> None:
        r = _make_reservation(notional=Decimal('0'))
        assert r.notional == Decimal('0')

    def test_zero_fees_accepted(self) -> None:
        r = _make_reservation(estimated_fees=Decimal('0'))
        assert r.estimated_fees == Decimal('0')


class TestTotal:
    def test_total_sums_notional_and_fees(self) -> None:
        r = _make_reservation(notional=Decimal('100'), estimated_fees=Decimal('2'))
        assert r.total == Decimal('102')

    def test_total_zero_fees(self) -> None:
        r = _make_reservation(notional=Decimal('100'), estimated_fees=Decimal('0'))
        assert r.total == Decimal('100')


class TestExpiry:
    def test_not_expired_before_ttl(self) -> None:
        r = _make_reservation()
        assert r.is_expired(_NOW + timedelta(seconds=10)) is False

    def test_expired_at_exact_ttl(self) -> None:
        r = _make_reservation()
        assert r.is_expired(_LATER) is True

    def test_expired_after_ttl(self) -> None:
        r = _make_reservation()
        assert r.is_expired(_LATER + timedelta(seconds=1)) is True


class TestImmutability:
    def test_cannot_set_notional(self) -> None:
        r = _make_reservation()
        with pytest.raises(AttributeError):
            r.notional = Decimal('0')  # type: ignore[misc]

    def test_cannot_set_strategy_id(self) -> None:
        r = _make_reservation()
        with pytest.raises(AttributeError):
            r.strategy_id = 'other'  # type: ignore[misc]


class TestValidation:
    def test_empty_reservation_id_rejected(self) -> None:
        with pytest.raises(ValueError, match='reservation_id'):
            _make_reservation(reservation_id='')

    def test_empty_strategy_id_rejected(self) -> None:
        with pytest.raises(ValueError, match='strategy_id'):
            _make_reservation(strategy_id='')

    def test_negative_notional_rejected(self) -> None:
        with pytest.raises(ValueError, match='notional'):
            _make_reservation(notional=Decimal('-1'))

    def test_negative_fees_rejected(self) -> None:
        with pytest.raises(ValueError, match='estimated_fees'):
            _make_reservation(estimated_fees=Decimal('-1'))

    def test_nan_notional_rejected(self) -> None:
        with pytest.raises(ValueError, match='notional'):
            _make_reservation(notional=Decimal('NaN'))

    def test_inf_fees_rejected(self) -> None:
        with pytest.raises(ValueError, match='estimated_fees'):
            _make_reservation(estimated_fees=Decimal('Inf'))

    def test_naive_created_at_rejected(self) -> None:
        with pytest.raises(ValueError, match='timezone-aware'):
            _make_reservation(created_at=datetime(2026, 3, 19, 12, 0, 0))

    def test_naive_expires_at_rejected(self) -> None:
        with pytest.raises(ValueError, match='timezone-aware'):
            _make_reservation(expires_at=datetime(2026, 3, 19, 12, 0, 30))

    def test_expires_at_before_created_at_rejected(self) -> None:
        with pytest.raises(ValueError, match='expires_at must be after'):
            _make_reservation(expires_at=_NOW - timedelta(seconds=1))

    def test_expires_at_equal_created_at_rejected(self) -> None:
        with pytest.raises(ValueError, match='expires_at must be after'):
            _make_reservation(expires_at=_NOW)


class TestReservationResult:
    def test_granted_result(self) -> None:
        r = _make_reservation()
        result = ReservationResult(granted=True, reservation=r)
        assert result.granted is True
        assert result.reservation is r
        assert result.denial_reason is None

    def test_denied_result(self) -> None:
        result = ReservationResult(granted=False, denial_reason='insufficient capital')
        assert result.granted is False
        assert result.reservation is None
        assert result.denial_reason == 'insufficient capital'
