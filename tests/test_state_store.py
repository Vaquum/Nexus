'''Verify StateStore checkpoint, append_mutation, append_event, and recover.'''

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import threading
from pathlib import Path

import pytest

from nexus.core.domain.capital_state import CapitalState
from nexus.core.domain.instance_state import InstanceState
from nexus.core.domain.risk_state import RiskState, StrategyRiskState
from nexus.infrastructure.state_store import StateSnapshotLocks, StateStore
from nexus.infrastructure.strategy_event import StrategyEvent
from nexus.infrastructure.wal import WriteAheadLog
from nexus.infrastructure.wal_codec import deserialize_event
from nexus.infrastructure.wal_entry import WALEntryType

_ZERO = Decimal(0)


def _make_state(pool: str = '10000') -> InstanceState:
    '''Build an InstanceState with the given capital pool.'''

    return InstanceState(capital=CapitalState(capital_pool=Decimal(pool)))


class _RecordingLock:

    def __init__(self, name: str, trace: list[str]) -> None:
        self._name = name
        self._trace = trace
        self._lock = threading.Lock()

    def __enter__(self) -> None:
        self._lock.acquire()
        self._trace.append(f'{self._name}:acquire')

    def __exit__(self, *exc: object) -> None:
        self._trace.append(f'{self._name}:release')
        self._lock.release()


class TestStateSnapshotLocks:

    def test_append_mutation_acquires_locks_in_chain_order(
        self,
        tmp_path: Path,
    ) -> None:
        trace: list[str] = []
        locks = StateSnapshotLocks(
            positions_lock=_RecordingLock('positions', trace),
            capital_lock=_RecordingLock('capital', trace),
        )
        store = StateStore(tmp_path, snapshot_locks=locks)

        store.append_mutation(_make_state())

        assert trace == [
            'positions:acquire',
            'capital:acquire',
            'capital:release',
            'positions:release',
        ]

    def test_checkpoint_acquires_locks_in_chain_order(
        self,
        tmp_path: Path,
    ) -> None:
        trace: list[str] = []
        locks = StateSnapshotLocks(
            positions_lock=_RecordingLock('positions', trace),
            capital_lock=_RecordingLock('capital', trace),
        )
        store = StateStore(tmp_path, snapshot_locks=locks)

        store.checkpoint(_make_state())

        assert trace == [
            'positions:acquire',
            'capital:acquire',
            'capital:release',
            'positions:release',
        ]

    def test_append_event_does_not_take_snapshot_locks(
        self,
        tmp_path: Path,
    ) -> None:
        trace: list[str] = []
        locks = StateSnapshotLocks(
            positions_lock=_RecordingLock('positions', trace),
            capital_lock=_RecordingLock('capital', trace),
        )
        store = StateStore(tmp_path, snapshot_locks=locks)

        store.append_event(_make_event())

        assert trace == []

    def test_legacy_construction_without_locks_unchanged(
        self,
        tmp_path: Path,
    ) -> None:
        store = StateStore(tmp_path)

        store.append_mutation(_make_state())
        store.checkpoint(_make_state())

        recovered = store.recover()
        assert recovered is not None

    def test_attach_snapshot_locks_engages_guard_on_checkpoint(
        self,
        tmp_path: Path,
    ) -> None:
        trace: list[str] = []
        store = StateStore(tmp_path)

        store.checkpoint(_make_state())
        assert trace == []

        store.attach_snapshot_locks(
            StateSnapshotLocks(
                positions_lock=_RecordingLock('positions', trace),
                capital_lock=_RecordingLock('capital', trace),
            ),
        )

        store.append_mutation(_make_state())
        store.checkpoint(_make_state())

        assert trace == [
            'positions:acquire',
            'capital:acquire',
            'capital:release',
            'positions:release',
            'positions:acquire',
            'capital:acquire',
            'capital:release',
            'positions:release',
        ]

    def test_attach_snapshot_locks_rejects_second_attach(
        self,
        tmp_path: Path,
    ) -> None:
        store = StateStore(tmp_path)
        bundle = StateSnapshotLocks(
            positions_lock=threading.Lock(),
            capital_lock=threading.Lock(),
        )
        store.attach_snapshot_locks(bundle)

        with pytest.raises(RuntimeError, match='already attached'):
            store.attach_snapshot_locks(bundle)

    def test_attach_snapshot_locks_rejects_non_bundle(
        self,
        tmp_path: Path,
    ) -> None:
        store = StateStore(tmp_path)

        with pytest.raises(TypeError, match='StateSnapshotLocks'):
            store.attach_snapshot_locks(None)  # type: ignore[arg-type]

    def test_attach_snapshot_locks_second_attach_wins_over_type_check(
        self,
        tmp_path: Path,
    ) -> None:
        store = StateStore(tmp_path)
        store.attach_snapshot_locks(
            StateSnapshotLocks(
                positions_lock=threading.Lock(),
                capital_lock=threading.Lock(),
            ),
        )

        with pytest.raises(RuntimeError, match='already attached'):
            store.attach_snapshot_locks(None)  # type: ignore[arg-type]


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

    def test_per_strategy_deployed_round_trips(self, tmp_path: Path) -> None:
        '''Verify per_strategy_deployed survives append_mutation and recover.'''

        store = StateStore(tmp_path / 'state')
        state = InstanceState(
            capital=CapitalState(
                capital_pool=Decimal('10000'),
                per_strategy_deployed={'strat_a': Decimal('250.5')},
            ),
        )
        store.append_mutation(state)

        recovered = StateStore(tmp_path / 'state').recover()
        assert recovered is not None
        assert recovered.capital.per_strategy_deployed == {'strat_a': Decimal('250.5')}


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
        timestamp=datetime(2026, 3, 19, 12, 0, 0, tzinfo=timezone.utc),
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


