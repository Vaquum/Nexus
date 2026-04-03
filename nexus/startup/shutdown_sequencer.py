'''Shutdown sequencer for Manager instance graceful termination.'''

from __future__ import annotations

from pathlib import Path

from nexus.core.domain.instance_state import InstanceState
from nexus.infrastructure.manifest import Manifest
from nexus.infrastructure.state_store import StateStore
from nexus.strategy.runner import StrategyRunner

import structlog

__all__ = ['ShutdownSequencer']

_log = structlog.get_logger()


class ShutdownSequencer:
    '''Orchestrates the shutdown sequence for a Manager instance.

    Executes steps in order: stop signals → stop timers → dispatch on_shutdown →
    submit actions through Validator → wait for terminal outcomes → dispatch on_save →
    persist strategy state → final checkpoint → deregister.

    Args:
        runner: StrategyRunner with active strategy executors.
        manifest: Loaded strategy manifest.
        state_store: Persistence facade for checkpointing.
        state: Current instance state.
        strategy_state_path: Directory for strategy state blobs.
    '''

    def __init__(
        self,
        runner: StrategyRunner,
        manifest: Manifest,
        state_store: StateStore,
        state: InstanceState,
        strategy_state_path: Path,
    ) -> None:
        if not isinstance(runner, StrategyRunner):
            msg = 'runner must be a StrategyRunner instance'
            raise ValueError(msg)

        if not isinstance(manifest, Manifest):
            msg = 'manifest must be a Manifest instance'
            raise ValueError(msg)

        if not isinstance(state_store, StateStore):
            msg = 'state_store must be a StateStore instance'
            raise ValueError(msg)

        if not isinstance(state, InstanceState):
            msg = 'state must be an InstanceState instance'
            raise ValueError(msg)

        if not isinstance(strategy_state_path, Path):
            msg = 'strategy_state_path must be a Path'
            raise ValueError(msg)

        self._runner = runner
        self._manifest = manifest
        self._state_store = state_store
        self._state = state
        self._strategy_state_path = strategy_state_path

    def shutdown(self) -> None:
        '''Execute the full shutdown sequence.

        Raises:
            ShutdownError: If a critical step fails (state corruption).
        '''

        self._stop_signals()
        self._stop_timers()
        self._dispatch_shutdown()
        self._submit_actions()
        self._wait_terminal()
        self._dispatch_save()
        self._persist_strategy_state()
        self._final_checkpoint()
        self._deregister()

    def _stop_signals(self) -> None:
        '''Stop predictor_fn signal subscriptions.

        Stub: logs warning, does nothing. See TD-009.
        '''

        _log.warning('stop_signals not implemented')

    def _stop_timers(self) -> None:
        '''Cancel all registered strategy timers.

        Stub: logs warning, does nothing. See TD-010.
        '''

        _log.warning('stop_timers not implemented')

    def _dispatch_shutdown(self) -> None:
        '''Dispatch on_shutdown to all strategies.

        Stub: logs warning, does nothing. Implemented in 9.2.3.
        '''

        _log.warning('dispatch_shutdown not implemented')

    def _submit_actions(self) -> None:
        '''Submit EXIT/ABORT/CANCEL actions through Validator.

        Stub: logs warning, does nothing. Implemented in 9.2.4.
        '''

        _log.warning('submit_actions not implemented')

    def _wait_terminal(self) -> None:
        '''Wait for all submitted commands to reach terminal state.

        Stub: logs warning, does nothing. Implemented in 9.2.5.
        '''

        _log.warning('wait_terminal not implemented')

    def _dispatch_save(self) -> None:
        '''Dispatch on_save to all strategies.

        Stub: logs warning, does nothing. Implemented in 9.2.6.
        '''

        _log.warning('dispatch_save not implemented')

    def _persist_strategy_state(self) -> None:
        '''Persist strategy state blobs to disk.

        Stub: logs warning, does nothing. Implemented in 9.2.7.
        '''

        _log.warning('persist_strategy_state not implemented')

    def _final_checkpoint(self) -> None:
        '''Save final snapshot and truncate WAL.

        Stub: logs warning, does nothing. Implemented in 9.2.8.
        '''

        _log.warning('final_checkpoint not implemented')

    def _deregister(self) -> None:
        '''Deregister from Trading sub-system.

        Stub: logs warning, does nothing. See TD-012.
        '''

        _log.warning('deregister not implemented')
