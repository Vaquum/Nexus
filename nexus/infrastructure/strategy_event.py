'''Strategy event record for WAL-based loss counter recovery.

Lightweight record of a trade outcome delivered to a strategy callback.
Written to WAL as STRATEGY_EVENT entries, replayed during recovery to
re-derive rolling loss counters.
'''

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

__all__ = ['StrategyEvent']


@dataclass(frozen=True)
class StrategyEvent:
    '''An immutable record of a strategy trade outcome.

    Args:
        strategy_id: Which strategy produced this event.
        event_type: Kind of event (e.g. 'trade_outcome').
        realized_pnl: Realized profit or loss from the event.
        timestamp: When this event occurred.
        outcome_id: FINAL-TD-02 — venue-side unique outcome identifier
            (`TradeOutcome.outcome_id`) carried through so
            `derive_rolling_losses` can deduplicate events when Praxis
            re-delivers a terminal outcome that was already emitted
            pre-crash. Empty string for legacy v1-codec events whose
            payloads predate this field; those events are processed
            without dedup (the legacy WAL contract).

            PRODUCER CONTRACT: every NEW production producer must set
            this field to a non-empty string. The empty-string default
            exists ONLY so `_decode_event_v1` can legally construct a
            legacy event from a payload that predates the field. Any
            production producer that constructs a `StrategyEvent`
            without setting `outcome_id` will silently emit a v1-encoded
            payload (per `serialize_event`'s conditional dispatch) and
            bypass dedup on recovery, manifesting later as duplicate
            P&L / rolling-loss accounting on Praxis re-deliveries.
            Tracked as TD-074 (split into `LegacyStrategyEvent` +
            `StrategyEvent` with required `outcome_id` for production).
    '''

    strategy_id: str
    event_type: str
    realized_pnl: Decimal
    timestamp: datetime
    outcome_id: str = ''

    def __post_init__(self) -> None:
        '''Validate invariants at construction time.'''

        if not isinstance(self.strategy_id, str) or not self.strategy_id.strip():
            msg = 'StrategyEvent.strategy_id must be a non-empty string'
            raise ValueError(msg)

        if not isinstance(self.event_type, str) or not self.event_type.strip():
            msg = 'StrategyEvent.event_type must be a non-empty string'
            raise ValueError(msg)

        if (
            not isinstance(self.realized_pnl, Decimal)
            or not self.realized_pnl.is_finite()
        ):
            msg = 'StrategyEvent.realized_pnl must be a finite Decimal'
            raise ValueError(msg)

        if not isinstance(self.timestamp, datetime):
            msg = 'StrategyEvent.timestamp must be a datetime'
            raise ValueError(msg)

        if self.timestamp.tzinfo is not timezone.utc:
            msg = 'StrategyEvent.timestamp must be UTC'
            raise ValueError(msg)

        if not isinstance(self.outcome_id, str):
            msg = 'StrategyEvent.outcome_id must be a string'
            raise ValueError(msg)

        if self.outcome_id and not self.outcome_id.strip():
            msg = (
                'StrategyEvent.outcome_id must be either empty (legacy v1) '
                'or non-blank; whitespace-only values would collide in the '
                'dedup set across distinct outcomes'
            )
            raise ValueError(msg)
