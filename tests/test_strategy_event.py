'''Verify StrategyEvent construction and validation.'''

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from typing import Any

from nexus.infrastructure.strategy_event import StrategyEvent


def _make_event(**overrides: Any) -> StrategyEvent:
    defaults: dict[str, Any] = {
        'strategy_id': 'strat_a',
        'event_type': 'trade_outcome',
        'realized_pnl': Decimal('-50.25'),
        'timestamp': datetime(2026, 3, 19, 12, 0, 0),
    }
    defaults.update(overrides)
    return StrategyEvent(**defaults)


class TestValidConstruction:
    def test_basic_construction(self) -> None:
        event = _make_event()
        assert event.strategy_id == 'strat_a'
        assert event.event_type == 'trade_outcome'
        assert event.realized_pnl == Decimal('-50.25')
        assert event.timestamp == datetime(2026, 3, 19, 12, 0, 0)

    def test_positive_pnl(self) -> None:
        event = _make_event(realized_pnl=Decimal('100'))
        assert event.realized_pnl == Decimal('100')

    def test_zero_pnl(self) -> None:
        event = _make_event(realized_pnl=Decimal('0'))
        assert event.realized_pnl == Decimal('0')


class TestImmutability:
    def test_cannot_set_strategy_id(self) -> None:
        event = _make_event()
        with pytest.raises(AttributeError):
            event.strategy_id = 'other'  # type: ignore[misc]

    def test_cannot_set_realized_pnl(self) -> None:
        event = _make_event()
        with pytest.raises(AttributeError):
            event.realized_pnl = Decimal('0')  # type: ignore[misc]


class TestValidation:
    def test_empty_strategy_id_rejected(self) -> None:
        with pytest.raises(ValueError, match='strategy_id'):
            _make_event(strategy_id='')

    def test_whitespace_strategy_id_rejected(self) -> None:
        with pytest.raises(ValueError, match='strategy_id'):
            _make_event(strategy_id='   ')

    def test_empty_event_type_rejected(self) -> None:
        with pytest.raises(ValueError, match='event_type'):
            _make_event(event_type='')

    def test_infinite_pnl_rejected(self) -> None:
        with pytest.raises(ValueError, match='realized_pnl'):
            _make_event(realized_pnl=Decimal('Inf'))

    def test_nan_pnl_rejected(self) -> None:
        with pytest.raises(ValueError, match='realized_pnl'):
            _make_event(realized_pnl=Decimal('NaN'))
