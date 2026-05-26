'''Startup sequencer for Manager instance initialization.'''

from __future__ import annotations

import hashlib
import os
import pickle
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import structlog

from limen.experiment.trainer.trainer import Trainer

from nexus.core.domain.enums import OperationalMode, OrderSide
from nexus.core.domain.instance_state import InstanceState
from nexus.core.domain.operational_mode import ModeState
from nexus.core.domain.position import Position
from nexus.core.health_evaluator import HealthEvaluator, HealthSnapshot
from nexus.infrastructure.praxis_connector.praxis_outbound import PraxisOutbound
from nexus.infrastructure.manifest import Manifest, TimerSpec, load_manifest
from nexus.infrastructure.state_store import StateStore
from nexus.startup.error import StartupError
from nexus.startup.sensor_cache import (
    CACHE_DIR_ENV,
    bundle_id_for,
    cache_path_for,
    default_max_workers,
    reconstruct_sensor,
    write_sensor_atomic,
)
from nexus.strategy.action import Action
from nexus.strategy.context import StrategyContext
from nexus.strategy.executor import StrategyExecutor
from nexus.strategy.loader import instantiate_strategy
from nexus.strategy.params import StrategyParams
from nexus.strategy.runner import StrategyRunner

_ZERO = Decimal(0)
_HUNDRED = Decimal('100')
_POSITION_KEY_LENGTH = 2
_log = structlog.get_logger()

__all__ = ['StartupSequencer', 'WiredSensor']


@dataclass(frozen=True)
class WiredSensor:
    '''A trained Sensor ready for signal generation.

    Args:
        sensor_id: Unique identifier ({path_hash_12}:{permutation_id}).
        sensor: Limen Sensor callable (predict(data) -> dict).
        limen_manifest: Limen Manifest for feature preparation.
        round_params: Hyperparameters used to train this Sensor.
        strategy_id: Strategy this Sensor feeds signals to.
        interval_seconds: How often to call predict().
    '''

    sensor_id: str
    sensor: Any
    limen_manifest: Any
    round_params: dict[str, Any]
    strategy_id: str
    interval_seconds: int


@dataclass(frozen=True)
class _SensorWiringTask:
    '''One sensor reconstruction unit: a single (dir, permutation) pair.

    Args:
        strategy_id: Strategy this sensor feeds signals to.
        resolved_dir: Resolved experiment directory.
        path_hash: 12-char hash of `resolved_dir` for the sensor id.
        permutation_id: Permutation (round) id to reconstruct.
        interval_seconds: How often the wired sensor's predict() runs.
    '''

    strategy_id: str
    resolved_dir: Path
    path_hash: str
    permutation_id: int
    interval_seconds: int

    @property
    def key(self) -> tuple[Path, int]:
        '''Return the `(resolved_dir, permutation_id)` reconstruction key.'''

        return (self.resolved_dir, self.permutation_id)