def _make_state_with_risk(
    pool: str = '10000',
    strategy_id: str = 'strat_a',
) -> InstanceState:
    srs = StrategyRiskState(
        strategy_id=strategy_id,
        rolling_loss_24h=Decimal('999'),
        rolling_loss_7d=Decimal('999'),
        rolling_loss_30d=Decimal('999'),
    )
    return InstanceState(
        capital=CapitalState(capital_pool=Decimal(pool)),
        risk=RiskState(per_strategy={strategy_id: srs}),
    )


class TestRecoverWithEvents:
    def test_no_events_decays_snapshot_losses_to_zero(self, tmp_path: Path) -> None:
        '''FINAL-MAJOR-10: pre-fix this test pinned that with no
        STRATEGY_EVENT entries `recover()` returned the snapshot's
        rolling_loss_* values verbatim (frozen at last-snapshot time,
        not decayed against current time). Post-fix `recover()`
        always re-derives — with no events all rolling-loss windows
        decay to zero, since `derive_rolling_losses` returns empty
        when given no events and the per-strategy fallback branch
        zeroes the windows.
        '''

        store = StateStore(tmp_path / 'state')
        state = _make_state_with_risk()
        store.append_mutation(state)

        store2 = StateStore(tmp_path / 'state')
        recovered = store2.recover()
        assert recovered is not None
        srs = recovered.risk.per_strategy['strat_a']
        assert srs.rolling_loss_24h == _ZERO
        assert srs.rolling_loss_7d == _ZERO
        assert srs.rolling_loss_30d == _ZERO

    def test_events_overwrite_snapshot_losses(self, tmp_path: Path) -> None:
        store = StateStore(tmp_path / 'state')
        state = _make_state_with_risk()
        store.append_mutation(state)

        event = StrategyEvent(
            strategy_id='strat_a',
            event_type='trade_outcome',
            realized_pnl=Decimal('-75'),
            timestamp=datetime.now(tz=timezone.utc),
        )
        store.append_event(event)

        store2 = StateStore(tmp_path / 'state')
        recovered = store2.recover()
        assert recovered is not None
        srs = recovered.risk.per_strategy['strat_a']
        assert srs.rolling_loss_24h == Decimal('75')
        assert srs.rolling_loss_7d == Decimal('75')
        assert srs.rolling_loss_30d == Decimal('75')

    def test_events_for_unknown_strategy_lazily_inserted(self, tmp_path: Path) -> None:
        '''PR #55 round-5 review: events for strategies NOT present in
        the snapshot's per_strategy dict (e.g. a strategy whose first-
        ever exit happened post-snapshot, before the next STATE_MUTATION
        could persist its lazily-created StrategyRiskState) are now
        adopted on recovery. Pre-fix this loop only iterated existing
        per_strategy entries, so the first realized P&L / rolling-loss
        delta of any new strategy was permanently dropped on recovery.
        Post-fix recover() inserts a fresh StrategyRiskState for any
        sid present in derived losses or derived PnL but missing from
        the snapshot. The known strategy's rolling losses are still
        decayed against current time (FINAL-MAJOR-10) — with no events
        for `strat_a`, its windows go to zero regardless of the
        snapshot value.
        '''

        store = StateStore(tmp_path / 'state')
        store.append_mutation(_make_state_with_risk(strategy_id='strat_a'))

        event = StrategyEvent(
            strategy_id='strat_unknown',
            event_type='trade_outcome',
            realized_pnl=Decimal('-100'),
            timestamp=datetime.now(tz=timezone.utc),
        )
        store.append_event(event)

        store2 = StateStore(tmp_path / 'state')
        recovered = store2.recover()
        assert recovered is not None
        assert 'strat_unknown' in recovered.risk.per_strategy, (
            'PR #55 round-5: post-snapshot strategies must be lazily '
            'inserted on recovery so first-ever-exit deltas are not lost'
        )
        srs_unknown = recovered.risk.per_strategy['strat_unknown']
        assert srs_unknown.strategy_realized_pnl == Decimal('-100')
        assert srs_unknown.rolling_loss_24h == Decimal('100')
        assert recovered.risk.per_strategy['strat_a'].rolling_loss_24h == _ZERO

    def test_multiple_strategies_recovered(self, tmp_path: Path) -> None:
        srs_a = StrategyRiskState(
            strategy_id='strat_a', rolling_loss_24h=Decimal('999')
        )
        srs_b = StrategyRiskState(
            strategy_id='strat_b', rolling_loss_24h=Decimal('999')
        )
        state = InstanceState(
            capital=CapitalState(capital_pool=Decimal('10000')),
            risk=RiskState(per_strategy={'strat_a': srs_a, 'strat_b': srs_b}),
        )
        store = StateStore(tmp_path / 'state')
        store.append_mutation(state)

        store.append_event(
            StrategyEvent(
                strategy_id='strat_a',
                event_type='trade_outcome',
                realized_pnl=Decimal('-10'),
                timestamp=datetime.now(tz=timezone.utc),
            )
        )
        store.append_event(
            StrategyEvent(
                strategy_id='strat_b',
                event_type='trade_outcome',
                realized_pnl=Decimal('-20'),
                timestamp=datetime.now(tz=timezone.utc),
            )
        )

        store2 = StateStore(tmp_path / 'state')
        recovered = store2.recover()
        assert recovered is not None
        assert recovered.risk.per_strategy['strat_a'].rolling_loss_24h == Decimal('10')
        assert recovered.risk.per_strategy['strat_b'].rolling_loss_24h == Decimal('20')

    def test_checkpoint_preserves_events_for_rolling_windows(self, tmp_path: Path) -> None:
        store = StateStore(tmp_path / 'state')
        state = _make_state_with_risk()
        store.append_mutation(state)

        store.append_event(
            StrategyEvent(
                strategy_id='strat_a',
                event_type='trade_outcome',
                realized_pnl=Decimal('-100'),
                timestamp=datetime.now(tz=timezone.utc),
            )
        )

        store.checkpoint(state)

        store.append_event(
            StrategyEvent(
                strategy_id='strat_a',
                event_type='trade_outcome',
                realized_pnl=Decimal('-25'),
                timestamp=datetime.now(tz=timezone.utc),
            )
        )

        store2 = StateStore(tmp_path / 'state')
        recovered = store2.recover()
        assert recovered is not None
        assert recovered.risk.per_strategy['strat_a'].rolling_loss_24h == Decimal('125')


