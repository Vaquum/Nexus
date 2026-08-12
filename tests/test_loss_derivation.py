'''Verify rolling loss re-derivation from strategy events.'''

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from nexus.infrastructure.loss_derivation import (
    RollingLosses,
    derive_rolling_losses,
    derive_strategy_realized_pnl,
)
from nexus.infrastructure.strategy_event import StrategyEvent

_NOW = datetime(2026, 3, 19, 12, 0, 0, tzinfo=timezone.utc)
_ZERO = Decimal(0)


def _event(
    pnl: str,
    hours_ago: float,
    strategy_id: str = 'strat_a',
) -> StrategyEvent:
    return StrategyEvent(
        strategy_id=strategy_id,
        event_type='trade_outcome',
        realized_pnl=Decimal(pnl),
        timestamp=_NOW - timedelta(hours=hours_ago),
    )


class TestEmptyInput:
    def test_no_events_returns_empty(self) -> None:
        result = derive_rolling_losses([], _NOW)
        assert result == {}

    def test_only_positive_pnl_returns_empty(self) -> None:
        events = [_event('100', 1), _event('50', 2)]
        result = derive_rolling_losses(events, _NOW)
        assert result == {}

    def test_zero_pnl_returns_empty(self) -> None:
        events = [_event('0', 1)]
        result = derive_rolling_losses(events, _NOW)
        assert result == {}


class TestSingleWindow:
    def test_loss_within_24h(self) -> None:
        events = [_event('-50', 1)]
        result = derive_rolling_losses(events, _NOW)
        assert result['strat_a'].rolling_loss_24h == Decimal('50')
        assert result['strat_a'].rolling_loss_7d == Decimal('50')
        assert result['strat_a'].rolling_loss_30d == Decimal('50')

    def test_loss_outside_24h_within_7d(self) -> None:
        events = [_event('-50', 48)]
        result = derive_rolling_losses(events, _NOW)
        assert result['strat_a'].rolling_loss_24h == _ZERO
        assert result['strat_a'].rolling_loss_7d == Decimal('50')
        assert result['strat_a'].rolling_loss_30d == Decimal('50')

    def test_loss_outside_7d_within_30d(self) -> None:
        events = [_event('-50', 24 * 10)]
        result = derive_rolling_losses(events, _NOW)
        assert result['strat_a'].rolling_loss_24h == _ZERO
        assert result['strat_a'].rolling_loss_7d == _ZERO
        assert result['strat_a'].rolling_loss_30d == Decimal('50')

    def test_loss_outside_30d_excluded(self) -> None:
        events = [_event('-50', 24 * 31)]
        result = derive_rolling_losses(events, _NOW)
        assert result == {}


class TestWindowBoundaries:
    def test_exactly_at_24h_boundary_included(self) -> None:
        events = [_event('-25', 24)]
        result = derive_rolling_losses(events, _NOW)
        assert result['strat_a'].rolling_loss_24h == Decimal('25')

    def test_one_second_past_24h_excluded_from_24h(self) -> None:
        event = StrategyEvent(
            strategy_id='strat_a',
            event_type='trade_outcome',
            realized_pnl=Decimal('-25'),
            timestamp=_NOW - timedelta(hours=24, seconds=1),
        )
        result = derive_rolling_losses([event], _NOW)
        assert result['strat_a'].rolling_loss_24h == _ZERO
        assert result['strat_a'].rolling_loss_7d == Decimal('25')


class TestMultipleStrategies:
    def test_losses_grouped_by_strategy(self) -> None:
        events = [
            _event('-10', 1, strategy_id='strat_a'),
            _event('-20', 1, strategy_id='strat_b'),
            _event('-30', 1, strategy_id='strat_a'),
        ]
        result = derive_rolling_losses(events, _NOW)
        assert result['strat_a'].rolling_loss_24h == Decimal('40')
        assert result['strat_b'].rolling_loss_24h == Decimal('20')


class TestAccumulation:
    def test_multiple_losses_summed(self) -> None:
        events = [
            _event('-10', 1),
            _event('-20', 2),
            _event('-30', 3),
        ]
        result = derive_rolling_losses(events, _NOW)
        assert result['strat_a'].rolling_loss_24h == Decimal('60')

    def test_mixed_pnl_only_losses_counted(self) -> None:
        events = [
            _event('-50', 1),
            _event('100', 2),
            _event('-25', 3),
        ]
        result = derive_rolling_losses(events, _NOW)
        assert result['strat_a'].rolling_loss_24h == Decimal('75')