class StartupSequencer:
    '''Orchestrates the startup sequence for a Manager instance.

    Executes steps in order: load manifest (source of account_id and
    allocated_capital) → recover state (snapshot + WAL) → register with
    Trading → reconcile capital → instantiate strategies → restore
    strategy state → replay strategy events → wire sensors → register
    timers → determine mode → dispatch on_startup.

    Args:
        state_store: Persistence facade for state recovery.
        manifest_path: Path to the strategy manifest YAML file.
        strategies_base_path: Base path for resolving strategy file paths.
        strategy_state_path: Directory for strategy state blob files.
        praxis_outbound: Outbound connector for Praxis Trading operations.
        health_evaluator: `HealthEvaluator` consulted by `_determine_mode`
            to start the instance in REDUCE_ONLY/HALTED when boot-time
            health is degraded. Falls back to a default-thresholds
            evaluator when omitted.
        health_snapshot: Optional `HealthSnapshot` driving the
            boot-time mode decision; `_determine_mode` synthesizes a
            healthy default when omitted.
        action_submit: Optional callback invoked with `(actions,
            strategy_id)` for actions that strategies return from
            `on_startup`. When omitted at construction the actions
            are buffered into `_pending_startup_actions` for the
            launcher to drain via `drain_pending_startup_actions`
            once the runtime submitter is wired.
    '''

    def __init__(
        self,
        state_store: StateStore,
        manifest_path: Path,
        strategies_base_path: Path,
        strategy_state_path: Path | None = None,
        praxis_outbound: PraxisOutbound | None = None,
        health_evaluator: HealthEvaluator | None = None,
        health_snapshot: HealthSnapshot | None = None,
        action_submit: Callable[[list[Action], str], None] | None = None,
    ) -> None:
        if not isinstance(state_store, StateStore):
            msg = 'state_store must be a StateStore instance'
            raise ValueError(msg)

        if not isinstance(manifest_path, Path):
            msg = 'manifest_path must be a Path'
            raise ValueError(msg)

        if not isinstance(strategies_base_path, Path):
            msg = 'strategies_base_path must be a Path'
            raise ValueError(msg)

        if strategy_state_path is not None and not isinstance(strategy_state_path, Path):
            msg = 'strategy_state_path must be a Path or None'
            raise ValueError(msg)

        self._state_store = state_store
        self._manifest_path = manifest_path
        self._strategies_base_path = strategies_base_path
        self._strategy_state_path = strategy_state_path
        self._praxis_outbound = praxis_outbound
        self._health_evaluator = health_evaluator
        self._health_snapshot = health_snapshot
        self._action_submit = action_submit

        self._state: InstanceState | None = None
        self._manifest: Manifest | None = None
        self._runner: StrategyRunner | None = None
        self._mode: OperationalMode | None = None
        self._wired_sensors: list[WiredSensor] = []
        self._timer_specs: dict[str, tuple[TimerSpec, ...]] = {}
        self._pending_startup_actions: dict[str, list[Action]] = {}
        self._sensor_wire_max_workers: int = default_max_workers()

    @property
    def timer_specs(self) -> dict[str, tuple[TimerSpec, ...]]:
        '''Return registered timer specs by strategy_id.'''

        return dict(self._timer_specs)

    @property
    def wired_sensors(self) -> list[WiredSensor]:
        '''Return trained Sensors wired during startup.'''

        return list(self._wired_sensors)

    @property
    def instance_state(self) -> InstanceState | None:
        '''Return the live `InstanceState` after `_recover_state` has run.

        Returns the actual mutable state object — not a copy — so callers
        (notably the launcher's runtime `context_provider` and OutcomeLoop)
        observe reservations, position changes, and operational-mode
        transitions made by validator stages and outcome processing.

        `None` before `start()` (or before `_recover_state()`) has run.
        '''

        return self._state

    @property
    def manifest(self) -> Manifest | None:
        '''Return the loaded `Manifest`, or `None` before `_load_manifest()`.

        Exposed alongside `instance_state` so the launcher's runtime
        `context_provider` can derive per-strategy budgets from
        `manifest.capital_pool` × `strategy.capital_pct` without
        reaching into private attrs.
        '''

        return self._manifest

    def start(self) -> StrategyRunner:
        '''Execute the full startup sequence.

        Returns:
            Configured StrategyRunner ready for event dispatch.

        Raises:
            StartupError: If any step fails.
        '''

        self._load_manifest()
        self._recover_state()
        self._register_with_trading()
        self._reconcile_capital()
        self._instantiate_strategies()
        self._restore_strategy_state()
        self._replay_strategy_events()
        self._wire_sensors()
        self._register_timers()
        self._determine_mode()
        self._dispatch_startup()

        if self._runner is None:
            raise StartupError('start', 'runner not initialized')

        return self._runner

    def drain_pending_startup_actions(
        self,
        submitter: Callable[[list[Action], str], None],
    ) -> None:

        '''Forward buffered `on_startup` actions through `submitter`.

        `_dispatch_startup` runs inside `start()` before the launcher
        has assembled the validation pipeline / capital controller /
        submitter (those depend on `instance_state`, which only
        materialises during `start()` itself). Strategy actions
        captured at that point are stashed in
        `_pending_startup_actions` until the launcher finishes wiring
        and calls this method, which submits them in arrival order.
        Idempotent: subsequent calls find an empty buffer and no-op.
        Submitter exceptions per strategy are caught and logged so
        one bad strategy does not block the rest.
        '''

        if not self._pending_startup_actions:
            return

        pending = self._pending_startup_actions
        self._pending_startup_actions = {}

        for strategy_id, actions in pending.items():
            if not actions:
                continue
            try:
                submitter(actions, strategy_id)
            except Exception:  # noqa: BLE001 - submitter must not abort drain
                _log.exception(
                    'pending on_startup action submission raised',
                    strategy_id=strategy_id,
                )

    def _recover_state(self) -> None:
        '''Recover InstanceState from snapshot and WAL.

        Delegates to StateStore.recover() which loads the latest snapshot
        and replays STATE_MUTATION entries from WAL atomically. If no
        persisted state exists (fresh start), creates initial state from
        manifest capital_pool (the operational allocation — NOT
        allocated_capital, which is the infrastructure ceiling). Same
        code path for fresh start and crash recovery.
        '''

        try:
            self._state = self._state_store.recover()
            if self._state is None:
                if self._manifest is None:
                    raise StartupError(
                        'recover_state',
                        'manifest not loaded; cannot bootstrap fresh InstanceState',
                    )
                self._state = InstanceState.fresh(self._manifest.capital_pool)
        except StartupError:
            raise
        except Exception as e:
            raise StartupError('recover_state', str(e)) from e

    def _register_with_trading(self) -> None:
        '''Register this account with Trading sub-system via PraxisOutbound.'''

        if self._praxis_outbound is None or self._manifest is None:
            _log.warning('praxis_outbound or manifest not configured, skipping registration')
            return

        try:
            self._praxis_outbound.register_account(self._manifest.account_id)
        except Exception as e:
            raise StartupError('register_with_trading', str(e)) from e

    def _import_praxis_position(
        self,
        trade_id: str,
        praxis_pos: Any,
        qty: Decimal,
        price: Decimal,
    ) -> Position | None:
        '''Build a Nexus Position from a Praxis-only position when possible.

        Returns the imported Position, or None if the Praxis position lacks
        the fields Nexus requires (e.g. strategy_id, symbol, side).
        '''

        resolved = self._resolve_imported_position_fields(trade_id, praxis_pos, qty)
        if resolved is None:
            return None
        strategy_id, symbol, side = resolved

        try:
            imported = Position(
                trade_id=trade_id,
                strategy_id=strategy_id,
                symbol=symbol,
                side=side,
                size=qty,
                entry_price=price,
                avg_cost_basis=price,
            )
        except ValueError as e:
            _log.warning(
                'cannot import Praxis-only position with invalid fields',
                trade_id=trade_id,
                error=str(e),
            )
            return None

        _log.info(
            'imported Praxis-only position',
            trade_id=trade_id,
            strategy_id=strategy_id,
            symbol=symbol,
            size=str(qty),
        )
        return imported

    def _resolve_imported_position_fields(
        self,
        trade_id: str,
        praxis_pos: Any,
        qty: Decimal,
    ) -> tuple[str, str, OrderSide] | None:
        '''Extract and validate strategy_id / symbol / side from a Praxis position.'''

        strategy_id = getattr(praxis_pos, 'strategy_id', None)
        if not isinstance(strategy_id, str) or not strategy_id.strip():
            _log.warning(
                'cannot import Praxis-only position without strategy_id',
                trade_id=trade_id,
                praxis_qty=str(qty),
            )
            return None

        symbol = getattr(praxis_pos, 'symbol', None)
        if not isinstance(symbol, str) or not symbol.strip():
            _log.warning(
                'cannot import Praxis-only position without symbol',
                trade_id=trade_id,
            )
            return None

        praxis_side = getattr(praxis_pos, 'side', None)
        side_value = getattr(praxis_side, 'value', None)
        try:
            side = OrderSide(side_value)
        except ValueError:
            _log.warning(
                'cannot import Praxis-only position with invalid side',
                trade_id=trade_id,
                side=repr(praxis_side),
            )
            return None

        return strategy_id, symbol, side

    def _reconcile_capital(self) -> None:
        '''Reconcile capital state against Trading positions.

        Pulls positions from Praxis, compares against Nexus state by trade_id,
        updates position_notional to match actual venue state. Logs discrepancies.
        '''

        if self._praxis_outbound is None or self._manifest is None:
            _log.warning('praxis_outbound or manifest not configured, skipping reconciliation')
            return

        if self._state is None:
            raise StartupError('reconcile_capital', 'state not recovered')

        try:
            praxis_positions = self._praxis_outbound.pull_positions(self._manifest.account_id)
        except Exception as e:
            raise StartupError('reconcile_capital', str(e)) from e

        praxis_by_trade_id: dict[str, Any] = {}

        try:
            for key, pos in praxis_positions.items():
                if not isinstance(key, tuple) or len(key) != _POSITION_KEY_LENGTH:
                    _log.warning('skipping Praxis position with unexpected key', key=repr(key))
                    continue
                _, trade_id = key
                if isinstance(trade_id, str):
                    praxis_by_trade_id[trade_id] = pos
                else:
                    _log.warning(
                        'skipping Praxis position with non-string trade_id',
                        trade_id=repr(trade_id),
                    )
        except Exception as e:
            raise StartupError('reconcile_capital', f'failed to parse Praxis positions: {e}') from e

        praxis_total_notional = _ZERO

        for trade_id, praxis_pos in praxis_by_trade_id.items():
            try:
                qty = Decimal(str(praxis_pos.qty))
                price = Decimal(str(praxis_pos.avg_entry_price))
            except (AttributeError, ArithmeticError) as e:
                _log.warning('skipping position with invalid fields', trade_id=trade_id, error=str(e))
                continue

            nexus_pos = self._state.positions.get(trade_id)

            if nexus_pos is None:
                imported = self._import_praxis_position(trade_id, praxis_pos, qty, price)
                if imported is None:
                    _log.warning(
                        'Praxis-only position rejected by import — skipping '
                        'qty * price contribution to position_notional so '
                        'reconcile_at_boot per_strategy_deployed rebuild '
                        '(which sources only from state.positions) does not '
                        'leave position_notional > sum(per_strategy_deployed) '
                        'and trip the attribution-mismatch denial',
                        trade_id=trade_id,
                        qty=str(qty),
                        praxis_avg_entry_price=str(price),
                    )
                    continue
                self._state.positions[trade_id] = imported
                praxis_total_notional += qty * price
                continue

            if nexus_pos.size != qty:
                _log.warning(
                    'position size mismatch — adopting Praxis qty as truth',
                    trade_id=trade_id,
                    nexus_size=str(nexus_pos.size),
                    praxis_qty=str(qty),
                )
                nexus_pos.size = qty

            basis_price = nexus_pos.avg_cost_basis
            if basis_price == _ZERO:
                fallback_price = price if price != _ZERO else nexus_pos.entry_price
                _log.warning(
                    'nexus avg_cost_basis is zero during reconciliation; '
                    'using fallback price and persisting it onto the '
                    'position so reconcile_at_boot, _compute_exit_cost_basis, '
                    'and the next EXIT fill all see a consistent non-zero '
                    'cost basis',
                    trade_id=trade_id,
                    nexus_avg_cost_basis=str(nexus_pos.avg_cost_basis),
                    praxis_avg_entry_price=str(price),
                    nexus_entry_price=str(nexus_pos.entry_price),
                    fallback_price=str(fallback_price),
                )
                basis_price = fallback_price
                nexus_pos.avg_cost_basis = fallback_price

            praxis_total_notional += qty * basis_price

        nexus_only_trade_ids = [
            trade_id for trade_id in self._state.positions
            if trade_id not in praxis_by_trade_id
        ]
        for trade_id in nexus_only_trade_ids:
            evicted = self._state.positions.pop(trade_id)
            _log.warning(
                'evicting Nexus-only position not present in Praxis snapshot — '
                'Praxis truth wins; the per_strategy_deployed rebuild downstream '
                'will not include this stale entry',
                trade_id=trade_id,
                strategy_id=evicted.strategy_id,
                size=str(evicted.size),
                entry_price=str(evicted.entry_price),
            )

        old_notional = self._state.capital.position_notional

        if old_notional != praxis_total_notional:
            _log.warning(
                'position_notional adjusted',
                old=str(old_notional),
                new=str(praxis_total_notional),
            )
            self._state.capital.position_notional = praxis_total_notional

            try:
                self._state_store.checkpoint(self._state)
            except Exception as e:
                raise StartupError('reconcile_capital', f'checkpoint after reconciliation failed: {e}') from e

        _log.info(
            'capital reconciliation complete',
            nexus_positions=len(self._state.positions),
            praxis_positions=len(praxis_by_trade_id),
            position_notional=str(self._state.capital.position_notional),
        )

    def _load_manifest(self) -> None:
        '''Load and validate strategy manifest.'''

        try:
            self._manifest = load_manifest(self._manifest_path)
        except Exception as e:
            raise StartupError('load_manifest', str(e)) from e

    def _instantiate_strategies(self) -> None:
        '''Instantiate strategy classes and build StrategyRunner.'''

        if self._manifest is None:
            raise StartupError('instantiate_strategies', 'manifest not loaded')

        try:
            executors: dict[str, StrategyExecutor] = {}

            for spec in self._manifest.strategies:
                strategy_id = spec.strategy_id.strip()
                strategy = instantiate_strategy(spec, self._strategies_base_path)
                executors[strategy_id] = StrategyExecutor(strategy)

            self._runner = StrategyRunner(executors)
        except Exception as e:
            raise StartupError('instantiate_strategies', str(e)) from e

    def _restore_strategy_state(self) -> None:
        '''Call on_load(bytes) on each strategy for state restoration.

        Loads strategy state from {strategy_state_path}/{strategy_id}.bin
        if the file exists, otherwise passes empty bytes. Same code path
        for fresh start (no files) and crash recovery (files exist).
        '''

        if self._runner is None:
            raise StartupError('restore_strategy_state', 'runner not initialized')

        if self._manifest is None:
            raise StartupError('restore_strategy_state', 'manifest not loaded')

        if self._strategy_state_path is None:
            _log.warning('strategy_state_path not configured, skipping state restoration')
            return

        for spec in self._manifest.strategies:
            strategy_id = spec.strategy_id.strip()

            if '/' in strategy_id or '\\' in strategy_id:
                _log.error('unsafe strategy_id rejected', strategy_id=strategy_id)
                continue

            state_file = self._strategy_state_path / f'{strategy_id}.bin'

            if state_file.exists():
                try:
                    data = state_file.read_bytes()
                except OSError:
                    _log.exception('failed to read strategy state', strategy_id=strategy_id)
                    data = b''
            else:
                data = b''

            try:
                self._runner.dispatch_load(strategy_id, data)
            except Exception:  # noqa: BLE001 - intentional catch-all for strategy code
                _log.exception('on_load failed', strategy_id=strategy_id)

    def _replay_strategy_events(self) -> None:
        '''Replay strategy events from WAL for state reconstruction.

        Reads STRATEGY_EVENT entries from WAL via StateStore and dispatches
        them to the appropriate strategies. Strategies can use these events
        to rebuild internal state (e.g., P&L tracking, position history).
        Same code path for fresh start (no events) and crash recovery (events exist).
        '''

        if self._runner is None:
            raise StartupError('replay_strategy_events', 'runner not initialized')

        if self._manifest is None:
            raise StartupError('replay_strategy_events', 'manifest not loaded')

        try:
            events = self._state_store.read_events()
        except Exception as e:
            raise StartupError('replay_strategy_events', str(e)) from e

        if not events:
            return

        known_strategies = {spec.strategy_id.strip() for spec in self._manifest.strategies}

        for event in events:
            strategy_id = event.strategy_id.strip()

            if strategy_id not in known_strategies:
                _log.warning('skipping event for unknown strategy', strategy_id=strategy_id)
                continue

            try:
                self._runner.dispatch_event_replay(strategy_id, event)
            except Exception:  # noqa: BLE001 - intentional catch-all for strategy code
                _log.exception('on_event_replay failed', strategy_id=strategy_id)

    def _wire_sensors(self) -> None:
        '''Reconstruct Limen Sensors and wire them for signal generation.

        Builds the work set (one entry per (strategy, experiment_dir,
        permutation_id)) from the manifest, then for each unique
        experiment directory constructs a single loader `Trainer(dir)`
        in this process to obtain its `_manifest` (reused for every
        `WiredSensor` from that dir) and its frozen `_data`. Sensors
        already present in the opt-in disk cache
        (`NEXUS_SENSOR_CACHE_DIR`) are loaded directly; the remaining
        misses are reconstructed inline (when `max_workers <= 1`) or
        across a `ProcessPoolExecutor`, then persisted to the cache.

        Per-sensor failures are logged with full context and skipped.
        The account aborts with `StartupError` only when no sensor
        wires — running with zero signal sources is silent dead air.
        '''

        if self._manifest is None:
            raise StartupError('wire_sensors', 'manifest not loaded')

        self._wired_sensors.clear()

        tasks = self._collect_sensor_tasks()
        if not tasks:
            raise StartupError(
                'wire_sensors',
                'manifest declared 0 sensor specs across all strategies; '
                'refusing to start an account with no signal source',
            )

        loaders, failed_dirs = self._load_bundle_trainers(tasks)
        cache_dir = self._sensor_cache_dir()

        misses: list[_SensorWiringTask] = []
        attempted = 0
        raised = 0
        empty = 0

        for task in tasks:
            if task.resolved_dir in failed_dirs:
                attempted += 1
                raised += 1
                continue

            cached_sensor = self._load_cached_sensor(cache_dir, task)
            if cached_sensor is not None:
                self._append_wired_sensor(cached_sensor, task, loaders)
                continue

            misses.append(task)

        reconstructed = self._reconstruct_misses(misses, loaders)

        for task in misses:
            attempted += 1
            sensor = reconstructed.get(task.key)
            if sensor is None:
                if task.key in reconstructed:
                    empty += 1
                else:
                    raised += 1
                continue

            self._store_cached_sensor(cache_dir, task, sensor)
            self._append_wired_sensor(sensor, task, loaders)

        if not self._wired_sensors:
            raise StartupError(
                'wire_sensors',
                f'all {attempted} sensor specs produced no wired sensors '
                f'({raised} raised, {empty} returned no Sensors); '
                'refusing to start an account with no signal source',
            )

    def _collect_sensor_tasks(self) -> list[_SensorWiringTask]:
        '''Flatten the manifest into one reconstruction task per permutation.'''

        if self._manifest is None:
            raise StartupError('wire_sensors', 'manifest not loaded')

        tasks: list[_SensorWiringTask] = []
        for spec in self._manifest.strategies:
            strategy_id = spec.strategy_id

            for sensor_spec in spec.sensors:
                resolved_dir = sensor_spec.experiment_dir.resolve()
                path_hash = hashlib.sha256(
                    str(resolved_dir).encode(),
                ).hexdigest()[:12]

                for permutation_id in sensor_spec.permutation_ids:
                    tasks.append(
                        _SensorWiringTask(
                            strategy_id=strategy_id,
                            resolved_dir=resolved_dir,
                            path_hash=path_hash,
                            permutation_id=permutation_id,
                            interval_seconds=sensor_spec.interval_seconds,
                        ),
                    )

        return tasks

    def _load_bundle_trainers(
        self,
        tasks: list[_SensorWiringTask],
    ) -> tuple[dict[Path, Trainer], set[Path]]:
        '''Construct one loader `Trainer(dir)` per unique experiment dir.

        The loader yields the bundle's `_manifest` (reused for every
        `WiredSensor` from that dir) and the frozen `_data` slice that
        seeds every reconstruction from that dir. A dir whose loader
        raises is recorded as failed so its tasks are counted as
        reconstruction failures without aborting unrelated dirs.

        Args:
            tasks: All reconstruction tasks.

        Returns:
            A `(loaders, failed_dirs)` pair.
        '''

        loaders: dict[Path, Trainer] = {}
        failed_dirs: set[Path] = set()

        for resolved_dir in dict.fromkeys(task.resolved_dir for task in tasks):
            try:
                loaders[resolved_dir] = Trainer(resolved_dir)
            except Exception:  # noqa: BLE001 - per-bundle isolation
                failed_dirs.add(resolved_dir)
                _log.exception(
                    'sensor bundle load failed',
                    experiment_dir=str(resolved_dir),
                )

        return loaders, failed_dirs

    def _reconstruct_misses(
        self,
        misses: list[_SensorWiringTask],
        loaders: dict[Path, Trainer],
    ) -> dict[tuple[Path, int], Any]:
        '''Reconstruct cache-miss sensors inline or across a process pool.

        When `_sensor_wire_max_workers <= 1` reconstruction runs inline
        in this process so a patched `Trainer` (used by tests) applies;
        otherwise it fans out across a `ProcessPoolExecutor` seeded with
        each dir's frozen `_data` via the pool initializer.

        A key present in the result with a non-`None` value reconstructed
        cleanly; a key present mapped to `None` means `train()` returned
        no Sensor; an absent key means the reconstruction raised.

        Args:
            misses: Tasks not satisfied by the cache.
            loaders: Per-dir loader Trainers (source of frozen `_data`).

        Returns:
            Mapping of `task.key` to the reconstructed `Sensor` (or
            `None` for an empty `train()` result).
        '''

        if not misses:
            return {}

        if self._sensor_wire_max_workers <= 1:
            return self._reconstruct_inline(misses, loaders)

        return self._reconstruct_pooled(misses, loaders)

    def _reconstruct_inline(
        self,
        misses: list[_SensorWiringTask],
        loaders: dict[Path, Trainer],
    ) -> dict[tuple[Path, int], Any]:
        '''Reconstruct misses in the current process (no ProcessPool).'''

        results: dict[tuple[Path, int], Any] = {}

        for task in misses:
            loader = loaders[task.resolved_dir]
            try:
                # NOTE: loader._data is a private attribute on Limen Trainer.
                # No public accessor exists as of vaquum_limen 4.0.1.
                trainer = Trainer(task.resolved_dir, data=loader._data)
                sensors = trainer.train([task.permutation_id])
            except Exception:  # noqa: BLE001 - per-sensor isolation
                self._log_reconstruction_failure(task)
                continue

            results[task.key] = sensors[0] if sensors else None

        return results

    def _reconstruct_pooled(
        self,
        misses: list[_SensorWiringTask],
        loaders: dict[Path, Trainer],
    ) -> dict[tuple[Path, int], Any]:
        '''Reconstruct misses across a `ProcessPoolExecutor`.'''

        from nexus.startup import sensor_cache

        miss_dirs = {task.resolved_dir for task in misses}
        # NOTE: loader._data is a private attribute on Limen Trainer.
        # No public accessor exists as of vaquum_limen 4.0.1.
        data_by_dir = {
            str(resolved_dir): loaders[resolved_dir]._data
            for resolved_dir in miss_dirs
        }

        results: dict[tuple[Path, int], Any] = {}
        max_workers = min(self._sensor_wire_max_workers, len(misses))

        with ProcessPoolExecutor(
            max_workers=max_workers,
            initializer=sensor_cache._init_worker,
            initargs=(data_by_dir,),
        ) as pool:
            futures = {
                pool.submit(
                    reconstruct_sensor,
                    str(task.resolved_dir),
                    task.permutation_id,
                ): task
                for task in misses
            }

            for future, task in futures.items():
                try:
                    results[task.key] = future.result()
                except Exception:  # noqa: BLE001 - per-sensor isolation
                    self._log_reconstruction_failure(task)

        return results

    def _append_wired_sensor(
        self,
        sensor: Any,
        task: _SensorWiringTask,
        loaders: dict[Path, Trainer],
    ) -> None:
        '''Wrap a reconstructed/cached Sensor in a WiredSensor and store it.'''

        sensor_id = f'{task.path_hash}:{sensor.permutation_id}'
        # NOTE: loader._manifest is a private attribute on Limen Trainer.
        # No public accessor exists as of vaquum_limen 4.0.1.
        wired = WiredSensor(
            sensor_id=sensor_id,
            sensor=sensor,
            limen_manifest=loaders[task.resolved_dir]._manifest,
            round_params=sensor.round_params,
            strategy_id=task.strategy_id,
            interval_seconds=task.interval_seconds,
        )
        self._wired_sensors.append(wired)
        _log.info(
            'wired sensor',
            sensor_id=sensor_id,
            strategy_id=task.strategy_id,
            interval_seconds=task.interval_seconds,
        )

    def _sensor_cache_dir(self) -> Path | None:
        '''Return the configured sensor cache directory, or None when off.'''

        raw = os.environ.get(CACHE_DIR_ENV)
        if not raw:
            return None

        return Path(raw)

    def _load_cached_sensor(
        self,
        cache_dir: Path | None,
        task: _SensorWiringTask,
    ) -> Any | None:
        '''Load a cached Sensor for a task, or None on miss/corruption.'''

        if cache_dir is None:
            return None

        path = cache_path_for(cache_dir, bundle_id_for(task.resolved_dir), task.permutation_id)
        if not path.is_file():
            return None

        try:
            with path.open('rb') as handle:
                return pickle.load(handle)  # noqa: S301 - operator-owned cache dir, not untrusted input
        except Exception:  # noqa: BLE001 - corrupt cache => treat as miss
            _log.warning(
                'sensor cache entry unreadable, reconstructing',
                experiment_dir=str(task.resolved_dir),
                permutation_id=task.permutation_id,
                cache_path=str(path),
            )
            return None

    def _store_cached_sensor(
        self,
        cache_dir: Path | None,
        task: _SensorWiringTask,
        sensor: Any,
    ) -> None:
        '''Persist a reconstructed Sensor to the cache when enabled.'''

        if cache_dir is None:
            return

        path = cache_path_for(cache_dir, bundle_id_for(task.resolved_dir), task.permutation_id)
        try:
            write_sensor_atomic(path, sensor)
        except Exception:  # noqa: BLE001 - cache write must not abort wiring
            _log.warning(
                'sensor cache write failed',
                experiment_dir=str(task.resolved_dir),
                permutation_id=task.permutation_id,
                cache_path=str(path),
            )

    def _log_reconstruction_failure(self, task: _SensorWiringTask) -> None:
        '''Log a per-sensor reconstruction failure with full context.'''

        _log.exception(
            'sensor wiring failed',
            strategy_id=task.strategy_id,
            experiment_dir=str(task.resolved_dir),
            permutation_ids=[task.permutation_id],
        )

    def _register_timers(self) -> None:
        '''Register strategy timers from manifest.

        Collects timer specs per strategy from the manifest. The caller
        creates and starts a TimerLoop from the stored specs.
        '''

        if self._manifest is None:
            raise StartupError('register_timers', 'manifest not loaded')

        strategy_timers: dict[str, tuple[TimerSpec, ...]] = {}

        for spec in self._manifest.strategies:
            if spec.timers:
                strategy_timers[spec.strategy_id] = spec.timers
                for t in spec.timers:
                    _log.info(
                        'registered timer',
                        strategy_id=spec.strategy_id,
                        timer_id=t.timer_id,
                        interval_seconds=t.interval_seconds,
                    )

        self._timer_specs = strategy_timers

    def _determine_mode(self) -> None:
        '''Determine operational mode based on health.

        Evaluates health snapshot against thresholds if a health_evaluator
        and health_snapshot are configured. When health data is
        unavailable (no `health_snapshot` wired at boot, before the first
        HealthLoop tick lands), defaults to `REDUCE_ONLY` instead of
        `ACTIVE` (PT-FIX-26). The HealthLoop's first tick promotes the
        mode to `ACTIVE` if Praxis health is good — but until then any
        `on_startup` ENTER action gets blocked by the validator's
        `_check_operational_mode` stage rather than landing on the venue
        in a permissive ~5 s window before health is known.

        Mirrors the resolved mode into `state.mode` (with a fresh
        `ModeState`) so the validator sees the same value the
        sequencer-local `_mode` carries into `StrategyContext`.
        '''

        if self._health_evaluator is not None and self._health_snapshot is not None:
            self._mode = self._health_evaluator.evaluate(self._health_snapshot)
            trigger = 'boot_health_evaluation'
            _log.info('mode determined from health', mode=self._mode.value)
        else:
            self._mode = OperationalMode.REDUCE_ONLY
            trigger = 'boot_no_health_data'
            _log.warning(
                'no health data available, defaulting to REDUCE_ONLY '
                'until first HealthLoop tick',
            )

        if self._state is not None:
            self._state.mode = ModeState(
                mode=self._mode,
                trigger=trigger,
                transitioned_at=datetime.now(tz=timezone.utc),
            )

    def _dispatch_startup(self) -> None:
        '''Dispatch on_startup to all strategies.

        Calls dispatch_startup on each strategy with context. Actions
        returned by `Strategy.on_startup` are routed in one of two
        ways depending on whether `self._action_submit` was supplied
        at construction:

        * Submitter wired (production launcher path) — actions are
          forwarded directly through the submitter, which runs them
          through the full validation pipeline before
          `PraxisOutbound.send_command`.
        * Submitter unset — actions are stashed per-strategy in
          `self._pending_startup_actions` so the launcher can drain
          them via `drain_pending_startup_actions(submitter)` once
          the runtime submitter is wired (the validation pipeline
          depends on `instance_state`, which only exists after
          `start()` runs).

        Either way the actions reach the validator before any venue
        contact; the buffered path just defers the submission until
        the launcher finishes assembly.
        '''

        if self._runner is None:
            raise StartupError('dispatch_startup', 'runner not initialized')

        if self._manifest is None:
            raise StartupError('dispatch_startup', 'manifest not loaded')

        if self._mode is None:
            raise StartupError('dispatch_startup', 'mode not determined')

        mode = self._mode

        for spec in self._manifest.strategies:
            capital_available = self._manifest.capital_pool * spec.capital_pct / _HUNDRED
            params = StrategyParams(raw={})
            context = StrategyContext(
                positions=(),
                capital_available=capital_available,
                operational_mode=mode,
            )
            try:
                actions = self._runner.dispatch_startup(
                    spec.strategy_id,
                    params,
                    context,
                )
            except Exception as e:
                msg = f'strategy {spec.strategy_id} on_startup failed: {e}'
                raise StartupError('dispatch_startup', msg) from e

            if not actions:
                continue

            if self._action_submit is None:
                self._pending_startup_actions.setdefault(
                    spec.strategy_id,
                    [],
                ).extend(actions)
                continue

            try:
                self._action_submit(actions, spec.strategy_id)
            except Exception:  # noqa: BLE001 - submitter must not abort startup
                _log.exception(
                    'on_startup action submission raised',
                    strategy_id=spec.strategy_id,
                )