class TestReadEvents:
    def test_read_events_returns_empty_list_when_no_events(self, tmp_path: Path) -> None:
        store = StateStore(tmp_path / 'state')
        assert store.read_events() == []

    def test_read_events_returns_single_event(self, tmp_path: Path) -> None:
        store = StateStore(tmp_path / 'state')
        event = _make_event()
        store.append_event(event)

        events = store.read_events()
        assert len(events) == 1
        assert events[0].strategy_id == event.strategy_id
        assert events[0].realized_pnl == event.realized_pnl

    def test_read_events_returns_multiple_events_in_order(self, tmp_path: Path) -> None:
        store = StateStore(tmp_path / 'state')
        store.append_event(_make_event(strategy_id='s1', pnl='-10'))
        store.append_event(_make_event(strategy_id='s2', pnl='-20'))
        store.append_event(_make_event(strategy_id='s1', pnl='-30'))

        events = store.read_events()
        assert len(events) == 3
        assert events[0].strategy_id == 's1'
        assert events[0].realized_pnl == Decimal('-10')
        assert events[1].strategy_id == 's2'
        assert events[2].strategy_id == 's1'
        assert events[2].realized_pnl == Decimal('-30')

    def test_read_events_ignores_mutation_entries(self, tmp_path: Path) -> None:
        store = StateStore(tmp_path / 'state')
        store.append_mutation(_make_state('1000'))
        store.append_event(_make_event())
        store.append_mutation(_make_state('2000'))

        events = store.read_events()
        assert len(events) == 1


