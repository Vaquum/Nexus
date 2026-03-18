'''Snapshot manager for persisting full InstanceState to disk.

Saves and loads complete InstanceState snapshots via the WAL codec.
A save triggers WAL truncation to keep the log bounded.
'''

from __future__ import annotations

import os
from pathlib import Path

from nexus.core.domain.instance_state import InstanceState
from nexus.infrastructure.wal import WriteAheadLog
from nexus.infrastructure.wal_codec import deserialize_state, serialize_state

__all__ = ['load_snapshot', 'save_snapshot']


def _fsync_directory(dir_path: Path) -> None:
    '''Fsync a directory to make rename/replace durable across power loss.'''

    fd = os.open(str(dir_path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def save_snapshot(state: InstanceState, path: Path, wal: WriteAheadLog) -> None:
    '''Serialize full InstanceState to disk and truncate the WAL.

    Writes to a temporary file then atomically renames for crash safety.

    Args:
        state: The instance state to persist.
        path: Destination file path for the snapshot.
        wal: The write-ahead log to truncate after saving.
    '''

    data = serialize_state(state)
    tmp = path.with_suffix('.tmp')
    tmp.write_bytes(data)
    with tmp.open('rb') as f:
        os.fsync(f.fileno())
    tmp.replace(path)
    _fsync_directory(path.parent)
    wal.truncate()


def load_snapshot(path: Path) -> InstanceState | None:
    '''Load an InstanceState snapshot from disk.

    Args:
        path: File path to the snapshot.

    Returns:
        Deserialized InstanceState, or None if file does not exist.
    '''

    if not path.exists():
        return None
    return deserialize_state(path.read_bytes())
