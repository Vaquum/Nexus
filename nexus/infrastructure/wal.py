'''Write-ahead log for Manager instance state persistence.

Append-only binary log with magic header, CRC32 integrity checks,
and length-prefixed records. Each record is a msgpack-serialized
WALEntry.

File format:
    Header (written once):
        [8 bytes: magic b'NXWAL\\x00\\x01\\x00']

    Per record:
        [4-byte big-endian payload length]
        [4-byte big-endian CRC32 of payload]
        [msgpack payload bytes]
'''

from __future__ import annotations

import os
import struct
import zlib
from datetime import datetime
from pathlib import Path

import msgpack

from nexus.infrastructure.wal_entry import WALEntry, WALEntryType

__all__ = ['WriteAheadLog']

_MAGIC = b'NXWAL\x00\x01\x00'
_MAGIC_SIZE = len(_MAGIC)
_RECORD_HEADER_FMT = '>II'
_RECORD_HEADER_SIZE = struct.calcsize(_RECORD_HEADER_FMT)


class WriteAheadLog:
    '''Append-only write-ahead log backed by a binary file.

    Args:
        path: File path for the WAL. Created on first append if absent.
    '''

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        '''Return the WAL file path.'''

        return self._path

    def append(self, entry: WALEntry) -> None:
        '''Append a single entry to the WAL and fsync.

        Args:
            entry: The WAL entry to persist.
        '''

        payload = _serialize_entry(entry)
        crc = zlib.crc32(payload) & 0xFFFFFFFF
        header = struct.pack(_RECORD_HEADER_FMT, len(payload), crc)

        repair = not self._path.exists() or self._path.stat().st_size < _MAGIC_SIZE
        if not repair:
            with self._path.open('rb') as f:
                if f.read(_MAGIC_SIZE) != _MAGIC:
                    msg = (
                        f"Cannot append to WAL with invalid magic header: {self._path}"
                    )
                    raise ValueError(msg)
        with self._path.open('wb' if repair else 'ab') as f:
            if repair:
                f.write(_MAGIC)
            f.write(header)
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())

    def read_all(self) -> list[WALEntry]:
        '''Read all entries from the WAL file.

        Returns:
            List of WALEntry in append order. Empty list if file missing.
        '''

        if not self._path.exists():
            return []

        with self._path.open('rb') as f:
            file_magic = f.read(_MAGIC_SIZE)
            if not file_magic:
                return []
            if len(file_magic) < _MAGIC_SIZE:
                return []
            if file_magic != _MAGIC:
                msg = f"Invalid WAL magic header: {file_magic!r}"
                raise ValueError(msg)

            entries: list[WALEntry] = []
            while True:
                record_header = f.read(_RECORD_HEADER_SIZE)
                if not record_header:
                    break
                if len(record_header) < _RECORD_HEADER_SIZE:
                    break
                length, expected_crc = struct.unpack(_RECORD_HEADER_FMT, record_header)
                payload = f.read(length)
                if len(payload) < length:
                    break
                actual_crc = zlib.crc32(payload) & 0xFFFFFFFF
                if actual_crc != expected_crc:
                    msg = f"WAL record CRC32 mismatch: expected {expected_crc:#010x}, got {actual_crc:#010x}"
                    raise ValueError(msg)
                entries.append(_deserialize_entry(payload))
        return entries

    def truncate(self) -> None:
        '''Truncate the WAL file to zero bytes.

        Used after a snapshot has been saved. No-op if file missing.
        '''

        if self._path.exists():
            with self._path.open('wb') as f:
                f.flush()
                os.fsync(f.fileno())


def _serialize_entry(entry: WALEntry) -> bytes:
    '''Serialize a WALEntry to msgpack bytes.'''

    d = {
        'seq': entry.sequence,
        'ts': entry.timestamp.isoformat(),
        'type': entry.entry_type.value,
        'payload': entry.payload,
    }
    return bytes(msgpack.packb(d))


def _deserialize_entry(data: bytes) -> WALEntry:
    '''Deserialize msgpack bytes to a WALEntry.'''

    d = msgpack.unpackb(data, raw=False)
    if not isinstance(d, dict):
        msg = f'Expected dict from WAL entry, got {type(d).__name__}'
        raise ValueError(msg)
    try:
        return WALEntry(
            sequence=d['seq'],
            timestamp=datetime.fromisoformat(d['ts']),
            entry_type=WALEntryType(d['type']),
            payload=d['payload'],
        )
    except KeyError as exc:
        msg = f'WAL entry missing required field: {exc}'
        raise ValueError(msg) from exc