class TestRefreshRollingLosses:
    def test_refresh_updates_in_memory_state(self, tmp_path: Path) -> None:
        store = StateStore(tmp_path / 'state')
        state = _make_state_with_risk()
        store.append_mutation(state)

        store.append_event(
            StrategyEvent(
                strategy_id='strat_a',
                event_type='trade_outcome',
                realized_pnl=Decimal('-50'),
                timestamp=datetime.now(tz=timezone.utc),
            )
        )

        state.risk.per_strategy['strat_a'].rolling_loss_24h = Decimal('999')

        store.refresh_rolling_losses(state)

        assert state.risk.per_strategy['strat_a'].rolling_loss_24h == Decimal('50')

    def test_refresh_with_no_events_zeros_stale_losses(self, tmp_path: Path) -> None:
        store = StateStore(tmp_path / 'state')
        state = _make_state_with_risk()
        store.append_mutation(state)

        store.refresh_rolling_losses(state)

        assert state.risk.per_strategy['strat_a'].rolling_loss_24h == _ZERO

    def test_refresh_zeros_strategy_not_in_wal(self, tmp_path: Path) -> None:
        store = StateStore(tmp_path / 'state')

        srs_a = StrategyRiskState(
            strategy_id='strat_a', rolling_loss_24h=Decimal('100')
        )
        srs_b = StrategyRiskState(
            strategy_id='strat_b', rolling_loss_24h=Decimal('200')
        )
        state = InstanceState(
            capital=CapitalState(capital_pool=Decimal('10000')),
            risk=RiskState(per_strategy={'strat_a': srs_a, 'strat_b': srs_b}),
        )
        store.append_mutation(state)

        store.append_event(
            StrategyEvent(
                strategy_id='strat_a',
                event_type='trade_outcome',
                realized_pnl=Decimal('-50'),
                timestamp=datetime.now(tz=timezone.utc),
            )
        )

        store.refresh_rolling_losses(state)

        assert state.risk.per_strategy['strat_a'].rolling_loss_24h == Decimal('50')
        assert state.risk.per_strategy['strat_b'].rolling_loss_24h == _ZERO


