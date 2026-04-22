'''Startup sequencer for Manager instance initialization.'''

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import structlog

from limen.experiment.trainer.trainer import Trainer

from nexus.core.domain.enums import OperationalMode, OrderSide
from nexus.core.domain.instance_state import InstanceState
from nexus.core.domain.position import Position
from nexus.core.health_evaluator import HealthEvaluator, HealthSnapshot
from nexus.infrastructure.praxis_connector.praxis_outbound import PraxisOutbound
from nexus.infrastructure.manifest import Manifest, TimerSpec, load_manifest
from nexus.infrastructure.state_store import StateStore
from nexus.startup.error import StartupError
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

        self._state: InstanceState | None = None
        self._manifest: Manifest | None = None
        self._runner: StrategyRunner | None = None
        self._mode: OperationalMode | None = None
        self._wired_sensors: list[WiredSensor] = []
        self._timer_specs: dict[str, tuple[TimerSpec, ...]] = {}

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
            notional = qty * price
            praxis_total_notional += notional

            nexus_pos = self._state.positions.get(trade_id)

            if nexus_pos is None:
                imported = self._import_praxis_position(trade_id, praxis_pos, qty, price)
                if imported is not None:
                    self._state.positions[trade_id] = imported
                continue

            if nexus_pos.size != qty:
                _log.warning(
                    'position size mismatch',
                    trade_id=trade_id,
                    nexus_size=str(nexus_pos.size),
                    praxis_qty=str(qty),
                )

        for trade_id in self._state.positions:
            if trade_id not in praxis_by_trade_id:
                _log.warning(
                    'position in Nexus but not in Praxis',
                    trade_id=trade_id,
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
        '''Train Limen Sensors and wire them for signal generation.

        For each SensorSpec in the manifest, calls Trainer(experiment_dir).train(permutation_ids)
        to produce Sensor callables. Stores WiredSensor entries for later use by the predict loop.
        '''

        if self._manifest is None:
            raise StartupError('wire_sensors', 'manifest not loaded')

        self._wired_sensors.clear()

        for spec in self._manifest.strategies:
            strategy_id = spec.strategy_id

            for sensor_spec in spec.sensors:
                path_hash = hashlib.sha256(
                    str(sensor_spec.experiment_dir.resolve()).encode(),
                ).hexdigest()[:12]

                try:
                    trainer = Trainer(sensor_spec.experiment_dir)
                    sensors = trainer.train(list(sensor_spec.permutation_ids))
                except Exception as e:
                    raise StartupError(
                        'wire_sensors',
                        f'strategy {strategy_id!r} experiment '
                        f'{sensor_spec.experiment_dir}: {e}',
                    ) from e

                for sensor in sensors:
                    sensor_id = f'{path_hash}:{sensor.permutation_id}'
                    # NOTE: trainer._manifest is a private attribute on Limen Trainer.
                    # No public accessor exists as of vaquum_limen 1.52.0.
                    wired = WiredSensor(
                        sensor_id=sensor_id,
                        sensor=sensor,
                        limen_manifest=trainer._manifest,
                        round_params=sensor.round_params,
                        strategy_id=strategy_id,
                        interval_seconds=sensor_spec.interval_seconds,
                    )
                    self._wired_sensors.append(wired)
                    _log.info(
                        'wired sensor',
                        sensor_id=sensor_id,
                        strategy_id=strategy_id,
                        interval_seconds=sensor_spec.interval_seconds,
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
        and health_snapshot are configured. Defaults to ACTIVE when health
        data is unavailable (no health signal source wired yet — TD-026).
        '''

        if self._health_evaluator is not None and self._health_snapshot is not None:
            self._mode = self._health_evaluator.evaluate(self._health_snapshot)
            _log.info('mode determined from health', mode=self._mode.value)
        else:
            _log.warning('no health data available, defaulting to ACTIVE')
            self._mode = OperationalMode.ACTIVE

    def _dispatch_startup(self) -> None:
        '''Dispatch on_startup to all strategies.

        Calls dispatch_startup on each strategy with context.
        Actions are not validated (Validator wiring is later phase).
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
                self._runner.dispatch_startup(spec.strategy_id, params, context)
            except Exception as e:
                msg = f'strategy {spec.strategy_id} on_startup failed: {e}'
                raise StartupError('dispatch_startup', msg) from e
