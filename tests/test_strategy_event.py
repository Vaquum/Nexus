'''Verify StrategyEvent construction and validation.'''

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from typing import Any

import pytest

from nexus.infrastructure.strategy_event import StrategyEvent


def _make_event(**overrides: Any) -> StrategyEvent:
    defaults: dict[str, Any] = {
        'strategy_id': 'strat_a',
        'event_type': 'trade_outcome',
        'realized_pnl': Decimal('-50.25'),
        'timestamp': datetime(2026, 3, 19, 12, 0, 0, tzinfo=timezone.utc),
    }
    defaults.update(overrides)
    return StrategyEvent(**defaults)


class TestValidConstruction:
    def test_basic_construction(self) -> None:
        event = _make_event()
        assert event.strategy_id == 'strat_a'
        assert event.event_type == 'trade_outcome'
        assert event.realized_pnl == Decimal('-50.25')
        assert event.timestamp == datetime(2026, 3, 19, 12, 0, 0, tzinfo=timezone.utc)

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

    def test_naive_timestamp_rejected(self) -> None:
        with pytest.raises(ValueError, match='must be UTC'):
            _make_event(timestamp=datetime(2026, 3, 19, 12, 0, 0))

    def test_non_utc_timestamp_rejected(self) -> None:
        non_utc = datetime(2026, 3, 19, 12, 0, 0, tzinfo=timezone(timedelta(hours=5)))
        with pytest.raises(ValueError, match='must be UTC'):
            _make_event(timestamp=non_utc)


class TestOutcomeIdValidation:
    '''PR #55 round-8 review: pin the new `outcome_id` invariants
    directly on `StrategyEvent.__post_init__` so future refactors
    cannot relax them independently of the WAL codec tests.
    '''

    def test_default_empty_string_accepted(self) -> None:
        '''The empty-string default is the legacy v1 marker; must
        construct cleanly so `_decode_event_v1` keeps working.
        '''

        event = _make_event()
        assert event.outcome_id == ''

    def test_explicit_non_empty_string_accepted(self) -> None:
        '''Production producers pass a non-empty venue outcome id;
        it must round-trip through construction unchanged.
        '''

        event = _make_event(outcome_id='outcome-abc-123')
        assert event.outcome_id == 'outcome-abc-123'

    def test_non_string_outcome_id_rejected(self) -> None:
        '''Type errors must fail at construction time, not later in
        the WAL codec or the dedup set.
        '''

        with pytest.raises(ValueError, match='outcome_id must be a string'):
            _make_event(outcome_id=12345)

    def test_whitespace_only_outcome_id_rejected(self) -> None:
        '''A whitespace-only outcome_id would collide in the dedup
        set across distinct outcomes — must be either truly empty
        (legacy v1) or non-blank.
        '''

        with pytest.raises(ValueError, match='non-blank'):
            _make_event(outcome_id='   ')

    def test_tab_only_outcome_id_rejected(self) -> None:
        '''Tabs and newlines are also whitespace; same rejection.'''

        with pytest.raises(ValueError, match='non-blank'):
            _make_event(outcome_id='\t\n ')