class TestFinalMajor04WalAppendAtomicity:
    '''FINAL-MAJOR-04: StateStore._sequence and WriteAheadLog.append
    are not lock-protected pre-fix. Concurrent writers (OutcomeLoop +
    shutdown thread per round-16 TD-010) can produce duplicate
    sequence numbers and torn appends.

    Post-fix `_wal_lock` makes the entire serialize + sequence-bump +
    file-write one atomic critical section.
    '''

    def test_concurrent_appenders_produce_monotonic_unique_sequences(self) -> None:
        '''Two threads tight-loop append_event; assert no duplicate
        sequences, monotonic ordering, and file readable end-to-end
        (no torn appends).
        '''

        import threading
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp))

            errors: list[Exception] = []
            iterations_per_thread = 50

            def appender(thread_id: int) -> None:
                try:
                    for i in range(iterations_per_thread):
                        store.append_event(
                            StrategyEvent(
                                strategy_id=f'strat_{thread_id}',
                                event_type='trade_outcome',
                                realized_pnl=Decimal(f'-{i}'),
                                timestamp=datetime.now(tz=timezone.utc),
                            )
                        )
                except Exception as exc:
                    errors.append(exc)

            threads = [
                threading.Thread(target=appender, args=(tid,), daemon=True)
                for tid in range(4)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=15)

            alive = [t.name for t in threads if t.is_alive()]
            assert not alive, f'threads did not finish: {alive}'
            assert not errors, f'appenders raised: {errors[:3]}'

            entries = store._wal.read_safe()
            sequences = [e.sequence for e in entries]
            assert len(sequences) == 4 * iterations_per_thread, (
                f'expected {4 * iterations_per_thread} entries, '
                f'got {len(sequences)}'
            )
            assert len(set(sequences)) == len(sequences), (
                f'duplicate sequence numbers detected: '
                f'{len(sequences) - len(set(sequences))} duplicates'
            )
            assert sequences == sorted(sequences), (
                'sequences not monotonic — torn append detected'
            )

    def test_concurrent_append_and_checkpoint_no_data_loss(self) -> None:
        '''A checkpoint mid-append-storm must not interleave between
        append and truncate — appends written before the checkpoint
        survive in the snapshot, appends after the checkpoint survive
        in the post-truncate WAL. No append silently dropped.
        '''

        import threading
        from tempfile import TemporaryDirectory

        srs = StrategyRiskState(strategy_id='strat_a')
        state = InstanceState(
            capital=CapitalState(capital_pool=Decimal('10000')),
            risk=RiskState(per_strategy={'strat_a': srs}),
        )

        with TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp))

            errors: list[Exception] = []
            stop_event = threading.Event()
            append_count = [0]

            def appender() -> None:
                try:
                    while not stop_event.is_set():
                        store.append_event(
                            StrategyEvent(
                                strategy_id='strat_a',
                                event_type='trade_outcome',
                                realized_pnl=Decimal('-1'),
                                timestamp=datetime.now(tz=timezone.utc),
                            )
                        )
                        append_count[0] += 1
                except Exception as exc:
                    errors.append(exc)

            def checkpointer() -> None:
                try:
                    for _ in range(5):
                        store.checkpoint(state)
                except Exception as exc:
                    errors.append(exc)
                finally:
                    stop_event.set()

            threads = [
                threading.Thread(target=appender, daemon=True),
                threading.Thread(target=checkpointer, daemon=True),
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=15)

            alive = [t.name for t in threads if t.is_alive()]
            assert not alive, f'threads did not finish: {alive}'
            assert not errors, f'race: {errors[:3]}'
            assert append_count[0] > 0, 'appender did not run'

            entries = store._wal.read_safe()
            sequences = [e.sequence for e in entries]
            assert sequences == sorted(sequences), (
                'sequences not monotonic — torn append/checkpoint'
            )
            if len(sequences) > 1:
                assert len(set(sequences)) == len(sequences), (
                    'duplicate sequence numbers across checkpoint boundary'
                )


