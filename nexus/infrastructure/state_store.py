'''Unified persistence facade combining WAL and snapshots.

StateStore manages the directory layout under a base path,
coordinates snapshot saves with WAL truncation, and provides
crash recovery via snapshot + WAL replay.

Directory layout:
    {base_path}/
        snapshots/
            snapshot.bin   — latest full InstanceState
        wal/
            wal.bin        — write-ahead log since last snapshot
'''

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from nexus.core.domain.instance_state import InstanceState
from nexus.infrastructure.snapshot import load_snapshot, save_snapshot
from nexus.infrastructure.wal import WriteAheadLog
from nexus.infrastructure.wal_codec import deserialize_state, serialize_state
from nexus.infrastructure.wal_entry import WALEntry, WALEntryType

__all__ = ['StateStore']

_SNAPSHOTS_DIR = 'snapshots'
_WAL_DIR = 'wal'
_SNAPSHOT_FILENAME = 'snapshot.bin'
_WAL_FILENAME = 'wal.bin'


class StateStore:
    '''Unified persistence facade for Manager instance state.

    Args:
        base_path: Directory for snapshot and WAL files. Created if absent.
    '''

    def __init__(self, base_path: Path) -> None:
        self._base_path = base_path
        snap_dir = self._base_path / _SNAPSHOTS_DIR
        wal_dir = self._base_path / _WAL_DIR
        snap_dir.mkdir(parents=True, exist_ok=True)
        wal_dir.mkdir(parents=True, exist_ok=True)
        self._wal = WriteAheadLog(wal_dir / _WAL_FILENAME)
        self._snapshot_path = snap_dir / _SNAPSHOT_FILENAME
        existing = self._wal.read_all()
        self._sequence = existing[-1].sequence + 1 if existing else 0

    @property
    def base_path(self) -> Path:
        '''Return the base directory path.'''

        return self._base_path

    def checkpoint(self, state: InstanceState) -> None:
        '''Save a full snapshot and truncate the WAL.

        Args:
            state: The current instance state to persist.
        '''

        save_snapshot(state, self._snapshot_path, self._wal)

    def append_mutation(self, state: InstanceState) -> None:
        '''Append a full state entry to the WAL.

        Args:
            state: The current instance state after mutation.
        '''

        payload = serialize_state(state)
        entry = WALEntry(
            sequence=self._sequence,
            timestamp=datetime.now(tz=timezone.utc),
            entry_type=WALEntryType.STATE_MUTATION,
            payload=payload,
        )
        self._wal.append(entry)
        self._sequence += 1

    def recover(self) -> InstanceState | None:
        '''Recover instance state from snapshot and WAL.

        Loads the snapshot, then replays WAL entries to reach the
        latest persisted state. WAL STATE_MUTATION entries contain
        full serialized state; the last entry wins.

        Returns:
            Recovered InstanceState, or None if no persisted state exists.
        '''

        state = load_snapshot(self._snapshot_path)
        wal_entries = self._wal.read_all()

        for entry in wal_entries:
            if entry.entry_type == WALEntryType.STATE_MUTATION:
                state = deserialize_state(entry.payload)

        if wal_entries:
            self._sequence = wal_entries[-1].sequence + 1

        return state
