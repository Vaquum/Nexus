'''Shutdown sequencer for Manager instance graceful termination.'''

from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import structlog

from nexus.core.domain.enums import OrderSide
from nexus.core.domain.instance_state import InstanceState
from nexus.core.outcome_loop import OutcomeLoop
from nexus.core.validator.pipeline_models import (
    ValidationAction,
    ValidationDecision,
    ValidationRequestContext,
)
from nexus.infrastructure.manifest import Manifest
from nexus.infrastructure.praxis_connector.praxis_inbound import PraxisInbound
from nexus.infrastructure.praxis_connector.praxis_outbound import PraxisOutbound
from nexus.infrastructure.praxis_connector.translate import translate_to_trade_command
from nexus.infrastructure.state_store import StateStore
from nexus.instance_config import InstanceConfig
from nexus.strategy.action import Action, ActionType
from nexus.strategy.context import StrategyContext
from nexus.strategy.params import StrategyParams
from nexus.strategy.predict_loop import PredictLoop
from nexus.strategy.runner import StrategyRunner
from nexus.strategy.timer_loop import TimerLoop

__all__ = ['ShutdownSequencer']

_log = structlog.get_logger()
_HUNDRED = Decimal('100')
_ZERO = Decimal(0)
_SHUTDOWN_ABORT_REASON = 'shutdown'
_ESCALATION_ABORT_REASON = 'shutdown_escalation'
_ESCALATION_TIMEOUT_RATIO = 0.5


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
        timer_loop: Running TimerLoop to stop during shutdown.
        outcome_loop: Running OutcomeLoop to stop before
            `_wait_terminal`. Both consume from the same
            `PraxisInbound` queue, so the OutcomeLoop must halt first
            to avoid two consumers racing on terminal outcomes.
        praxis_outbound: Outbound connector for deregistration.
        praxis_inbound: Inbound connector for outcome consumption.
        account_id: Account identifier for Praxis deregistration. When config
            is also provided, account_id must equal config.account_id.
        shutdown_timeout: Seconds to wait for commands to reach terminal state.
        config: Instance configuration carrying account_id, venue, stp_mode
            for shutdown command translation. When provided alongside
            account_id, the two account_ids must match.
    '''

    def __init__(
        self,
        runner: StrategyRunner,
        manifest: Manifest,
        state_store: StateStore,
        state: InstanceState,
        strategy_state_path: Path,
        predict_loop: PredictLoop | None = None,
        timer_loop: TimerLoop | None = None,
        outcome_loop: OutcomeLoop | None = None,
        praxis_outbound: PraxisOutbound | None = None,
        praxis_inbound: PraxisInbound | None = None,
        account_id: str | None = None,
        shutdown_timeout: float = 120.0,
        config: InstanceConfig | None = None,
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

        if config is not None and not isinstance(config, InstanceConfig):
            msg = 'config must be an InstanceConfig instance or None'
            raise ValueError(msg)

        if (
            config is not None
            and account_id is not None
            and account_id != config.account_id
        ):
            msg = (
                'account_id and config.account_id must match: '
                f'{account_id!r} vs {config.account_id!r}'
            )
            raise ValueError(msg)

        self._runner = runner
        self._manifest = manifest
        self._state_store = state_store
        self._state = state
        self._strategy_state_path = strategy_state_path
        self._predict_loop = predict_loop
        self._timer_loop = timer_loop
        self._outcome_loop = outcome_loop
        self._praxis_outbound = praxis_outbound
        self._praxis_inbound = praxis_inbound
        self._account_id = account_id
        self._shutdown_timeout = shutdown_timeout
        self._config = config
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
        self._stop_outcome_loop()
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

        Stops the TimerLoop, preventing further on_timer callbacks
        during shutdown.
        '''

        if self._timer_loop is None:
            _log.warning('timer_loop not configured, skipping timer stop')
            return

        self._timer_loop.stop()
        _log.info('timer loop stopped')

    def _stop_outcome_loop(self) -> None:
        '''Stop the OutcomeLoop before `_wait_terminal` polls for outcomes.

        OutcomeLoop and `_wait_terminal` both consume from the same
        `PraxisInbound` queue; leaving the loop running would steal
        terminal outcomes out of the shutdown-path poll.
        '''

        if self._outcome_loop is None:
            return

        self._outcome_loop.stop()
        _log.info('outcome loop stopped')

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
        '''Submit EXIT/ABORT actions to Praxis.

        Filters actions returned by strategies from _dispatch_shutdown.
        Only EXIT/ABORT are allowed during shutdown; ENTER/MODIFY skipped.
        EXIT goes through translate → PraxisOutbound.send_command.
        ABORT goes through PraxisOutbound.send_abort.
        Returned command_ids are collected in _submitted_command_ids for
        _wait_terminal to poll.
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

        if self._config is None:
            _log.warning(
                'config not configured, cannot submit shutdown actions',
                action_count=len(filtered),
            )
            return

        for strategy_id, action in filtered:
            if action.action_type == ActionType.EXIT:
                self._submit_exit(strategy_id, action)
            else:
                self._submit_abort(action)

    def _submit_exit(self, strategy_id: str, action: Action) -> None:
        '''Translate an EXIT Action and submit it as a NEW_ORDER.'''

        if self._praxis_outbound is None or self._config is None:
            return

        context = self._build_exit_context(strategy_id, action)
        if context is None:
            return

        cmd = translate_to_trade_command(
            action,
            context,
            ValidationDecision(allowed=True),
            self._config,
            datetime.now(tz=timezone.utc),
        )

        try:
            returned_id = self._praxis_outbound.send_command(cmd)
        except Exception:  # noqa: BLE001 - outbound failure must not abort shutdown
            _log.exception(
                'exit submission failed',
                strategy_id=strategy_id,
                trade_id=action.trade_id,
            )
            return

        self._submitted_command_ids.append(returned_id)
        _log.info(
            'exit submitted',
            strategy_id=strategy_id,
            trade_id=action.trade_id,
            command_id=returned_id,
        )

    def _build_exit_context(
        self,
        strategy_id: str,
        action: Action,
    ) -> ValidationRequestContext | None:
        '''Build a ValidationRequestContext for an EXIT action or return None.'''

        if action.trade_id is None:
            _log.warning('exit action missing trade_id', strategy_id=strategy_id)
            return None

        position = self._state.positions.get(action.trade_id)
        if position is None:
            _log.warning(
                'exit action references unknown trade_id',
                strategy_id=strategy_id,
                trade_id=action.trade_id,
            )
            return None

        if self._config is None:
            return None

        exit_side = (
            OrderSide.SELL if position.side == OrderSide.BUY else OrderSide.BUY
        )
        command_id = f'shutdown-{strategy_id}-{uuid.uuid4().hex[:8]}'
        return ValidationRequestContext(
            strategy_id=strategy_id,
            action=ValidationAction.EXIT,
            symbol=position.symbol,
            order_side=exit_side,
            order_size=action.size,
            command_id=command_id,
            trade_id=action.trade_id,
            order_notional=_ZERO,
            estimated_fees=_ZERO,
            strategy_budget=_ZERO,
            state=self._state,
            config=self._config,
        )

    def _submit_abort(self, action: Action) -> None:
        '''Submit an ABORT Action via PraxisOutbound.send_abort.'''

        if self._praxis_outbound is None or self._config is None:
            return

        if action.command_id is None:
            _log.warning('abort action missing command_id')
            return

        try:
            self._praxis_outbound.send_abort(
                command_id=action.command_id,
                account_id=self._config.account_id,
                reason=_SHUTDOWN_ABORT_REASON,
                created_at=datetime.now(tz=timezone.utc),
            )
        except Exception:  # noqa: BLE001 - outbound failure must not abort shutdown
            _log.exception('abort submission failed', command_id=action.command_id)
            return

        self._submitted_command_ids.append(action.command_id)
        _log.info('abort submitted', command_id=action.command_id)

    def _wait_terminal(self) -> None:
        '''Wait for all submitted commands to reach terminal state.

        Polls PraxisInbound for outcomes matching submitted commands.
        On first-round timeout, escalates by issuing ABORT for each still-
        pending command via PraxisOutbound, then polls again with a shorter
        deadline before giving up.
        '''

        if not self._submitted_command_ids:
            return

        if self._praxis_inbound is None:
            _log.warning(
                'praxis_inbound not configured, cannot wait for terminal outcomes',
                command_count=len(self._submitted_command_ids),
            )
            return

        pending = self._poll_until_terminal(
            set(self._submitted_command_ids),
            self._shutdown_timeout,
        )

        if not pending:
            return

        self._escalate_abort_pending(pending)

        pending = self._poll_until_terminal(
            pending,
            self._shutdown_timeout * _ESCALATION_TIMEOUT_RATIO,
        )

        if pending:
            _log.warning(
                'shutdown timeout after escalation: commands still pending',
                pending_count=len(pending),
                pending_ids=sorted(pending),
            )

    def _poll_until_terminal(
        self,
        pending: set[str],
        timeout: float,
    ) -> set[str]:
        '''Poll PraxisInbound until pending is empty or deadline expires.'''

        if self._praxis_inbound is None or timeout <= 0:
            return pending

        remaining = set(pending)
        deadline = time.monotonic() + timeout

        while remaining and time.monotonic() < deadline:
            outcome = self._praxis_inbound.receive_outcome()

            if outcome is None:
                continue

            if outcome.command_id in remaining and outcome.outcome_type.is_terminal:
                remaining.discard(outcome.command_id)
                _log.info(
                    'command reached terminal state',
                    command_id=outcome.command_id,
                    outcome_type=outcome.outcome_type.value,
                )

        return remaining

    def _escalate_abort_pending(self, pending: set[str]) -> None:
        '''Send ABORT for each still-pending command and log outcomes.'''

        if self._praxis_outbound is None or self._config is None:
            _log.warning(
                'praxis_outbound or config not configured, cannot escalate aborts',
                pending_count=len(pending),
            )
            return

        _log.warning(
            'shutdown timeout: escalating aborts for pending commands',
            pending_count=len(pending),
            pending_ids=sorted(pending),
        )

        for command_id in sorted(pending):
            try:
                self._praxis_outbound.send_abort(
                    command_id=command_id,
                    account_id=self._config.account_id,
                    reason=_ESCALATION_ABORT_REASON,
                    created_at=datetime.now(tz=timezone.utc),
                )
            except Exception:  # noqa: BLE001 - outbound failure must not abort shutdown
                _log.exception(
                    'escalation abort failed',
                    command_id=command_id,
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
            _log.exception('deregister failed', account_id=self._account_id)
