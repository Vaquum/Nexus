'''Verify WriteAheadLog append, read, truncate, file format, and integrity.'''

from __future__ import annotations

import struct
import zlib
from datetime import datetime
from pathlib import Path

import pytest

from nexus.infrastructure.wal import WriteAheadLog, _MAGIC, _RECORD_HEADER_FMT
from nexus.infrastructure.wal_entry import WALEntry, WALEntryType


def _make_entry(seq: int = 0) -> WALEntry:
    '''Build a WALEntry with the given sequence number.'''

    return WALEntry(
        sequence=seq,
        timestamp=datetime(2025, 6, 15, 12, 0, 0),
        entry_type=WALEntryType.STATE_MUTATION,
        payload=b'\x01\x02\x03',
    )


class TestAppendAndRead:
    '''Verify append persists entries and read_all recovers them.'''

    def test_single_entry(self, tmp_path: Path) -> None:
        '''Verify single append and read_all round-trip.'''

        wal = WriteAheadLog(tmp_path / 'test.wal')
        entry = _make_entry(0)
        wal.append(entry)

        entries = wal.read_all()
        assert len(entries) == 1
        assert entries[0].sequence == 0
        assert entries[0].timestamp == entry.timestamp
        assert entries[0].entry_type == WALEntryType.STATE_MUTATION
        assert entries[0].payload == b'\x01\x02\x03'

    def test_multiple_entries(self, tmp_path: Path) -> None:
        '''Verify multiple appends are read back in order.'''

        wal = WriteAheadLog(tmp_path / 'test.wal')
        for i in range(5):
            wal.append(_make_entry(i))

        entries = wal.read_all()
        assert len(entries) == 5
        assert [e.sequence for e in entries] == [0, 1, 2, 3, 4]

    def test_all_entry_types(self, tmp_path: Path) -> None:
        '''Verify all WALEntryType values survive round-trip.'''

        wal = WriteAheadLog(tmp_path / 'test.wal')
        for i, et in enumerate(WALEntryType):
            wal.append(
                WALEntry(
                    sequence=i,
                    timestamp=datetime.min,
                    entry_type=et,
                    payload=b'',
                )
            )

        entries = wal.read_all()
        assert [e.entry_type for e in entries] == list(WALEntryType)

    def test_empty_payload(self, tmp_path: Path) -> None:
        '''Verify zero-length payload survives round-trip.'''

        wal = WriteAheadLog(tmp_path / 'test.wal')
        wal.append(
            WALEntry(
                sequence=0,
                timestamp=datetime.min,
                entry_type=WALEntryType.SNAPSHOT,
                payload=b'',
            )
        )

        entries = wal.read_all()
        assert entries[0].payload == b''

    def test_large_payload(self, tmp_path: Path) -> None:
        '''Verify large payload survives round-trip.'''

        wal = WriteAheadLog(tmp_path / 'test.wal')
        big_payload = b'\xff' * 100_000
        wal.append(
            WALEntry(
                sequence=0,
                timestamp=datetime.min,
                entry_type=WALEntryType.SNAPSHOT,
                payload=big_payload,
            )
        )

        entries = wal.read_all()
        assert entries[0].payload == big_payload


class TestReadEmptyOrMissing:
    '''Verify read_all behavior for missing or empty files.'''

    def test_missing_file(self, tmp_path: Path) -> None:
        '''Verify read_all returns empty list when file does not exist.'''

        wal = WriteAheadLog(tmp_path / 'nonexistent.wal')
        assert wal.read_all() == []

    def test_empty_file(self, tmp_path: Path) -> None:
        '''Verify read_all returns empty list for zero-byte file.'''

        path = tmp_path / 'empty.wal'
        path.write_bytes(b'')
        wal = WriteAheadLog(path)
        assert wal.read_all() == []


class TestTruncate:
    '''Verify WAL truncation behavior.'''

    def test_truncate_clears_entries(self, tmp_path: Path) -> None:
        '''Verify truncate removes all entries.'''

        wal = WriteAheadLog(tmp_path / 'test.wal')
        wal.append(_make_entry(0))
        wal.append(_make_entry(1))
        assert len(wal.read_all()) == 2

        wal.truncate()
        assert wal.read_all() == []

    def test_truncate_missing_file_noop(self, tmp_path: Path) -> None:
        '''Verify truncate on missing file does not raise.'''

        wal = WriteAheadLog(tmp_path / 'nonexistent.wal')
        wal.truncate()

    def test_append_after_truncate(self, tmp_path: Path) -> None:
        '''Verify append works after truncation.'''

        wal = WriteAheadLog(tmp_path / 'test.wal')
        wal.append(_make_entry(0))
        wal.truncate()
        wal.append(_make_entry(10))

        entries = wal.read_all()
        assert len(entries) == 1
        assert entries[0].sequence == 10


