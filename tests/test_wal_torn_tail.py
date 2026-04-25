'''Tests for PT-FIX-17: WAL tolerates a torn-tail record on boot.

Pre-fix: `WriteAheadLog.read_all` raised `ValueError("WAL record CRC32
mismatch")` whenever the file ended on a partially-written record.
`StateStore.__init__` called `read_all` unconditionally, so a Nexus
thread killed mid-`append` blocked the next boot before
`_recover_state` could even run.

Post-fix: `WriteAheadLog.read_safe()` bounds the read at
`_find_valid_end()` and silently stops at the torn tail, returning
the valid prefix. `StateStore.__init__`, `StateStore.recover`, and
`StateStore.read_events` all consume the safe variant. The corrupt
record's bytes are unrecoverable and discarded; the next `append()`
call truncates the dead suffix off the file (existing self-cleanup
in `append`) so future reads don't stumble over junk between valid
records.
'''

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from nexus.core.domain.instance_state import InstanceState
from nexus.infrastructure.state_store import StateStore
from nexus.infrastructure.wal import WriteAheadLog
from nexus.infrastructure.wal_codec import serialize_state
from nexus.infrastructure.wal_entry import WALEntry, WALEntryType


def _wal_path(tmp_path: Path) -> Path:
    return tmp_path / 'wal.bin'


def _entry(seq: int, state: InstanceState) -> WALEntry:
    return WALEntry(
        sequence=seq,
        timestamp=datetime(2026, 4, 25, 12, 0, seq, tzinfo=timezone.utc),
        entry_type=WALEntryType.STATE_MUTATION,
        payload=serialize_state(state),
    )


def _append_torn_record(path: Path) -> None:
    '''Corrupt the last byte of the WAL in place to simulate a torn record.

    Flipping a byte inside the final record's payload leaves the
    length and CRC headers intact but breaks the CRC check, which is
    exactly the failure mode `read_all` raised on and the boot path
    couldn't recover from.
    '''

    size = path.stat().st_size
    with path.open('r+b') as f:
        f.seek(size - 1)
        original = f.read(1)
        f.seek(size - 1)
        f.write(bytes([original[0] ^ 0xFF]))


def test_read_all_raises_on_torn_tail(tmp_path: Path) -> None:
    '''Sanity: read_all still raises strictly so existing semantics hold.'''

    state = InstanceState.fresh(Decimal('10000'))
    wal = WriteAheadLog(_wal_path(tmp_path))
    wal.append(_entry(0, state))
    wal.append(_entry(1, state))

    _append_torn_record(_wal_path(tmp_path))

    with pytest.raises(ValueError):
        wal.read_all()


def test_read_safe_returns_valid_prefix_on_torn_tail(tmp_path: Path) -> None:

    state = InstanceState.fresh(Decimal('10000'))
    wal = WriteAheadLog(_wal_path(tmp_path))
    wal.append(_entry(0, state))
    wal.append(_entry(1, state))
    wal.append(_entry(2, state))

    _append_torn_record(_wal_path(tmp_path))

    entries = wal.read_safe()

    assert [e.sequence for e in entries] == [0, 1]


def test_read_safe_matches_read_all_on_clean_file(tmp_path: Path) -> None:

    state = InstanceState.fresh(Decimal('10000'))
    wal = WriteAheadLog(_wal_path(tmp_path))
    wal.append(_entry(0, state))
    wal.append(_entry(1, state))
    wal.append(_entry(2, state))

    safe = wal.read_safe()
    strict = wal.read_all()

    assert [e.sequence for e in safe] == [e.sequence for e in strict]


def test_read_safe_returns_empty_when_file_missing(tmp_path: Path) -> None:

    wal = WriteAheadLog(_wal_path(tmp_path))

    assert wal.read_safe() == []


def test_read_safe_returns_empty_when_only_magic_present(tmp_path: Path) -> None:

    wal_file = _wal_path(tmp_path)
    wal_file.write_bytes(b'NXWAL\x00\x01\x00')

    wal = WriteAheadLog(wal_file)

    assert wal.read_safe() == []


def test_read_safe_returns_empty_on_invalid_magic(tmp_path: Path) -> None:

    wal_file = _wal_path(tmp_path)
    wal_file.write_bytes(b'GARBAGE!')

    wal = WriteAheadLog(wal_file)

    assert wal.read_safe() == []


