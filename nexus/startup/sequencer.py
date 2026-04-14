'''Startup sequencer for Manager instance initialization.'''

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import structlog

from limen.experiment.trainer.trainer import Trainer

from nexus.core.domain.capital_state import CapitalState
from nexus.infrastructure.praxis_connector.praxis_outbound import PraxisOutbound
from nexus.core.domain.enums import OperationalMode
from nexus.core.domain.instance_state import InstanceState
from nexus.infrastructure.manifest import Manifest, load_manifest
from nexus.infrastructure.state_store import StateStore
from nexus.startup.error import StartupError
from nexus.strategy.context import StrategyContext
from nexus.strategy.executor import StrategyExecutor
from nexus.strategy.loader import instantiate_strategy
from nexus.strategy.params import StrategyParams
from nexus.strategy.runner import StrategyRunner

_ZERO = Decimal(0)
_HUNDRED = Decimal('100')
_log = structlog.get_logger()

__all__ = ['StartupSequencer', 'WiredSensor']


@dataclass(frozen=True)
class WiredSensor:
    '''A trained Sensor ready for signal generation.

    Args:
        sensor_id: Unique identifier ({experiment_name}:{permutation_id}).
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

    Executes steps in order: recover state (snapshot + WAL) → register with Trading →
    reconcile capital → load manifest → instantiate strategies → restore strategy state →
    replay strategy events → wire sensors → register timers →
    determine mode → dispatch on_startup.

    Args:
        state_store: Persistence facade for state recovery.
        manifest_path: Path to the strategy manifest YAML file.
        strategies_base_path: Base path for resolving strategy file paths.
        allocated_capital: Hard ceiling for manifest capital_pool validation.
        strategy_state_path: Directory for strategy state blob files.
        praxis_outbound: Outbound connector for Praxis Trading operations.
        account_id: Account identifier for Praxis registration.
    '''

    def __init__(
        self,
        state_store: StateStore,
        manifest_path: Path,
        strategies_base_path: Path,
        allocated_capital: Decimal,
        strategy_state_path: Path | None = None,
        praxis_outbound: PraxisOutbound | None = None,
        account_id: str | None = None,
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

        if (
            not isinstance(allocated_capital, Decimal)
            or not allocated_capital.is_finite()
            or allocated_capital <= 0
        ):
            msg = 'allocated_capital must be a finite positive Decimal'
            raise ValueError(msg)

        if strategy_state_path is not None and not isinstance(strategy_state_path, Path):
            msg = 'strategy_state_path must be a Path or None'
            raise ValueError(msg)

        self._state_store = state_store
        self._manifest_path = manifest_path
        self._strategies_base_path = strategies_base_path
        self._allocated_capital = allocated_capital
        self._strategy_state_path = strategy_state_path
        self._praxis_outbound = praxis_outbound
        self._account_id = account_id

        self._state: InstanceState | None = None
        self._manifest: Manifest | None = None
        self._runner: StrategyRunner | None = None
        self._mode: OperationalMode | None = None
        self._wired_sensors: list[WiredSensor] = []

    @property
    def wired_sensors(self) -> list[WiredSensor]:
        '''Return trained Sensors wired during startup.'''

        return list(self._wired_sensors)

    def start(self) -> StrategyRunner:
        '''Execute the full startup sequence.

        Returns:
            Configured StrategyRunner ready for event dispatch.

        Raises:
            StartupError: If any step fails.
        '''

        self._recover_state()
        self._register_with_trading()
        self._reconcile_capital()
        self._load_manifest()
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
        allocated capital. Same code path for fresh start and crash recovery.
        '''

        try:
            self._state = self._state_store.recover()
            if self._state is None:
                self._state = InstanceState(
                    capital=CapitalState(capital_pool=self._allocated_capital),
                )
        except Exception as e:
            raise StartupError('recover_state', str(e)) from e

    def _register_with_trading(self) -> None:
        '''Register this account with Trading sub-system via PraxisOutbound.'''

        if self._praxis_outbound is None or self._account_id is None:
            _log.warning('praxis_outbound or account_id not configured, skipping registration')
            return

        try:
            self._praxis_outbound.register_account(self._account_id)
        except Exception as e:
            raise StartupError('register_with_trading', str(e)) from e

    def _reconcile_capital(self) -> None:
        '''Reconcile capital state against Trading positions.

        Pulls positions from Praxis, compares against Nexus state by trade_id,
        updates position_notional to match actual venue state. Logs discrepancies.
        '''

        if self._praxis_outbound is None or self._account_id is None:
            _log.warning('praxis_outbound or account_id not configured, skipping reconciliation')
            return

        if self._state is None:
            raise StartupError('reconcile_capital', 'state not recovered')

        try:
            praxis_positions = self._praxis_outbound.pull_positions(self._account_id)
        except Exception as e:
            raise StartupError('reconcile_capital', str(e)) from e

        praxis_by_trade_id: dict[str, object] = {}

        for (_, trade_id), pos in praxis_positions.items():
            if isinstance(trade_id, str):
                praxis_by_trade_id[trade_id] = pos
            else:
                _log.warning(
                    'skipping Praxis position with non-string trade_id',
                    trade_id=repr(trade_id),
                )

        praxis_total_notional = _ZERO

        for trade_id, praxis_pos in praxis_by_trade_id.items():
            qty = Decimal(str(praxis_pos.qty))
            price = Decimal(str(praxis_pos.avg_entry_price))
            notional = qty * price
            praxis_total_notional += notional

            nexus_pos = self._state.positions.get(trade_id)

            if nexus_pos is None:
                _log.warning(
                    'position in Praxis but not in Nexus',
                    trade_id=trade_id,
                    praxis_qty=str(praxis_pos.qty),
                )
                continue

            if nexus_pos.size != praxis_pos.qty:
                _log.warning(
                    'position size mismatch',
                    trade_id=trade_id,
                    nexus_size=str(nexus_pos.size),
                    praxis_qty=str(praxis_pos.qty),
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
            self._manifest = load_manifest(self._manifest_path, self._allocated_capital)
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

        for spec in self._manifest.strategies:
            strategy_id = spec.strategy_id

            for sensor_spec in spec.sensors:
                experiment_name = sensor_spec.experiment_dir.name

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
                    sensor_id = f'{experiment_name}:{sensor.permutation_id}'
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
        '''Register strategy timers.

        Stub: logs warning, does nothing. See TD-010.
        Timer registration not implemented yet.
        '''

        _log.warning('register_timers not implemented')

    def _determine_mode(self) -> None:
        '''Determine operational mode based on health.

        Stub: always sets ACTIVE. See TD-011.
        Health check not implemented yet.
        '''

        _log.warning('determine_mode health check not implemented, defaulting to ACTIVE')
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
