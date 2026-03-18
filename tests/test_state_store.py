'''Verify StateStore checkpoint, append_mutation, append_event, and recover.'''

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

from nexus.core.domain.capital_state import CapitalState
from nexus.core.domain.instance_state import InstanceState
from nexus.infrastructure.state_store import StateStore
from nexus.infrastructure.strategy_event import StrategyEvent
from nexus.infrastructure.wal import WriteAheadLog
from nexus.infrastructure.wal_codec import deserialize_event
from nexus.infrastructure.wal_entry import WALEntryType


def _make_state(pool: str = '10000') -> InstanceState:
    '''Build an InstanceState with the given capital pool.'''

    return InstanceState(capital=CapitalState(capital_pool=Decimal(pool)))


class TestDirectoryLayout:
    '''Verify StateStore creates and manages its directory.'''

    def test_creates_base_directory(self, tmp_path: Path) -> None:
        '''Verify StateStore creates the base directory if absent.'''

        base = tmp_path / 'state'
        assert not base.exists()
        StateStore(base)
        assert base.is_dir()

    def test_base_path_property(self, tmp_path: Path) -> None:
        '''Verify base_path returns the configured path.'''

        base = tmp_path / 'state'
        store = StateStore(base)
        assert store.base_path == base


class TestCheckpoint:
    '''Verify checkpoint saves snapshot and truncates WAL.'''

    def test_checkpoint_creates_snapshot(self, tmp_path: Path) -> None:
        '''Verify checkpoint creates the snapshot file.'''

        store = StateStore(tmp_path / 'state')
        store.checkpoint(_make_state())
        assert (tmp_path / 'state' / 'snapshots' / 'snapshot.bin').exists()

    def test_checkpoint_truncates_wal(self, tmp_path: Path) -> None:
        '''Verify WAL is empty after checkpoint.'''

        store = StateStore(tmp_path / 'state')
        store.append_mutation(_make_state('5000'))
        store.checkpoint(_make_state('10000'))

        restored = store.recover()
        assert restored is not None
        assert restored.capital.capital_pool == Decimal('10000')

    def test_checkpoint_overwrites_previous(self, tmp_path: Path) -> None:
        '''Verify second checkpoint replaces the first.'''

        store = StateStore(tmp_path / 'state')
        store.checkpoint(_make_state('1000'))
        store.checkpoint(_make_state('2000'))

        restored = store.recover()
        assert restored is not None
        assert restored.capital.capital_pool == Decimal('2000')


class TestAppendMutation:
    '''Verify append_mutation writes state to WAL.'''

    def test_single_mutation_recoverable(self, tmp_path: Path) -> None:
        '''Verify single mutation is recoverable without snapshot.'''

        store = StateStore(tmp_path / 'state')
        store.append_mutation(_make_state('7500'))

        store2 = StateStore(tmp_path / 'state')
        restored = store2.recover()
        assert restored is not None
        assert restored.capital.capital_pool == Decimal('7500')

    def test_multiple_mutations_last_wins(self, tmp_path: Path) -> None:
        '''Verify last mutation is the recovered state.'''

        store = StateStore(tmp_path / 'state')
        store.append_mutation(_make_state('1000'))
        store.append_mutation(_make_state('2000'))
        store.append_mutation(_make_state('3000'))

        store2 = StateStore(tmp_path / 'state')
        restored = store2.recover()
        assert restored is not None
        assert restored.capital.capital_pool == Decimal('3000')

    def test_sequence_increments(self, tmp_path: Path) -> None:
        '''Verify WAL entry sequence numbers increment.'''

        store = StateStore(tmp_path / 'state')
        store.append_mutation(_make_state('1000'))
        store.append_mutation(_make_state('2000'))

        wal = WriteAheadLog(tmp_path / 'state' / 'wal' / 'wal.bin')
        entries = wal.read_all()
        assert entries[0].sequence == 0
        assert entries[1].sequence == 1

    def test_sequence_continues_without_recover(self, tmp_path: Path) -> None:
        '''Verify new StateStore on existing WAL continues sequence.'''

        store = StateStore(tmp_path / 'state')
        store.append_mutation(_make_state('1000'))
        store.append_mutation(_make_state('2000'))

        store2 = StateStore(tmp_path / 'state')
        store2.append_mutation(_make_state('3000'))

        wal = WriteAheadLog(tmp_path / 'state' / 'wal' / 'wal.bin')
        entries = wal.read_all()
        assert entries[-1].sequence == 2