def test_state_store_init_succeeds_against_torn_tail(tmp_path: Path) -> None:
    '''StateStore.__init__ must boot through a torn-tail WAL.'''

    base = tmp_path / 'state'
    StateStore(base)

    state = InstanceState.fresh(Decimal('10000'))
    wal_path = base / 'wal' / 'wal.bin'
    wal = WriteAheadLog(wal_path)
    wal.append(_entry(0, state))
    wal.append(_entry(1, state))

    _append_torn_record(wal_path)

    StateStore(base)


def test_state_store_recover_runs_against_torn_tail_prefix(
    tmp_path: Path,
) -> None:
    '''Recovery must read only the valid prefix, returning the last good state.'''

    base = tmp_path / 'state'
    store = StateStore(base)

    initial = InstanceState.fresh(Decimal('10000'))
    store.append_mutation(initial)

    second = InstanceState.fresh(Decimal('20000'))
    store.append_mutation(second)

    third = InstanceState.fresh(Decimal('30000'))
    store.append_mutation(third)

    wal_path = base / 'wal' / 'wal.bin'
    _append_torn_record(wal_path)

    fresh_store = StateStore(base)
    recovered = fresh_store.recover()

    assert recovered is not None
    assert recovered.capital.capital_pool == Decimal('20000')


def test_state_store_sequence_continues_after_torn_tail(tmp_path: Path) -> None:
    '''The next `append` after a torn-tail boot picks the right sequence.

    Three records committed → final byte flipped (record 2 corrupt) →
    `read_safe` sees the prefix [0, 1] → `__init__` sets _sequence=2 →
    next append writes a fresh record at sequence 2. The original
    record 2's bytes are unrecoverable and discarded — its data is
    gone forever. What gets cleaned up is the *file*: the `append`
    path's existing `_find_valid_end` + `f.truncate(valid_end)` block
    erases the corrupt suffix bytes so the new record lands at the
    correct offset and subsequent reads do not stumble over junk
    between valid records.
    '''

    base = tmp_path / 'state'
    store = StateStore(base)
    state = InstanceState.fresh(Decimal('10000'))
    store.append_mutation(state)
    store.append_mutation(state)
    store.append_mutation(state)

    wal_path = base / 'wal' / 'wal.bin'
    _append_torn_record(wal_path)

    fresh_store = StateStore(base)
    fresh_store.append_mutation(state)

    entries = WriteAheadLog(wal_path).read_safe()
    assert [e.sequence for e in entries] == [0, 1, 2]


def test_validate_magic_passes_for_missing_file(tmp_path: Path) -> None:

    wal = WriteAheadLog(_wal_path(tmp_path))
    wal.validate_magic()


def test_validate_magic_passes_for_magic_only_file(tmp_path: Path) -> None:

    wal_file = _wal_path(tmp_path)
    wal_file.write_bytes(b'NXWAL\x00\x01\x00')

    wal = WriteAheadLog(wal_file)
    wal.validate_magic()


def test_validate_magic_passes_for_clean_wal(tmp_path: Path) -> None:

    state = InstanceState.fresh(Decimal('10000'))
    wal = WriteAheadLog(_wal_path(tmp_path))
    wal.append(_entry(0, state))

    wal.validate_magic()


def test_validate_magic_raises_on_garbage_file(tmp_path: Path) -> None:

    wal_file = _wal_path(tmp_path)
    wal_file.write_bytes(b'GARBAGE_NOT_A_WAL_FILE_AT_ALL')

    wal = WriteAheadLog(wal_file)

    with pytest.raises(ValueError, match='invalid magic'):
        wal.validate_magic()


def test_state_store_init_raises_on_invalid_magic_wal(tmp_path: Path) -> None:
    '''Boot must fail loud rather than letting the next `append` crash later.'''

    base = tmp_path / 'state'
    StateStore(base)

    wal_path = base / 'wal' / 'wal.bin'
    wal_path.write_bytes(b'GARBAGE_NOT_A_WAL_FILE_AT_ALL')

    with pytest.raises(ValueError, match='invalid magic'):
        StateStore(base)
