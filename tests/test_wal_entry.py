'''Verify WALEntry and WALEntryType construction and validation.'''

from __future__ import annotations

from datetime import datetime

import pytest

from nexus.infrastructure.wal_entry import WALEntry, WALEntryType


def test_entry_type_members() -> None:
    '''Verify WALEntryType has exactly three members.'''

    assert set(WALEntryType) == {
        WALEntryType.SNAPSHOT,
        WALEntryType.STATE_MUTATION,
        WALEntryType.STRATEGY_EVENT,
    }


def test_entry_type_values() -> None:
    '''Verify WALEntryType string values.'''

    assert WALEntryType.SNAPSHOT.value == 'snapshot'
    assert WALEntryType.STATE_MUTATION.value == 'state_mutation'
    assert WALEntryType.STRATEGY_EVENT.value == 'strategy_event'


def test_valid_entry() -> None:
    '''Verify WALEntry accepts valid construction arguments.'''

    ts = datetime(2025, 1, 1, 12, 0, 0)
    entry = WALEntry(
        sequence=0,
        timestamp=ts,
        entry_type=WALEntryType.SNAPSHOT,
        payload=b'\x00\x01',
    )
    assert entry.sequence == 0
    assert entry.timestamp == ts
    assert entry.entry_type == WALEntryType.SNAPSHOT
    assert entry.payload == b'\x00\x01'


def test_entry_is_frozen() -> None:
    '''Verify WALEntry is immutable.'''

    entry = WALEntry(
        sequence=1,
        timestamp=datetime.min,
        entry_type=WALEntryType.STATE_MUTATION,
        payload=b'',
    )
    with pytest.raises(AttributeError):
        entry.sequence = 2  # type: ignore[misc]


def test_negative_sequence_rejected() -> None:
    '''Verify negative sequence number raises ValueError.'''

    with pytest.raises(ValueError, match='non-negative'):
        WALEntry(
            sequence=-1,
            timestamp=datetime.min,
            entry_type=WALEntryType.SNAPSHOT,
            payload=b'',
        )


def test_invalid_timestamp_rejected() -> None:
    '''Verify non-datetime timestamp raises ValueError.'''

    with pytest.raises(ValueError, match='datetime'):
        WALEntry(
            sequence=0,
            timestamp='not-a-datetime',  # type: ignore[arg-type]
            entry_type=WALEntryType.SNAPSHOT,
            payload=b'',
        )


def test_invalid_entry_type_rejected() -> None:
    '''Verify non-WALEntryType raises ValueError.'''

    with pytest.raises(ValueError, match='WALEntryType'):
        WALEntry(
            sequence=0,
            timestamp=datetime.min,
            entry_type='snapshot',  # type: ignore[arg-type]
            payload=b'',
        )


def test_invalid_payload_rejected() -> None:
    '''Verify non-bytes payload raises ValueError.'''

    with pytest.raises(ValueError, match='bytes'):
        WALEntry(
            sequence=0,
            timestamp=datetime.min,
            entry_type=WALEntryType.SNAPSHOT,
            payload='not-bytes',  # type: ignore[arg-type]
        )
