'''Verify TrackedOrder dataclass construction, validation, and properties.'''

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from nexus.core.capital_controller.tracked_order import (
    OrderLifecycleState,
    TrackedOrder,
)

_NOW = datetime.now(tz=timezone.utc)
_ZERO = Decimal(0)


def _make_order(**overrides: Any) -> TrackedOrder:
    defaults: dict[str, Any] = {
        'order_id': 'ORD-001',
        'reservation_id': 'RES-001',
        'strategy_id': 'strat_a',
        'notional': Decimal('1000'),
        'estimated_fees': Decimal('10'),
        'remaining_notional': Decimal('1000'),
        'state': OrderLifecycleState.IN_FLIGHT,
        'created_at': _NOW,
    }
    defaults.update(overrides)
    return TrackedOrder(**defaults)


class TestConstruction:
    def test_valid_order(self) -> None:
        order = _make_order()
        assert order.order_id == 'ORD-001'
        assert order.reservation_id == 'RES-001'
        assert order.strategy_id == 'strat_a'
        assert order.notional == Decimal('1000')
        assert order.estimated_fees == Decimal('10')
        assert order.remaining_notional == Decimal('1000')
        assert order.state == OrderLifecycleState.IN_FLIGHT
        assert order.created_at == _NOW

    def test_working_state(self) -> None:
        order = _make_order(state=OrderLifecycleState.WORKING)
        assert order.state == OrderLifecycleState.WORKING


class TestTotalProperty:
    def test_total_includes_fees(self) -> None:
        order = _make_order(notional=Decimal('1000'), estimated_fees=Decimal('10'))
        assert order.total == Decimal('1010')

    def test_total_zero_fees(self) -> None:
        order = _make_order(
            notional=Decimal('500'),
            estimated_fees=_ZERO,
            remaining_notional=Decimal('500'),
        )
        assert order.total == Decimal('500')


class TestRemainingTotalProperty:
    def test_remaining_total_full(self) -> None:
        order = _make_order(
            notional=Decimal('1000'),
            estimated_fees=Decimal('10'),
            remaining_notional=Decimal('1000'),
        )
        assert order.remaining_total == Decimal('1010')

    def test_remaining_total_partial(self) -> None:
        order = _make_order(
            notional=Decimal('1000'),
            estimated_fees=Decimal('10'),
            remaining_notional=Decimal('500'),
        )
        assert order.remaining_total == Decimal('505')

    def test_remaining_total_zero_notional_zero_fees(self) -> None:
        order = _make_order(
            notional=_ZERO,
            estimated_fees=_ZERO,
            remaining_notional=_ZERO,
        )
        assert order.remaining_total == _ZERO

    def test_remaining_total_zero_notional_with_fees(self) -> None:
        order = _make_order(
            notional=_ZERO,
            estimated_fees=Decimal('10'),
            remaining_notional=_ZERO,
        )
        assert order.remaining_total == Decimal('10')

    def test_remaining_total_non_terminating_ratio(self) -> None:
        order = _make_order(
            notional=Decimal('3'),
            estimated_fees=Decimal('1'),
            remaining_notional=Decimal('3'),
        )
        assert order.remaining_total == order.total


class TestImmutability:
    def test_frozen_dataclass(self) -> None:
        order = _make_order()
        with pytest.raises(AttributeError):
            order.notional = Decimal('2000')  # type: ignore[misc]

    def test_frozen_state(self) -> None:
        order = _make_order()
        with pytest.raises(AttributeError):
            order.state = OrderLifecycleState.WORKING  # type: ignore[misc]


class TestValidation:
    def test_empty_order_id_rejected(self) -> None:
        with pytest.raises(ValueError, match='order_id'):
            _make_order(order_id='')

    def test_whitespace_order_id_rejected(self) -> None:
        with pytest.raises(ValueError, match='order_id'):
            _make_order(order_id='   ')

    def test_empty_reservation_id_rejected(self) -> None:
        with pytest.raises(ValueError, match='reservation_id'):
            _make_order(reservation_id='')

    def test_empty_strategy_id_rejected(self) -> None:
        with pytest.raises(ValueError, match='strategy_id'):
            _make_order(strategy_id='')

    def test_negative_notional_rejected(self) -> None:
        with pytest.raises(ValueError, match='notional'):
            _make_order(notional=Decimal('-1'))

    def test_nan_notional_rejected(self) -> None:
        with pytest.raises(ValueError, match='notional'):
            _make_order(notional=Decimal('NaN'))

    def test_negative_fees_rejected(self) -> None:
        with pytest.raises(ValueError, match='estimated_fees'):
            _make_order(estimated_fees=Decimal('-1'))

    def test_nan_fees_rejected(self) -> None:
        with pytest.raises(ValueError, match='estimated_fees'):
            _make_order(estimated_fees=Decimal('NaN'))

    def test_negative_remaining_rejected(self) -> None:
        with pytest.raises(ValueError, match='remaining_notional'):
            _make_order(remaining_notional=Decimal('-1'))

    def test_remaining_exceeds_notional_rejected(self) -> None:
        with pytest.raises(ValueError, match='remaining_notional cannot exceed'):
            _make_order(notional=Decimal('100'), remaining_notional=Decimal('200'))

    def test_invalid_state_rejected(self) -> None:
        with pytest.raises(ValueError, match='state'):
            _make_order(state='INVALID')

    def test_naive_datetime_rejected(self) -> None:
        naive = datetime(2024, 1, 1)
        with pytest.raises(ValueError, match='timezone-aware'):
            _make_order(created_at=naive)
