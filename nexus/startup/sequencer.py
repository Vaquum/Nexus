'''Startup sequencer for Manager instance initialization.'''

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from nexus.core.domain.instance_state import InstanceState
from nexus.infrastructure.state_store import StateStore
from nexus.infrastructure.manifest import Manifest
from nexus.strategy.runner import StrategyRunner
from nexus.startup.error import StartupError

__all__ = ['StartupSequencer']


class StartupSequencer:
    '''Orchestrates the startup sequence for a Manager instance.

    Executes steps in order: load state → replay WAL → register with Trading →
    reconcile capital → load manifest → instantiate strategies → on_load →
    replay strategy events → wire predictor_fns → register timers →
    set mode → on_startup.

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

        import structlog
        structlog.get_logger().warning('register_with_trading not implemented')

    def _reconcile_capital(self) -> None:
        '''Reconcile capital state against Trading positions.

        Stub: logs warning, does nothing. See TD-006.
        '''

        import structlog
        structlog.get_logger().warning('reconcile_capital not implemented')

    def _load_manifest(self) -> None:
        '''Load and validate strategy manifest.'''

        raise NotImplementedError

    def _instantiate_strategies(self) -> None:
        '''Instantiate strategy classes and build StrategyRunner.'''

        raise NotImplementedError

    def _restore_strategy_state(self) -> None:
        '''Call on_load(bytes) on each strategy for state restoration.'''

        raise NotImplementedError

    def _replay_strategy_events(self) -> None:
        '''Replay strategy events from WAL (actions discarded).'''

        raise NotImplementedError

    def _wire_predictor_fns(self) -> None:
        '''Wire predictor_fn subscriptions (stub).'''

        raise NotImplementedError

    def _register_timers(self) -> None:
        '''Register strategy timers (stub).'''

        raise NotImplementedError

    def _determine_mode(self) -> None:
        '''Determine operational mode based on health.'''

        raise NotImplementedError

    def _dispatch_startup(self) -> None:
        '''Dispatch on_startup to all strategies.'''

        raise NotImplementedError