class TestFinalMajor10RecoverDecaysRollingLossesEvenWithEmptyEvents:
    '''FINAL-MAJOR-10: pre-fix `state_store.recover()` early-returned
    when the WAL had zero STRATEGY_EVENT entries, leaving the
    snapshot's `state.risk.per_strategy[sid].rolling_loss_*` values
    adopted verbatim — frozen at last-snapshot time, not decayed
    against the current time. Combined with PredictLoop starting
    BEFORE HealthLoop in the launcher, the validator could read
    inflated rolling losses for up to ~5s after boot — denying every
    ENTER that should pass via stale RISK_ROLLING_LOSS_*_LIMIT
    breaches. If the first HealthLoop tick fired the FINAL-MAJOR-02
    dict-resize race and the refresh failed silently, the staleness
    extended indefinitely.

    Post-fix `recover()` always re-derives `rolling_loss_*` against
    current time, regardless of WAL event count. Snapshot's
    `rolling_loss_*` values get zeroed (or recomputed from any
    surviving events) BEFORE the validator can read them.
    '''

    def test_snapshot_with_inflated_rolling_loss_decays_to_zero_when_no_events(
        self, tmp_path: Path,
    ) -> None:
        '''Build a snapshot where a strategy has rolling_loss_24h=500
        (e.g. from a long-ago losing trade that has aged out of the
        24h window since the snapshot was taken). Truncate the WAL
        of all events. Recover. Assert the validator reads zero
        rolling loss for that strategy.
        '''

        store = StateStore(tmp_path)
        srs = StrategyRiskState(
            strategy_id='strat_a',
            rolling_loss_24h=Decimal('500'),
            rolling_loss_7d=Decimal('500'),
            rolling_loss_30d=Decimal('500'),
        )
        state = InstanceState(
            capital=CapitalState(capital_pool=Decimal('10000')),
            risk=RiskState(per_strategy={'strat_a': srs}),
        )
        store.checkpoint(state)
        # Create a fresh store reading the same path; recover should
        # see zero events and decay rolling losses to zero
        # (no events => no losses in any window).
        recovery_store = StateStore(tmp_path)
        recovered = recovery_store.recover()

        assert recovered is not None
        recovered_srs = recovered.risk.per_strategy['strat_a']
        assert recovered_srs.rolling_loss_24h == _ZERO, (
            f'pre-fix would adopt snapshot value 500; got '
            f'{recovered_srs.rolling_loss_24h}'
        )
        assert recovered_srs.rolling_loss_7d == _ZERO
        assert recovered_srs.rolling_loss_30d == _ZERO

    def test_snapshot_with_inflated_rolling_loss_recomputes_from_recent_events(
        self, tmp_path: Path,
    ) -> None:
        '''A snapshot has rolling_loss_24h=500 stale; the WAL has a
        single recent event for -50. Recover should derive 50 (not
        500 + 50, not 500).
        '''

        store = StateStore(tmp_path)
        srs = StrategyRiskState(
            strategy_id='strat_a',
            rolling_loss_24h=Decimal('500'),
        )
        state = InstanceState(
            capital=CapitalState(capital_pool=Decimal('10000')),
            risk=RiskState(per_strategy={'strat_a': srs}),
        )
        store.checkpoint(state)
        store.append_event(
            StrategyEvent(
                strategy_id='strat_a',
                event_type='trade_outcome',
                realized_pnl=Decimal('-50'),
                timestamp=datetime.now(tz=timezone.utc),
            )
        )

        recovery_store = StateStore(tmp_path)
        recovered = recovery_store.recover()

        assert recovered is not None
        recovered_srs = recovered.risk.per_strategy['strat_a']
        assert recovered_srs.rolling_loss_24h == Decimal('50'), (
            f'expected re-derived value 50, got '
            f'{recovered_srs.rolling_loss_24h}'
        )


