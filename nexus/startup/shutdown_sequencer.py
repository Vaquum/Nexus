'''Shutdown sequencer for Manager instance graceful termination.'''

from __future__ import annotations

import os
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import nullcontext
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from nexus.core.capital_controller.capital_controller import CapitalController
from nexus.core.domain.enums import OperationalMode, OrderSide
from nexus.core.domain.instance_state import InstanceState
from nexus.core.domain.operational_mode import ModeState
from nexus.core.outcome_loop import OutcomeLoop
from nexus.core.validator.pipeline_models import (
    ValidationAction,
    ValidationDecision,
    ValidationRequestContext,
)
from nexus.infrastructure.manifest import Manifest
from nexus.infrastructure.praxis_connector.order_context import OrderContext
from nexus.infrastructure.praxis_connector.outcome_processor import OutcomeProcessor
from nexus.infrastructure.praxis_connector.praxis_inbound import PraxisInbound
from nexus.infrastructure.praxis_connector.praxis_outbound import PraxisOutbound
from nexus.infrastructure.praxis_connector.trade_outcome import TradeOutcome
from nexus.infrastructure.praxis_connector.translate import translate_to_trade_command
from nexus.infrastructure.state_store import StateStore
from nexus.instance_config import InstanceConfig
from nexus.strategy.action import Action, ActionType
from nexus.strategy.context import StrategyContext
from nexus.strategy.params import StrategyParams
from nexus.strategy.runner import StrategyRunner
from nexus.strategy.timer_loop import TimerLoop

if TYPE_CHECKING:
    from nexus.strategy.predict_loop import PredictLoop

__all__ = ['ShutdownSequencer']

_log = structlog.get_logger()
_HUNDRED = Decimal('100')
_ZERO = Decimal(0)
_SHUTDOWN_ABORT_REASON = 'shutdown'
_ESCALATION_ABORT_REASON = 'shutdown_escalation'
_ESCALATION_TIMEOUT_RATIO = 0.5


