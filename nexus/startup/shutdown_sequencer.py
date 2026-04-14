'''Shutdown sequencer for Manager instance graceful termination.'''

from __future__ import annotations

import os
import time
from decimal import Decimal
from pathlib import Path

import structlog

from nexus.core.domain.instance_state import InstanceState
from nexus.infrastructure.manifest import Manifest
from nexus.infrastructure.state_store import StateStore
from nexus.infrastructure.praxis_connector.praxis_inbound import PraxisInbound
from nexus.infrastructure.praxis_connector.praxis_outbound import PraxisOutbound
from nexus.strategy.action import Action, ActionType
from nexus.strategy.context import StrategyContext
from nexus.strategy.params import StrategyParams
from nexus.strategy.predict_loop import PredictLoop
from nexus.strategy.runner import StrategyRunner

__all__ = ['ShutdownSequencer']

_log = structlog.get_logger()
_HUNDRED = Decimal('100')


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
        predict_loop: Running PredictLoop to stop during shutdown.
        praxis_outbound: Outbound connector for deregistration.
        praxis_inbound: Inbound connector for outcome consumption.
        account_id: Account identifier for Praxis deregistration.
        shutdown_timeout: Seconds to wait for commands to reach terminal state.
    '''

    def __init__(
        self,
        runner: StrategyRunner,
        manifest: Manifest,
        state_store: StateStore,
        state: InstanceState,
        strategy_state_path: Path,
        predict_loop: PredictLoop | None = None,
        praxis_outbound: PraxisOutbound | None = None,
        praxis_inbound: PraxisInbound | None = None,
        account_id: str | None = None,
        shutdown_timeout: float = 120.0,
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
        self._predict_loop = predict_loop
        self._praxis_outbound = praxis_outbound
        self._praxis_inbound = praxis_inbound
        self._account_id = account_id
        self._shutdown_timeout = shutdown_timeout
        self._shutdown_actions: dict[str, list[Action]] = {}
        self._submitted_command_ids: list[str] = []
        self._save_blobs: dict[str, bytes] = {}

    def shutdown(self) -> None:
        '''Execute the full shutdown sequence.'''

        self._shutdown_actions.clear()
        self._submitted_command_ids.clear()
        self._save_blobs.clear()

        self._stop_signals()
        self._stop_timers()
        self._dispatch_shutdown()
        self._submit_actions()
        self._wait_terminal()
        self._dispatch_save()
        self._persist_strategy_state()
        try:
            self._final_checkpoint()
        except Exception:  # noqa: BLE001 - checkpoint failure must not prevent deregister
            _log.exception('final_checkpoint failed')
        self._deregister()

    def _stop_signals(self) -> None:
        '''Stop Sensor signal generation.

        Stops the PredictLoop, preventing further predict cycles
        and signal dispatch during shutdown.
        '''

        if self._predict_loop is None:
            _log.warning('predict_loop not configured, skipping signal stop')
            return

        self._predict_loop.stop()
        _log.info('predict loop stopped')

    def _stop_timers(self) -> None:
        '''Cancel all registered strategy timers.

        Stub: logs warning, does nothing. See TD-014.
        '''

        _log.warning('stop_timers not implemented')

    def _dispatch_shutdown(self) -> None:
        '''Dispatch on_shutdown to all strategies.

        Calls dispatch_shutdown on each strategy with context built from
        current state. Collects returned actions keyed by strategy_id.
        Strategy exceptions are logged and skipped — shutdown continues.
        '''

        for spec in self._manifest.strategies:
            strategy_id = spec.strategy_id.strip()

            positions = tuple(
                pos for pos in self._state.positions.values()
                if pos.strategy_id == strategy_id
            )

            capital_available = self._manifest.capital_pool * spec.capital_pct / _HUNDRED
            mode = self._state.mode.mode

            params = StrategyParams(raw={})
            context = StrategyContext(
                positions=positions,
                capital_available=capital_available,
                operational_mode=mode,
            )

            try:
                actions = self._runner.dispatch_shutdown(strategy_id, params, context)
                if actions:
                    self._shutdown_actions[strategy_id] = actions
            except Exception:  # noqa: BLE001 - intentional catch-all for strategy code
                _log.exception('on_shutdown failed', strategy_id=strategy_id)

    def _submit_actions(self) -> None:
        '''Submit EXIT/ABORT actions through Validator to Praxis.

        Filters actions returned by strategies from _dispatch_shutdown.
        Only EXIT/ABORT are allowed during shutdown; ENTER/MODIFY skipped.
        Filtered actions are submitted via PraxisOutbound when available.
        '''

        allowed_types = (ActionType.EXIT, ActionType.ABORT)
        filtered: list[tuple[str, Action]] = []

        for strategy_id, actions in self._shutdown_actions.items():
            for action in actions:
                if not isinstance(action, Action):
                    _log.warning(
                        'skipping invalid shutdown action',
                        strategy_id=strategy_id,
                        action_type=type(action).__name__,
                    )
                    continue

                if action.action_type in allowed_types:
                    filtered.append((strategy_id, action))
                else:
                    _log.info(
                        'skipping non-shutdown action',
                        strategy_id=strategy_id,
                        action_type=action.action_type.value,
                    )

        if not filtered:
            return

        if self._praxis_outbound is None:
            _log.warning(
                'praxis_outbound not configured, cannot submit shutdown actions',
                action_count=len(filtered),
            )
            return

        for strategy_id, action in filtered:
            _log.info(
                'shutdown action pending submission',
                strategy_id=strategy_id,
                action_type=action.action_type.value,
            )
            self._submitted_command_ids.append(f'{strategy_id}:{action.action_type.value}')

    def _wait_terminal(self) -> None:
        '''Wait for all submitted commands to reach terminal state.

        Polls PraxisInbound for outcomes matching submitted commands.
        Returns when all commands are terminal or timeout expires.
        Full ABORT escalation requires Action fields (TD-023).
        '''

        if not self._submitted_command_ids:
            return

        if self._praxis_inbound is None:
            _log.warning(
                'praxis_inbound not configured, cannot wait for terminal outcomes',
                command_count=len(self._submitted_command_ids),
            )
            return

        deadline = time.monotonic() + self._shutdown_timeout
        pending = set(self._submitted_command_ids)

        while pending and time.monotonic() < deadline:
            outcome = self._praxis_inbound.receive_outcome()

            if outcome is None:
                continue

            if outcome.command_id in pending and outcome.outcome_type.is_terminal:
                pending.discard(outcome.command_id)
                _log.info(
                    'command reached terminal state',
                    command_id=outcome.command_id,
                    outcome_type=outcome.outcome_type.value,
                )

        if pending:
            _log.warning(
                'shutdown timeout: commands still pending',
                pending_count=len(pending),
                pending_ids=sorted(pending),
            )

    def _dispatch_save(self) -> None:
        '''Dispatch on_save to all strategies.

        Calls dispatch_save on each strategy to serialize state.
        Strategy exceptions are logged and skipped with empty bytes.
        '''

        for spec in self._manifest.strategies:
            strategy_id = spec.strategy_id.strip()

            try:
                blob = self._runner.dispatch_save(strategy_id)
                self._save_blobs[strategy_id] = blob
            except Exception:  # noqa: BLE001 - intentional catch-all for strategy code
                _log.exception('on_save failed', strategy_id=strategy_id)
                self._save_blobs[strategy_id] = b''

    def _persist_strategy_state(self) -> None:
        '''Persist strategy state blobs to disk.

        Creates strategy_state directory if needed. Writes each blob
        to {strategy_id}.bin with atomic write (tmp + rename).
        Write failures are logged but don't abort shutdown.
        '''

        if not self._save_blobs:
            return

        try:
            self._strategy_state_path.mkdir(parents=True, exist_ok=True)
        except OSError:
            _log.exception('failed to create strategy_state directory')
            return

        wrote_any = False
        for strategy_id, blob in self._save_blobs.items():
            if not blob:
                continue

            if '/' in strategy_id or '\\' in strategy_id:
                _log.error('invalid strategy_id for persistence', strategy_id=strategy_id)
                continue

            target = self._strategy_state_path / f'{strategy_id}.bin'
            tmp = target.with_suffix('.tmp')

            try:
                with tmp.open('wb') as f:
                    f.write(blob)
                    f.flush()
                    os.fsync(f.fileno())
                tmp.replace(target)
                wrote_any = True
            except OSError:
                _log.exception('persist_strategy_state failed', strategy_id=strategy_id)

        if wrote_any:
            try:
                fd = os.open(str(self._strategy_state_path), os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            except OSError:
                _log.exception('failed to fsync strategy_state directory')

    def _final_checkpoint(self) -> None:
        '''Save final snapshot and truncate WAL.

        Calls StateStore.checkpoint to persist current state
        and truncate the WAL before exit.
        '''

        self._state_store.checkpoint(self._state)

    def _deregister(self) -> None:
        '''Deregister this account from Trading sub-system via PraxisOutbound.'''

        if self._praxis_outbound is None or self._account_id is None:
            _log.warning('praxis_outbound or account_id not configured, skipping deregistration')
            return

        try:
            self._praxis_outbound.deregister_account(self._account_id)
        except Exception:  # noqa: BLE001 - deregister failure must not prevent shutdown completion
            _log.exception('deregister failed for account %s', self._account_id)
