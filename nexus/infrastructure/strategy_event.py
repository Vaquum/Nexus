'''Strategy event record for WAL-based loss counter recovery.

Lightweight record of a trade outcome delivered to a strategy callback.
Written to WAL as STRATEGY_EVENT entries, replayed during recovery to
re-derive rolling loss counters.
'''

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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
    '''

    strategy_id: str
    event_type: str
    realized_pnl: Decimal
    timestamp: datetime

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

        if (
            self.timestamp.tzinfo is None
            or self.timestamp.tzinfo.utcoffset(self.timestamp) is None
        ):
            msg = 'StrategyEvent.timestamp must be timezone-aware'
            raise ValueError(msg)