class ShutdownSequencer:
    '''Orchestrates the shutdown sequence for a Manager instance.

    Executes steps in order: stop signals → stop timers → stop outcome loop →
    dispatch on_shutdown → submit actions through Validator → wait for terminal
    outcomes → dispatch on_save → persist strategy state → final checkpoint →
    deregister.

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
        positions_lock: Optional `threading.Lock` shared with PredictLoop /
            TimerLoop / OutcomeProcessor that guards `state.positions`.
            When provided, `_dispatch_shutdown` snapshots
            `state.positions.values()` under the lock (MAJOR-S) so a
            timed-out OutcomeLoop join cannot trigger
            `RuntimeError: dictionary changed size during iteration`
            mid-snapshot and abort the shutdown before
            `_persist_strategy_state` / `_final_checkpoint` run. None
            disables the guard (legacy single-threaded shutdown paths).
            When supplied, the launcher MUST also wire
            `state.risk.lock = positions_lock` (same object) AND pass
            `capital_controller` — `__init__` raises `RuntimeError`
            otherwise so the FINAL-MAJOR-05 lock cluster cannot
            silently degrade.
        capital_controller: Optional `CapitalController` whose `_lock`
            is acquired by `_final_checkpoint` so the snapshot
            serializer iterations of `state.capital.per_strategy_deployed`
            and reads of the aggregate notional fields
            (`in_flight_order_notional`, `working_order_notional`,
            `position_notional`, `reservation_notional`, `fee_reserve`)
            cannot race a still-alive OutcomeLoop worker (FINAL-MAJOR-05).
            Required when `positions_lock` is supplied; `__init__`
            raises `RuntimeError` if positions_lock is provided
            without it. None disables the guard (legacy
            single-threaded shutdown paths).
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
        outcome_processor: OutcomeProcessor | None = None,
        non_pending_outcome_handler: Callable[[TradeOutcome], None] | None = None,
        positions_lock: threading.Lock | None = None,
        capital_controller: CapitalController | None = None,
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
        self._outcome_processor = outcome_processor
        self._exit_contexts: dict[str, OrderContext] = {}
        self._non_pending_outcome_handler = non_pending_outcome_handler
        self._positions_lock = positions_lock
        self._capital_controller = capital_controller

        if positions_lock is not None and (
            not hasattr(state.risk, 'lock')
            or state.risk.lock is not positions_lock
        ):
            risk_lock = getattr(state.risk, 'lock', '<missing>')
            msg = (
                'ShutdownSequencer requires `state.risk.lock is positions_lock` '
                'whenever `positions_lock` is supplied so a single acquisition '
                'in `_final_checkpoint` covers both `state.positions` AND '
                '`state.risk.per_strategy` iteration. Without identity-equal '
                'locks the snapshot serializer would iterate `per_strategy` '
                'unguarded against new-strategy inserts from a still-alive '
                'OutcomeLoop worker (FINAL-MAJOR-05 race remains reachable). '
                f'Got positions_lock={positions_lock!r}, '
                f'state.risk.lock={risk_lock!r}.'
            )
            raise RuntimeError(msg)

        if positions_lock is not None and capital_controller is None:
            msg = (
                'ShutdownSequencer requires `capital_controller` whenever '
                '`positions_lock` is supplied. `_final_checkpoint` needs the '
                'controller lock to freeze `state.capital.per_strategy_deployed` '
                'and the aggregate notional fields during snapshot serialization. '
                'Without it the capital-side `dictionary changed size during '
                'iteration` race against a still-alive OutcomeLoop worker '
                'remains reachable — the lock-cluster fix is only partial.'
            )
            raise RuntimeError(msg)

    def shutdown(self) -> None:
        '''Execute the full shutdown sequence.'''

        self._shutdown_actions.clear()
        self._submitted_command_ids.clear()
        self._exit_contexts.clear()
        self._save_blobs.clear()

        self._halt_state_mode()
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

    def _halt_state_mode(self) -> None:
        '''Flip `state.mode` to HALTED before any loop is stopped.

        The OutcomeLoop keeps draining `praxis_inbound` until
        `_stop_outcome_loop` runs, and a FILLED outcome arriving in
        that window can drive `Strategy.on_outcome` to return a
        fresh `Action(ENTER)`. The validator's
        `_check_operational_mode` stage rejects ENTER only when
        `state.mode != ACTIVE`, so flipping to HALTED here ensures
        any in-flight outcome dispatch's downstream order is rejected
        with `INTAKE_MODE_BLOCKS_ENTER` instead of leaking past
        `_dispatch_shutdown` to the venue.
        '''

        self._state.mode = ModeState(
            mode=OperationalMode.HALTED,
            trigger='shutdown',
            transitioned_at=datetime.now(tz=timezone.utc),
        )
        _log.info('state mode flipped to HALTED for shutdown')

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

        MAJOR-S: snapshots `state.positions.values()` under
        `positions_lock`. `_stop_outcome_loop` runs before this method
        and is supposed to halt the OutcomeLoop, but the join can time
        out (5s default) leaving the worker still applying outcomes.
        Without the lock the iteration could fire `RuntimeError:
        dictionary changed size during iteration` mid-snapshot, escape
        the per-strategy try/except below, and terminate shutdown
        before `_persist_strategy_state` / `_final_checkpoint` ran —
        data loss. The snapshot is collected once under the lock;
        per-strategy filtering by `strategy_id` happens after release.
        '''

        if self._outcome_loop is not None and self._outcome_loop.running:
            _log.warning(
                'OutcomeLoop still running at _dispatch_shutdown; '
                '_stop_outcome_loop join_timeout fired without worker '
                'exit — positions_lock around snapshot iteration is '
                'load-bearing for safe iteration'
            )

        lock_cm = self._positions_lock if self._positions_lock is not None else nullcontext()

        with lock_cm:
            positions_snapshot = tuple(self._state.positions.values())

        for spec in self._manifest.strategies:
            strategy_id = spec.strategy_id.strip()

            positions = tuple(
                pos for pos in positions_snapshot
                if pos.strategy_id == strategy_id
            )

            gross_budget = self._manifest.capital_pool * spec.capital_pct / _HUNDRED
            deployed = self._state.capital.per_strategy_deployed.get(strategy_id, _ZERO)
            capital_available = max(gross_budget - deployed, _ZERO)
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

        # PR #55 round-16 review: build the OrderContext FIRST (pure
        # construction, raises ValueError on bad inputs but mutates
        # nothing). Pre-fix the `pending_exit` increment ran before
        # this call; if `_build_exit_order_context` raised, the except
        # block returned without reverting `pending_exit`, leaving the
        # position artificially blocked for the rest of shutdown
        # (subsequent EXIT submissions for the same trade would see
        # `pending_exit > 0` from the failed prior attempt). Post-fix
        # the increment only runs after construction succeeds.
        try:
            order_context = self._build_exit_order_context(
                strategy_id=strategy_id,
                action=action,
                command_id=returned_id,
                validation_context=context,
            )
        except ValueError:
            _log.exception(
                'shutdown exit OrderContext rejected by invariants; '
                'skipping _wait_terminal for this command — venue may '
                'leave the order open. Next boot reconcile_at_boot '
                'will reset stranded aggregates and the venue '
                'reconciliation pass should pick up the dangling order',
                strategy_id=strategy_id,
                trade_id=action.trade_id,
                command_id=returned_id,
            )
            return

        if (
            action.trade_id is not None
            and self._state is not None
            and context.order_size is not None
        ):
            lock_cm = (
                self._positions_lock
                if self._positions_lock is not None
                else nullcontext()
            )
            with lock_cm:
                position = self._state.positions.get(action.trade_id)
                if position is not None:
                    position.pending_exit += context.order_size

        self._exit_contexts[returned_id] = order_context
        self._submitted_command_ids.append(returned_id)

        _log.info(
            'exit submitted',
            strategy_id=strategy_id,
            trade_id=action.trade_id,
            command_id=returned_id,
        )

    def _build_exit_order_context(
        self,
        *,
        strategy_id: str,
        action: Action,
        command_id: str,
        validation_context: ValidationRequestContext,
    ) -> OrderContext:
        '''Build the per-command `OrderContext` consumed by
        `OutcomeProcessor.process` when the shutdown EXIT's terminal
        outcome arrives.

        PT-FIX-31: shutdown EXITs bypass the validator, so the
        `validation_context.order_notional` is always zero and cannot
        be reused. The notional here is approximated from the
        position's `entry_price * action.size` purely so the
        `OrderContext` invariants pass; only `outcome.fill_size` /
        `outcome.fill_price` are actually consumed by the
        non-entry FILL path inside `_update_position_on_fill`.
        '''

        if action.trade_id is None or action.size is None:
            msg = 'shutdown EXIT action missing trade_id or size'
            raise ValueError(msg)

        if validation_context.order_side is None:
            msg = 'shutdown EXIT validation context missing order_side'
            raise ValueError(msg)

        position = self._state.positions.get(action.trade_id)
        if position is None:
            msg = (
                f'shutdown EXIT trade_id {action.trade_id!r} not in '
                'state.positions; OutcomeLoop tick may have removed the '
                'position between _build_exit_context and _build_exit_order_context'
            )
            raise ValueError(msg)

        approx_notional = position.entry_price * action.size

        if approx_notional <= _ZERO:
            msg = (
                f'shutdown EXIT for trade_id {action.trade_id!r} produced '
                f'non-positive notional: entry_price={position.entry_price} '
                f'action.size={action.size}; refusing to construct OrderContext'
            )
            raise ValueError(msg)

        return OrderContext(
            command_id=command_id,
            strategy_id=strategy_id,
            trade_id=action.trade_id,
            side=validation_context.order_side,
            order_size=action.size,
            order_notional=approx_notional,
            estimated_fees=_ZERO,
            is_entry=False,
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

            if outcome.command_id not in remaining:
                self._dispatch_non_pending_outcome(outcome)
                continue

            if outcome.outcome_type.is_fill or outcome.outcome_type.is_terminal:
                self._apply_terminal_outcome(outcome)

            if outcome.outcome_type.is_terminal:
                remaining.discard(outcome.command_id)
                _log.info(
                    'command reached terminal state',
                    command_id=outcome.command_id,
                    outcome_type=outcome.outcome_type.value,
                )

        return remaining

    def _dispatch_non_pending_outcome(self, outcome: TradeOutcome) -> None:
        '''Route a queued outcome whose command_id is NOT a shutdown EXIT.

        PT-FIX-44: after `_stop_outcome_loop` halts the OutcomeLoop,
        `_poll_until_terminal` is the sole consumer of `praxis_inbound`.
        Pre-shutdown commands (ENTERs in flight when shutdown began)
        whose outcomes were queued before the OutcomeLoop stopped
        arrive here. Pre-fix the `not in remaining` guard discarded
        them with `continue`, so the FILLED outcome was lost: position
        never grew in `state.positions`, capital stayed in
        `in_flight_order_notional` (released only by next boot's
        `reconcile_at_boot`), and the persisted snapshot drifted from
        venue truth.

        Post-fix: when `non_pending_outcome_handler` is wired (the
        launcher passes its `process_outcome` closure), route the
        outcome through it. The handler applies the outcome to state
        and capital exactly as the OutcomeLoop would have.
        '''

        if self._non_pending_outcome_handler is None:
            _log.warning(
                'shutdown drained pre-shutdown outcome with no handler '
                'wired; state may drift from venue',
                command_id=outcome.command_id,
                outcome_type=outcome.outcome_type.value,
            )
            return

        try:
            self._non_pending_outcome_handler(outcome)
        except Exception:  # noqa: BLE001 - handler failure must not abort shutdown
            _log.exception(
                'non_pending_outcome_handler raised during shutdown',
                command_id=outcome.command_id,
                outcome_type=outcome.outcome_type.value,
            )

    def _apply_terminal_outcome(self, outcome: TradeOutcome) -> None:
        '''Apply a shutdown-EXIT outcome to instance state.

        Handles both fill outcomes (FILLED and PARTIAL — PARTIAL is
        non-terminal but still mutates state) and non-fill terminals
        (REJECTED, CANCELED, EXPIRED). The helper name retains
        "terminal" for git-history continuity with PT-FIX-31, but the
        actual gate is `is_fill OR is_non_fill_terminal`, applied by
        the caller `_poll_until_terminal`.

        PT-FIX-31: pre-fix the shutdown sequencer drained terminal
        outcomes from `praxis_inbound` purely as a "did the venue
        confirm?" signal; it never invoked `OutcomeProcessor.process`.
        Because `OutcomeLoop._dispatch` is already stopped by
        `_stop_outcome_loop` before `_submit_actions` runs, no other
        code path applied the EXIT FILL to `state.positions` or
        `state.capital`. Result: shutdown-EXIT FILLs were silently
        dropped at the state level, leaving the next boot to recover
        a stale `Position` and `position_notional` that the venue had
        already closed.

        PT-FIX-38: routes both FILLED AND PARTIAL outcomes through
        `OutcomeProcessor` (`is_fill` set) so a partial fill that
        precedes a CANCELED / EXPIRED terminal also decrements
        `state.positions`. Pre-PT-FIX-38 this method gated on
        `== TradeOutcomeType.FILLED`, so a PARTIAL fill on a
        shutdown EXIT was discarded — the position kept the
        partial-fill amount that the venue had actually decremented.

        MAJOR-I: now also routes REJECTED / CANCELED / EXPIRED
        non-fill terminals through `OutcomeProcessor.process`. Pre-fix
        the early-return on `not is_fill` left `position.pending_exit`
        non-zero (incremented in `submit_actions`) — the persisted
        Position then denied the next boot's first EXIT with
        `INTAKE_EXIT_SIZE_EXCEEDS_REMAINING`. Post-fix
        `_handle_reject` / `_handle_cancel` (with `context.is_exit=True`)
        invoke `_clear_pending_exit` to release the stuck value.
        '''

        command_id = outcome.command_id
        outcome_type = outcome.outcome_type

        context = self._exit_contexts.get(command_id)
        if context is None:
            return

        if self._outcome_processor is None:
            _log.warning(
                'no outcome_processor wired; shutdown EXIT terminal state '
                'update skipped — next boot may see stale Position or '
                'stuck pending_exit',
                command_id=command_id,
                trade_id=context.trade_id,
                outcome_type=outcome_type.value,
            )
            return

        try:
            self._outcome_processor.process(outcome, context)
        except Exception:  # noqa: BLE001 - state-update failure must not abort shutdown
            _log.exception(
                'outcome_processor.process raised during shutdown',
                command_id=command_id,
                trade_id=context.trade_id,
                outcome_type=outcome_type.value,
            )

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

        FINAL-MAJOR-05: holds positions_lock + CapitalController._lock
        across the snapshot serialization so the
        `serialize_state` iterations of `state.positions`,
        `state.risk.per_strategy`, and `state.capital.per_strategy_deployed`
        cannot race a still-alive OutcomeLoop worker (after
        `_stop_outcome_loop`'s join_timeout) writing those dicts;
        the CapitalController lock also covers the aggregate field
        reads (`in_flight_order_notional`, `working_order_notional`,
        `position_notional`, `reservation_notional`, `fee_reserve`)
        so the snapshot is internally consistent (closes R17-A
        TD-054 transitively). state_store.checkpoint internally
        acquires `_wal_lock` (FINAL-MAJOR-04). Lock-order:
        positions_lock → CapitalController._lock → _wal_lock.

        Requires the launcher to wire `state.risk.lock = positions_lock`
        (same lock identity) so a single acquisition covers both
        `state.positions` and `state.risk.per_strategy`. Enforced at
        construction time by `__init__`; `state.risk.lock_cm()` cannot
        be added to the chain here because `threading.Lock` is
        non-reentrant and would deadlock if it IS positions_lock.
        '''

        positions_cm = (
            self._positions_lock
            if self._positions_lock is not None
            else nullcontext()
        )
        capital_cm = (
            self._capital_controller.lock_cm()
            if self._capital_controller is not None
            else nullcontext()
        )
        with positions_cm, capital_cm:
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