class TestMagicHeader:
    '''Verify magic header behavior.'''

    def test_file_starts_with_magic(self, tmp_path: Path) -> None:
        '''Verify WAL file begins with the magic header bytes.'''

        path = tmp_path / 'test.wal'
        wal = WriteAheadLog(path)
        wal.append(_make_entry(0))

        raw = path.read_bytes()
        assert raw[:8] == _MAGIC

    def test_magic_written_once(self, tmp_path: Path) -> None:
        '''Verify magic header is not duplicated on subsequent appends.'''

        path = tmp_path / 'test.wal'
        wal = WriteAheadLog(path)
        wal.append(_make_entry(0))
        wal.append(_make_entry(1))

        raw = path.read_bytes()
        assert raw[:8] == _MAGIC
        assert raw.count(_MAGIC) == 1

    def test_invalid_magic_rejected(self, tmp_path: Path) -> None:
        '''Verify read_all rejects file with wrong magic header.'''

        path = tmp_path / 'bad.wal'
        path.write_bytes(b'BADMAGIC' + b'\x00' * 20)
        wal = WriteAheadLog(path)

        with pytest.raises(ValueError, match='Invalid WAL magic header'):
            wal.read_all()

    def test_magic_rewritten_after_truncate(self, tmp_path: Path) -> None:
        '''Verify magic header is rewritten after truncate + append.'''

        path = tmp_path / 'test.wal'
        wal = WriteAheadLog(path)
        wal.append(_make_entry(0))
        wal.truncate()
        wal.append(_make_entry(1))

        raw = path.read_bytes()
        assert raw[:8] == _MAGIC


class TestFileFormat:
    '''Verify binary record format integrity.'''

    def test_record_header_after_magic(self, tmp_path: Path) -> None:
        '''Verify first record starts immediately after magic header.'''

        path = tmp_path / 'test.wal'
        wal = WriteAheadLog(path)
        wal.append(_make_entry(0))

        raw = path.read_bytes()
        record_data = raw[8:]
        length, crc = struct.unpack(_RECORD_HEADER_FMT, record_data[:8])
        payload = record_data[8:]
        assert length == len(payload)
        assert crc == zlib.crc32(payload) & 0xFFFFFFFF

    def test_multiple_records_contiguous(self, tmp_path: Path) -> None:
        '''Verify multiple records are packed contiguously after magic.'''

        path = tmp_path / 'test.wal'
        wal = WriteAheadLog(path)
        wal.append(_make_entry(0))
        wal.append(_make_entry(1))

        raw = path.read_bytes()
        offset = 8
        len1, _ = struct.unpack(_RECORD_HEADER_FMT, raw[offset : offset + 8])
        offset += 8 + len1
        len2, _ = struct.unpack(_RECORD_HEADER_FMT, raw[offset : offset + 8])
        assert offset + 8 + len2 == len(raw)


class TestCorruptionDetection:
    '''Verify read_all detects corruption and truncation.'''

    def test_truncated_record_header(self, tmp_path: Path) -> None:
        '''Verify truncated record header raises ValueError.'''

        path = tmp_path / 'corrupt.wal'
        path.write_bytes(_MAGIC + b'\x00\x00')
        wal = WriteAheadLog(path)

        with pytest.raises(ValueError, match='Truncated WAL record header'):
            wal.read_all()

    def test_truncated_record_payload(self, tmp_path: Path) -> None:
        '''Verify truncated record payload raises ValueError.'''

        path = tmp_path / 'corrupt.wal'
        header = struct.pack(_RECORD_HEADER_FMT, 100, 0)
        path.write_bytes(_MAGIC + header + b'\x00' * 10)
        wal = WriteAheadLog(path)

        with pytest.raises(ValueError, match='Truncated WAL record payload'):
            wal.read_all()

    def test_crc_mismatch_detected(self, tmp_path: Path) -> None:
        '''Verify flipped bit in payload triggers CRC mismatch.'''

        path = tmp_path / 'test.wal'
        wal = WriteAheadLog(path)
        wal.append(_make_entry(0))

        raw = bytearray(path.read_bytes())
        raw[-1] ^= 0xFF
        path.write_bytes(bytes(raw))

        with pytest.raises(ValueError, match='CRC32 mismatch'):
            wal.read_all()

    def test_crc_mismatch_in_header_field(self, tmp_path: Path) -> None:
        '''Verify corrupted CRC field itself triggers mismatch.'''

        path = tmp_path / 'test.wal'
        wal = WriteAheadLog(path)
        wal.append(_make_entry(0))

        raw = bytearray(path.read_bytes())
        raw[8 + 4] ^= 0xFF
        path.write_bytes(bytes(raw))

        with pytest.raises(ValueError, match='CRC32 mismatch'):
            wal.read_all()


class TestPathProperty:
    '''Verify path accessor.'''

    def test_path_returns_configured_path(self, tmp_path: Path) -> None:
        '''Verify path property returns the path passed to constructor.'''

        p = tmp_path / 'my.wal'
        wal = WriteAheadLog(p)
        assert wal.path == p