class TestRollingLossesDataclass:
    def test_defaults_to_zero(self) -> None:
        rl = RollingLosses()
        assert rl.rolling_loss_24h == _ZERO
        assert rl.rolling_loss_7d == _ZERO
        assert rl.rolling_loss_30d == _ZERO

    def test_frozen(self) -> None:
        rl = RollingLosses()
        try:
            rl.rolling_loss_24h = Decimal('1')  # type: ignore[misc]
            raise AssertionError('Should have raised')
        except AttributeError:
            pass


class TestFinalTd02OutcomeIdDedup:
    '''FINAL-TD-02: pre-fix `derive_rolling_losses` did not dedupe
    events. A Praxis re-delivery of a terminal outcome that was
    already emitted pre-crash would produce duplicate STRATEGY_EVENTs
    in the WAL, double-counting the loss across rolling windows.
    Same applies to `derive_strategy_realized_pnl`.

    Post-fix both functions skip events whose `outcome_id` has been
    seen. Empty `outcome_id` (legacy v1-codec events that predate
    this field) is NOT deduped; processed at face value.
    '''

    def test_duplicate_outcome_id_loss_counted_once(self) -> None:
        e1 = StrategyEvent(
            strategy_id='strat_a',
            event_type='trade_outcome',
            realized_pnl=Decimal('-100'),
            timestamp=_NOW - timedelta(hours=1),
            outcome_id='out_001',
        )
        e2 = StrategyEvent(
            strategy_id='strat_a',
            event_type='trade_outcome',
            realized_pnl=Decimal('-100'),
            timestamp=_NOW - timedelta(hours=1),
            outcome_id='out_001',
        )

        result = derive_rolling_losses([e1, e2], _NOW)
        assert result['strat_a'].rolling_loss_24h == Decimal('100'), (
            f'duplicate outcome_id should count once, got '
            f'{result["strat_a"].rolling_loss_24h}'
        )

    def test_empty_outcome_id_not_deduped_legacy_compat(self) -> None:
        '''Legacy v1-codec events have outcome_id=''. They are NOT
        deduped — the legacy WAL contract permitted duplicate-emission
        and downstream systems counted them; deduping them now would
        break recovery semantics for old WAL files.
        '''

        e1 = StrategyEvent(
            strategy_id='strat_a',
            event_type='trade_outcome',
            realized_pnl=Decimal('-50'),
            timestamp=_NOW - timedelta(hours=1),
        )
        e2 = StrategyEvent(
            strategy_id='strat_a',
            event_type='trade_outcome',
            realized_pnl=Decimal('-50'),
            timestamp=_NOW - timedelta(hours=1),
        )

        result = derive_rolling_losses([e1, e2], _NOW)
        assert result['strat_a'].rolling_loss_24h == Decimal('100')

    def test_distinct_outcome_ids_both_counted(self) -> None:
        '''Sanity: two genuinely distinct outcomes with their own
        outcome_ids are BOTH counted. The dedup only catches exact
        repeats.
        '''

        e1 = StrategyEvent(
            strategy_id='strat_a',
            event_type='trade_outcome',
            realized_pnl=Decimal('-100'),
            timestamp=_NOW - timedelta(hours=1),
            outcome_id='out_001',
        )
        e2 = StrategyEvent(
            strategy_id='strat_a',
            event_type='trade_outcome',
            realized_pnl=Decimal('-100'),
            timestamp=_NOW - timedelta(hours=1),
            outcome_id='out_002',
        )

        result = derive_rolling_losses([e1, e2], _NOW)
        assert result['strat_a'].rolling_loss_24h == Decimal('200')

    def test_strategy_realized_pnl_dedup(self) -> None:
        '''Same dedup rule applies to derive_strategy_realized_pnl.'''

        e1 = StrategyEvent(
            strategy_id='strat_a',
            event_type='trade_outcome',
            realized_pnl=Decimal('150'),
            timestamp=_NOW - timedelta(hours=1),
            outcome_id='out_001',
        )
        e2 = StrategyEvent(
            strategy_id='strat_a',
            event_type='trade_outcome',
            realized_pnl=Decimal('150'),
            timestamp=_NOW - timedelta(hours=1),
            outcome_id='out_001',
        )

        result = derive_strategy_realized_pnl([e1, e2])
        assert result['strat_a'] == Decimal('150'), (
            f'duplicate outcome_id should count once, got {result["strat_a"]}'
        )
