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

import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from nexus.core.domain.instance_state import InstanceState
from nexus.infrastructure.snapshot import load_snapshot, save_snapshot
from nexus.infrastructure.wal import WriteAheadLog
from nexus.infrastructure.loss_derivation import (
    derive_rolling_losses,
    derive_strategy_realized_pnl,
)
from nexus.infrastructure.strategy_event import StrategyEvent
from nexus.infrastructure.wal_codec import (
    deserialize_event,
    deserialize_state,
    serialize_event,
    serialize_state,
)
from nexus.infrastructure.wal_entry import WALEntry, WALEntryType

__all__ = ['StateStore']

_ZERO = Decimal(0)

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
        self._wal.validate_magic()
        existing = self._wal.read_safe()
        self._sequence = existing[-1].sequence + 1 if existing else 0
        self._wal_lock = threading.Lock()

    @property
    def base_path(self) -> Path:
        '''Return the base directory path.'''

        return self._base_path

    def checkpoint(self, state: InstanceState) -> None:
        '''Save a full snapshot and truncate the WAL.

        FINAL-MAJOR-04: holds `_wal_lock` so a concurrent
        `append_mutation` / `append_event` from the OutcomeLoop or
        shutdown thread cannot interleave between the snapshot write
        and the WAL truncate, which would either persist appends that
        the truncate then drops (data loss) or truncate appends the
        snapshot did not capture.

        Args:
            state: The current instance state to persist.
        '''

        with self._wal_lock:
            save_snapshot(state, self._snapshot_path, self._wal)

    def append_mutation(self, state: InstanceState) -> None:
        '''Append a full state entry to the WAL.

        FINAL-MAJOR-04: `_sequence += 1` is a 2-bytecode RMW and the
        WAL append's `_find_valid_end + truncate + write + fsync`
        sequence is a TOCTOU on file size. Concurrent appenders from
        OutcomeLoop and the shutdown thread (round-16 TD-010) can
        produce duplicate `_sequence` records and torn appends. The
        lock makes the entire serialize + sequence-bump + file-write
        one atomic critical section. Innermost lock in the chain
        (`command_registry_lock -> positions_lock -> CapitalController._lock
        -> wal_lock`) — never holds and acquires another lock.

        Args:
            state: The current instance state after mutation.
        '''

        with self._wal_lock:
            payload = serialize_state(state)
            entry = WALEntry(
                sequence=self._sequence,
                timestamp=datetime.now(tz=timezone.utc),
                entry_type=WALEntryType.STATE_MUTATION,
                payload=payload,
            )
            self._wal.append(entry)
            self._sequence += 1

    def append_event(self, event: StrategyEvent) -> None:
        '''Append a strategy event entry to the WAL.

        FINAL-MAJOR-04: same atomicity guarantee as `append_mutation`
        — `_sequence += 1` and the WAL file-write are one critical
        section under `_wal_lock`.

        Args:
            event: The strategy event to persist.
        '''

        with self._wal_lock:
            payload = serialize_event(event)
            entry = WALEntry(
                sequence=self._sequence,
                timestamp=datetime.now(tz=timezone.utc),
                entry_type=WALEntryType.STRATEGY_EVENT,
                payload=payload,
            )
            self._wal.append(entry)
            self._sequence += 1

    def recover(self) -> InstanceState | None:
        '''Recover instance state from snapshot and WAL.

        Two-pass recovery:
        1. Load snapshot, replay STATE_MUTATION entries (last wins).
        2. Scan STRATEGY_EVENT entries, re-derive rolling loss counters.

        Returns:
            Recovered InstanceState with accurate loss counters,
            or None if no persisted state exists.
        '''

        self._wal.validate_magic()
        state = load_snapshot(self._snapshot_path)
        wal_entries = self._wal.read_safe()

        events = []

        for entry in wal_entries:
            if entry.entry_type == WALEntryType.STATE_MUTATION:
                state = deserialize_state(entry.payload)
            elif entry.entry_type == WALEntryType.STRATEGY_EVENT:
                events.append(deserialize_event(entry.payload))

        if wal_entries:
            self._sequence = wal_entries[-1].sequence + 1

        if state is None:
            return state

        # FINAL-MAJOR-10: do NOT early-return when `events` is empty.
        # Pre-fix the snapshot's `state.risk.per_strategy[sid].rolling_loss_*`
        # values were adopted verbatim — frozen at last-snapshot time,
        # not decayed against the current time. Combined with PredictLoop
        # starting BEFORE HealthLoop in the launcher (`praxis/launcher.py`
        # 1614 vs 1646), the validator could read inflated rolling losses
        # for up to ~5s after boot, denying every ENTER that should pass.
        # Post-fix every per_strategy entry is decayed against current
        # time on every recover() call, regardless of WAL event count.
        recovery_time = datetime.now(tz=timezone.utc)
        losses = derive_rolling_losses(events, recovery_time) if events else {}

        # FINAL-TD-01: also re-derive per-strategy SIGNED cumulative
        # `strategy_realized_pnl` from events. Pre-fix `recover()`
        # adopted the snapshot's `strategy_realized_pnl` verbatim;
        # on a crash between `append_event` and `append_mutation` the
        # delta from the lost STATE_MUTATION was permanently dropped
        # — drawdown gates fired LATER than they should by the missing
        # delta. The same residual is then propagated into the
        # instance-level `cumulative_realized_pnl` so drawdown
        # derivatives stay consistent.
        derived_pnl = derive_strategy_realized_pnl(events) if events else {}

        for sid, srs in state.risk.per_strategy.items():
            if sid in losses:
                srs.rolling_loss_24h = losses[sid].rolling_loss_24h
                srs.rolling_loss_7d = losses[sid].rolling_loss_7d
                srs.rolling_loss_30d = losses[sid].rolling_loss_30d
            else:
                srs.rolling_loss_24h = _ZERO
                srs.rolling_loss_7d = _ZERO
                srs.rolling_loss_30d = _ZERO

            srs.strategy_realized_pnl = derived_pnl.get(sid, _ZERO)
            srs.high_water_mark = max(
                srs.high_water_mark, srs.strategy_realized_pnl,
            )

        # Re-derive instance-level cumulative_realized_pnl as the sum
        # over per-strategy realized P&L (same identity used by
        # `state.risk.realized_pnl` property at runtime). Triggers
        # drawdown recompute under the new value.
        state.risk.update_cumulative_realized_pnl(state.risk.realized_pnl)

        return state

    def read_events(self) -> list[StrategyEvent]:
        '''Read all STRATEGY_EVENT entries from WAL.

        Returns:
            List of StrategyEvent records from WAL, in sequence order.
        '''

        self._wal.validate_magic()
        wal_entries = self._wal.read_safe()
        return [
            deserialize_event(entry.payload)
            for entry in wal_entries
            if entry.entry_type == WALEntryType.STRATEGY_EVENT
        ]

    def refresh_rolling_losses(self, state: InstanceState) -> None:
        '''Re-derive rolling loss counters from WAL events.

        Call periodically during uptime to ensure rolling loss windows
        remain accurate as old events age out of the 24h/7d/30d windows.

        FINAL-MAJOR-02: the per-strategy iteration + field assignments
        run under `state.risk.lock` so the OutcomeProcessor writer
        (`_update_strategy_risk_state`) cannot insert a fresh strategy
        key mid-iteration, which would raise
        `RuntimeError: dictionary changed size during iteration` and
        be silently swallowed by HealthLoop's catch-all.

        Args:
            state: Instance state whose rolling losses will be updated in place.
        '''

        events = self.read_events()

        recovery_time = datetime.now(tz=timezone.utc)
        losses = derive_rolling_losses(events, recovery_time) if events else {}

        with state.risk.lock_cm():
            for sid, srs in state.risk.per_strategy.items():
                if sid in losses:
                    srs.rolling_loss_24h = losses[sid].rolling_loss_24h
                    srs.rolling_loss_7d = losses[sid].rolling_loss_7d
                    srs.rolling_loss_30d = losses[sid].rolling_loss_30d
                else:
                    srs.rolling_loss_24h = _ZERO
                    srs.rolling_loss_7d = _ZERO
                    srs.rolling_loss_30d = _ZERO
