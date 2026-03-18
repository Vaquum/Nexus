'''Verify snapshot save/load round-trip and WAL truncation.'''

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

from nexus.core.domain.capital_state import CapitalState
from nexus.core.domain.enums import OperationalMode, OrderSide
from nexus.core.domain.instance_state import InstanceState
from nexus.core.domain.operational_mode import ModeState, StrategyModeState
from nexus.core.domain.position import Position
from nexus.core.domain.risk_state import RiskState, StrategyRiskState
from nexus.infrastructure.snapshot import load_snapshot, save_snapshot
from nexus.infrastructure.wal import WriteAheadLog
from nexus.infrastructure.wal_entry import WALEntry, WALEntryType


def _make_minimal_state() -> InstanceState:
    '''Build a minimal InstanceState with only capital.'''

    return InstanceState(capital=CapitalState(capital_pool=Decimal('10000')))


def _make_full_state() -> InstanceState:
    '''Build a fully populated InstanceState.'''

    return InstanceState(
        capital=CapitalState(
            capital_pool=Decimal('100000'),
            position_notional=Decimal('25000.50'),
            working_order_notional=Decimal('5000'),
            in_flight_order_notional=Decimal('1000.75'),
            fee_reserve=Decimal('200'),
            reservation_notional=Decimal('3000'),
        ),
        risk=RiskState(
            high_water_mark=Decimal('110000'),
            per_strategy={
                'momentum': StrategyRiskState(
                    strategy_id='momentum',
                    high_water_mark=Decimal('60000'),
                    rolling_loss_24h=Decimal('150.25'),
                    rolling_loss_7d=Decimal('800'),
                    rolling_loss_30d=Decimal('2500'),
                    strategy_realized_pnl=Decimal('-500.75'),
                ),
            },
        ),
        positions={
            't1': Position(
                trade_id='t1',
                strategy_id='momentum',
                symbol='BTCUSDT',
                side=OrderSide.BUY,
                size=Decimal('0.5'),
                entry_price=Decimal('50000'),
                unrealized_pnl=Decimal('1250.50'),
            ),
        },
        mode=ModeState(
            mode=OperationalMode.REDUCE_ONLY,
            trigger='risk_breach',
            transitioned_at=datetime(2025, 6, 15, 14, 30, 0),
        ),
        strategy_modes={
            'momentum': StrategyModeState(
                strategy_id='momentum',
                state=ModeState(
                    mode=OperationalMode.HALTED,
                    trigger='manual_halt',
                    transitioned_at=datetime(2025, 6, 15, 15, 0, 0),
                ),
            ),
        },
    )


class TestSaveAndLoad:
    '''Verify save_snapshot and load_snapshot round-trip.'''

    def test_minimal_state(self, tmp_path: Path) -> None:
        '''Verify minimal state survives save/load.'''

        wal = WriteAheadLog(tmp_path / 'test.wal')
        snap_path = tmp_path / 'snapshot.bin'
        original = _make_minimal_state()

        save_snapshot(original, snap_path, wal)
        restored = load_snapshot(snap_path)

        assert restored is not None
        assert restored.capital.capital_pool == original.capital.capital_pool

    def test_full_state(self, tmp_path: Path) -> None:
        '''Verify fully populated state survives save/load.'''

        wal = WriteAheadLog(tmp_path / 'test.wal')
        snap_path = tmp_path / 'snapshot.bin'
        original = _make_full_state()

        save_snapshot(original, snap_path, wal)
        restored = load_snapshot(snap_path)

        assert restored is not None
        assert restored.capital.capital_pool == original.capital.capital_pool
        assert restored.risk.high_water_mark == original.risk.high_water_mark
        assert 't1' in restored.positions
        assert restored.positions['t1'].side == OrderSide.BUY
        assert restored.mode.mode == OperationalMode.REDUCE_ONLY
        assert 'momentum' in restored.strategy_modes

    def test_snapshot_creates_file(self, tmp_path: Path) -> None:
        '''Verify save_snapshot creates the snapshot file.'''

        wal = WriteAheadLog(tmp_path / 'test.wal')
        snap_path = tmp_path / 'snapshot.bin'

        assert not snap_path.exists()
        save_snapshot(_make_minimal_state(), snap_path, wal)
        assert snap_path.exists()

    def test_snapshot_overwrites_previous(self, tmp_path: Path) -> None:
        '''Verify save_snapshot replaces an existing snapshot.'''

        wal = WriteAheadLog(tmp_path / 'test.wal')
        snap_path = tmp_path / 'snapshot.bin'

        save_snapshot(
            InstanceState(capital=CapitalState(capital_pool=Decimal('1000'))),
            snap_path,
            wal,
        )
        save_snapshot(
            InstanceState(capital=CapitalState(capital_pool=Decimal('2000'))),
            snap_path,
            wal,
        )

        restored = load_snapshot(snap_path)
        assert restored is not None
        assert restored.capital.capital_pool == Decimal('2000')


class TestLoadMissing:
    '''Verify load_snapshot behavior for missing files.'''

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        '''Verify load_snapshot returns None when file does not exist.'''

        assert load_snapshot(tmp_path / 'nonexistent.bin') is None


class TestWalTruncation:
    '''Verify save_snapshot truncates the WAL.'''

    def test_wal_truncated_after_save(self, tmp_path: Path) -> None:
        '''Verify WAL is empty after save_snapshot.'''

        wal = WriteAheadLog(tmp_path / 'test.wal')
        wal.append(
            WALEntry(
                sequence=0,
                timestamp=datetime(2025, 1, 1),
                entry_type=WALEntryType.STATE_MUTATION,
                payload=b'\x00',
            )
        )
        wal.append(
            WALEntry(
                sequence=1,
                timestamp=datetime(2025, 1, 1),
                entry_type=WALEntryType.STATE_MUTATION,
                payload=b'\x01',
            )
        )
        assert len(wal.read_all()) == 2

        save_snapshot(_make_minimal_state(), tmp_path / 'snapshot.bin', wal)
        assert wal.read_all() == []

    def test_wal_appendable_after_truncation(self, tmp_path: Path) -> None:
        '''Verify WAL accepts new entries after snapshot truncation.'''

        wal = WriteAheadLog(tmp_path / 'test.wal')
        wal.append(
            WALEntry(
                sequence=0,
                timestamp=datetime(2025, 1, 1),
                entry_type=WALEntryType.STATE_MUTATION,
                payload=b'\x00',
            )
        )

        save_snapshot(_make_minimal_state(), tmp_path / 'snapshot.bin', wal)
        wal.append(
            WALEntry(
                sequence=1,
                timestamp=datetime(2025, 1, 2),
                entry_type=WALEntryType.STATE_MUTATION,
                payload=b'\x01',
            )
        )

        entries = wal.read_all()
        assert len(entries) == 1
        assert entries[0].sequence == 1


class TestAtomicWrite:
    '''Verify snapshot write does not leave temp files.'''

    def test_no_tmp_file_after_save(self, tmp_path: Path) -> None:
        '''Verify .tmp file is cleaned up after save.'''

        wal = WriteAheadLog(tmp_path / 'test.wal')
        snap_path = tmp_path / 'snapshot.bin'
        save_snapshot(_make_minimal_state(), snap_path, wal)

        tmp_file = snap_path.with_suffix('.tmp')
        assert not tmp_file.exists()
        assert snap_path.exists()