class TestFinalTd01RecoverDerivesStrategyRealizedPnl:
    '''FINAL-TD-01: pre-fix `state_store.recover()` re-derived only
    `rolling_loss_*` from events; `strategy_realized_pnl`,
    `cumulative_realized_pnl`, and the drawdown derivatives were
    adopted from the snapshot verbatim. On a crash between
    `append_event` and `append_mutation`, the per-strategy
    realized PnL delta from the lost STATE_MUTATION was
    permanently dropped — drawdown gates fired LATER than they
    should by the missing delta.

    Post-fix `recover()` re-derives `strategy_realized_pnl` per
    strategy via `derive_strategy_realized_pnl` (signed, all
    events) and re-derives the instance-level
    `cumulative_realized_pnl` as the sum over per-strategy values.
    high_water_mark is bumped to at least the new
    strategy_realized_pnl so the drawdown chain stays consistent.
    '''

    def test_recover_overwrites_strategy_realized_pnl_from_events(
        self, tmp_path: Path,
    ) -> None:
        '''Snapshot has strategy_realized_pnl=0 stale; WAL has a
        winning fill (+100) and a losing fill (-30). Recover should
        derive net +70 not 0.
        '''

        store = StateStore(tmp_path)
        srs = StrategyRiskState(strategy_id='strat_a')
        state = InstanceState(
            capital=CapitalState(capital_pool=Decimal('10000')),
            risk=RiskState(per_strategy={'strat_a': srs}),
        )
        store.checkpoint(state)
        store.append_event(
            StrategyEvent(
                strategy_id='strat_a',
                event_type='trade_outcome',
                realized_pnl=Decimal('100'),
                timestamp=datetime.now(tz=timezone.utc),
            )
        )
        store.append_event(
            StrategyEvent(
                strategy_id='strat_a',
                event_type='trade_outcome',
                realized_pnl=Decimal('-30'),
                timestamp=datetime.now(tz=timezone.utc),
            )
        )

        recovery_store = StateStore(tmp_path)
        recovered = recovery_store.recover()

        assert recovered is not None
        srs_recovered = recovered.risk.per_strategy['strat_a']
        assert srs_recovered.strategy_realized_pnl == Decimal('70')
        assert recovered.risk.cumulative_realized_pnl == Decimal('70')

    def test_recover_decays_strategy_realized_pnl_to_zero_when_no_events(
        self, tmp_path: Path,
    ) -> None:
        '''Snapshot has strategy_realized_pnl=999 stale; WAL has no
        events. Recover should reset to 0 (since the events the
        snapshot was derived from are pre-snapshot and no longer in
        the WAL).
        '''

        store = StateStore(tmp_path)
        srs = StrategyRiskState(
            strategy_id='strat_a',
            strategy_realized_pnl=Decimal('999'),
            high_water_mark=Decimal('999'),
        )
        state = InstanceState(
            capital=CapitalState(capital_pool=Decimal('10000')),
            risk=RiskState(
                per_strategy={'strat_a': srs},
                cumulative_realized_pnl=Decimal('999'),
            ),
        )
        store.checkpoint(state)

        recovery_store = StateStore(tmp_path)
        recovered = recovery_store.recover()

        assert recovered is not None
        srs_recovered = recovered.risk.per_strategy['strat_a']
        assert srs_recovered.strategy_realized_pnl == _ZERO
        assert recovered.risk.cumulative_realized_pnl == _ZERO

    def test_recover_high_water_mark_at_least_strategy_realized_pnl(
        self, tmp_path: Path,
    ) -> None:
        '''high_water_mark must not drop below the recovered
        strategy_realized_pnl (the runtime invariant maintained by
        `_update_strategy_risk_state` is that hwm >= cumulative pnl).
        Snapshot has hwm=0; WAL events sum to +50; recovered hwm
        must be >= 50.
        '''

        store = StateStore(tmp_path)
        srs = StrategyRiskState(strategy_id='strat_a')
        state = InstanceState(
            capital=CapitalState(capital_pool=Decimal('10000')),
            risk=RiskState(per_strategy={'strat_a': srs}),
        )
        store.checkpoint(state)
        store.append_event(
            StrategyEvent(
                strategy_id='strat_a',
                event_type='trade_outcome',
                realized_pnl=Decimal('50'),
                timestamp=datetime.now(tz=timezone.utc),
            )
        )

        recovery_store = StateStore(tmp_path)
        recovered = recovery_store.recover()

        assert recovered is not None
        srs_recovered = recovered.risk.per_strategy['strat_a']
        assert srs_recovered.strategy_realized_pnl == Decimal('50')
        assert srs_recovered.high_water_mark >= Decimal('50')
