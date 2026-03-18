'''WAL entry types for Manager instance state persistence.

Defines the entry type enum and frozen dataclass representing
a single record in the write-ahead log.
'''

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

__all__ = ['WALEntry', 'WALEntryType']


class WALEntryType(Enum):
    '''Types of entries in the write-ahead log.'''

    SNAPSHOT = 'snapshot'
    STATE_MUTATION = 'state_mutation'
    STRATEGY_EVENT = 'strategy_event'


@dataclass(frozen=True)
class WALEntry:
    '''A single immutable entry in the write-ahead log.

    Args:
        sequence: Monotonically increasing sequence number.
        timestamp: When this entry was created.
        entry_type: What kind of entry this is.
        payload: Serialized content.
    '''

    sequence: int
    timestamp: datetime
    entry_type: WALEntryType
    payload: bytes

    def __post_init__(self) -> None:
        '''Validate invariants at construction time.'''

        if self.sequence < 0:
            msg = 'WALEntry.sequence must be non-negative'
            raise ValueError(msg)

        if not isinstance(self.timestamp, datetime):
            msg = 'WALEntry.timestamp must be a datetime'
            raise ValueError(msg)

        if not isinstance(self.entry_type, WALEntryType):
            msg = 'WALEntry.entry_type must be a WALEntryType member'
            raise ValueError(msg)

        if not isinstance(self.payload, bytes):
            msg = 'WALEntry.payload must be bytes'
            raise ValueError(msg)
