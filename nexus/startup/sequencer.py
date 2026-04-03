'''Startup sequencer for Manager instance initialization.'''

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from nexus.core.domain.enums import OperationalMode
from nexus.core.domain.instance_state import InstanceState
from nexus.infrastructure.state_store import StateStore
from nexus.infrastructure.manifest import Manifest, load_manifest
from nexus.strategy.context import StrategyContext
from nexus.strategy.loader import instantiate_strategy
from nexus.strategy.executor import StrategyExecutor
from nexus.strategy.params import StrategyParams
from nexus.strategy.runner import StrategyRunner
from nexus.startup.error import StartupError

import structlog

_HUNDRED = Decimal('100')
_log = structlog.get_logger()

__all__ = ['StartupSequencer']


class StartupSequencer:
    '''Orchestrates the startup sequence for a Manager instance.

    Executes steps in order: recover state (snapshot + WAL) → register with Trading →
    reconcile capital → load manifest → instantiate strategies → restore strategy state →
    replay strategy events → wire predictor_fns → register timers →
    determine mode → dispatch on_startup.

    Args:
        state_store: Persistence facade for state recovery.
        manifest_path: Path to the strategy manifest YAML file.
        strategies_base_path: Base path for resolving strategy file paths.
        allocated_capital: Hard ceiling for manifest capital_pool validation.
    '''

    def __init__(
        self,
        state_store: StateStore,
        manifest_path: Path,
        strategies_base_path: Path,
        allocated_capital: Decimal,
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

        if not isinstance(allocated_capital, Decimal) or not allocated_capital.is_finite():
            msg = 'allocated_capital must be a finite Decimal'
            raise ValueError(msg)

        self._state_store = state_store
        self._manifest_path = manifest_path
        self._strategies_base_path = strategies_base_path
        self._allocated_capital = allocated_capital

        self._state: InstanceState | None = None
        self._manifest: Manifest | None = None
        self._runner: StrategyRunner | None = None
        self._mode: OperationalMode | None = None

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
        self._wire_predictor_fns()
        self._register_timers()
        self._determine_mode()
        self._dispatch_startup()

        if self._runner is None:
            raise StartupError('start', 'runner not initialized')

        return self._runner

    def _recover_state(self) -> None:
        '''Recover InstanceState from snapshot and WAL.

        Delegates to StateStore.recover() which loads the latest snapshot
        and replays STATE_MUTATION entries from WAL atomically.
        '''

        try:
            self._state = self._state_store.recover()
        except Exception as e:
            raise StartupError('recover_state', str(e)) from e

    def _register_with_trading(self) -> None:
        '''Register with Trading sub-system.

        Stub: logs warning, does nothing. See TD-005.
        '''

        _log.warning('register_with_trading not implemented')

    def _reconcile_capital(self) -> None:
        '''Reconcile capital state against Trading positions.

        Stub: logs warning, does nothing. See TD-006.
        '''

        _log.warning('reconcile_capital not implemented')

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

        Stub: logs warning, does nothing. See TD-007.
        Strategy state blob storage not implemented yet.
        '''

        _log.warning('restore_strategy_state not implemented')

    def _replay_strategy_events(self) -> None:
        '''Replay strategy events from WAL (actions discarded).

        Stub: logs warning, does nothing. See TD-008.
        Event replay to strategies not implemented yet.
        '''

        _log.warning('replay_strategy_events not implemented')

    def _wire_predictor_fns(self) -> None:
        '''Wire predictor_fn subscriptions.

        Stub: logs warning, does nothing. See TD-009.
        Predictor function wiring not implemented yet.
        '''

        _log.warning('wire_predictor_fns not implemented')

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