class TestRecover:
    '''Verify recover behavior across scenarios.'''

    def test_no_state_returns_none(self, tmp_path: Path) -> None:
        '''Verify recover returns None with no snapshot and no WAL.'''

        store = StateStore(tmp_path / 'state')
        assert store.recover() is None

    def test_snapshot_only(self, tmp_path: Path) -> None:
        '''Verify recover from snapshot with no WAL entries.'''

        store = StateStore(tmp_path / 'state')
        store.checkpoint(_make_state('50000'))

        store2 = StateStore(tmp_path / 'state')
        restored = store2.recover()
        assert restored is not None
        assert restored.capital.capital_pool == Decimal('50000')

    def test_snapshot_plus_wal(self, tmp_path: Path) -> None:
        '''Verify WAL entries override snapshot state.'''

        store = StateStore(tmp_path / 'state')
        store.checkpoint(_make_state('10000'))
        store.append_mutation(_make_state('15000'))
        store.append_mutation(_make_state('20000'))

        store2 = StateStore(tmp_path / 'state')
        restored = store2.recover()
        assert restored is not None
        assert restored.capital.capital_pool == Decimal('20000')

    def test_wal_only_no_snapshot(self, tmp_path: Path) -> None:
        '''Verify recover from WAL entries without a snapshot.'''

        store = StateStore(tmp_path / 'state')
        store.append_mutation(_make_state('8000'))

        store2 = StateStore(tmp_path / 'state')
        restored = store2.recover()
        assert restored is not None
        assert restored.capital.capital_pool == Decimal('8000')

    def test_sequence_resumes_after_recover(self, tmp_path: Path) -> None:
        '''Verify sequence counter resumes from WAL after recovery.'''

        store = StateStore(tmp_path / 'state')
        store.append_mutation(_make_state('1000'))
        store.append_mutation(_make_state('2000'))

        store2 = StateStore(tmp_path / 'state')
        store2.recover()
        store2.append_mutation(_make_state('3000'))

        wal = WriteAheadLog(tmp_path / 'state' / 'wal' / 'wal.bin')
        entries = wal.read_all()
        assert entries[-1].sequence == 2


class TestCheckpointRecoverCycle:
    '''Verify full checkpoint-mutate-recover cycles.'''

    def test_full_cycle(self, tmp_path: Path) -> None:
        '''Verify checkpoint, mutate, recover, checkpoint cycle.'''

        store = StateStore(tmp_path / 'state')

        store.checkpoint(_make_state('10000'))
        store.append_mutation(_make_state('12000'))
        store.append_mutation(_make_state('14000'))

        store2 = StateStore(tmp_path / 'state')
        recovered = store2.recover()
        assert recovered is not None
        assert recovered.capital.capital_pool == Decimal('14000')

        store2.checkpoint(recovered)

        store3 = StateStore(tmp_path / 'state')
        final = store3.recover()
        assert final is not None
        assert final.capital.capital_pool == Decimal('14000')


def _make_event(strategy_id: str = 'strat_a', pnl: str = '-50.25') -> StrategyEvent:
    return StrategyEvent(
        strategy_id=strategy_id,
        event_type='trade_outcome',
        realized_pnl=Decimal(pnl),
        timestamp=datetime(2026, 3, 19, 12, 0, 0),
    )


class TestAppendEvent:
    def test_event_written_to_wal(self, tmp_path: Path) -> None:
        store = StateStore(tmp_path / 'state')
        store.append_event(_make_event())

        wal = WriteAheadLog(tmp_path / 'state' / 'wal' / 'wal.bin')
        entries = wal.read_all()
        assert len(entries) == 1
        assert entries[0].entry_type == WALEntryType.STRATEGY_EVENT

    def test_event_payload_round_trips(self, tmp_path: Path) -> None:
        store = StateStore(tmp_path / 'state')
        original = _make_event(pnl='-123.456')
        store.append_event(original)

        wal = WriteAheadLog(tmp_path / 'state' / 'wal' / 'wal.bin')
        entries = wal.read_all()
        recovered = deserialize_event(entries[0].payload)
        assert recovered.strategy_id == original.strategy_id
        assert recovered.realized_pnl == original.realized_pnl

    def test_event_sequence_increments(self, tmp_path: Path) -> None:
        store = StateStore(tmp_path / 'state')
        store.append_event(_make_event())
        store.append_event(_make_event(pnl='100'))

        wal = WriteAheadLog(tmp_path / 'state' / 'wal' / 'wal.bin')
        entries = wal.read_all()
        assert entries[0].sequence == 0
        assert entries[1].sequence == 1

    def test_mixed_mutations_and_events(self, tmp_path: Path) -> None:
        store = StateStore(tmp_path / 'state')
        store.append_mutation(_make_state('1000'))
        store.append_event(_make_event())
        store.append_mutation(_make_state('2000'))

        wal = WriteAheadLog(tmp_path / 'state' / 'wal' / 'wal.bin')
        entries = wal.read_all()
        assert len(entries) == 3
        assert entries[0].entry_type == WALEntryType.STATE_MUTATION
        assert entries[1].entry_type == WALEntryType.STRATEGY_EVENT
        assert entries[2].entry_type == WALEntryType.STATE_MUTATION
        assert entries[0].sequence == 0
        assert entries[1].sequence == 1
        assert entries[2].sequence == 2
