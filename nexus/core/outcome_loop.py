'''Outcome dispatch loop for runtime strategy → Praxis flow.

Single-thread polling loop that consumes `TradeOutcome`s from a
`PraxisInbound` queue, resolves the owning `strategy_id`, dispatches
`on_outcome` to the appropriate strategy via `StrategyRunner`, and
forwards any captured `list[Action]` through the injected
`action_submit` callback (typically the launcher-curried
`submit_actions` closure from `nexus.strategy.action_submit`).

Paired with `PredictLoop` (signal-driven) and `TimerLoop`
(interval-driven) to complete the three dispatch paths that feed
strategy callbacks at runtime. Exposes the same contract:
idempotent `start()`/`stop()` and a `tick_once()` entry point for
deterministic test harnesses.

`strategy_id` resolution: `TradeOutcome` carries only `command_id`,
not a strategy reference. The launcher supplies a
`resolve_strategy_id(outcome) -> str | None` callable backed by a
per-account registry populated at submission time. Registration is
visible only after the submitter's post-`send_command` loop runs,
while a fast venue (binsim fills in ~50ms) can deliver the
translated outcomes first — so `None` is usually a transient race,
not an unknown command. The loop therefore retries unresolved
outcomes on the `UNRESOLVED_RETRY_DELAYS` backoff schedule (~5.9s
total) before dropping them with an ERROR; it never raises.
'''

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from nexus.core.domain.instance_state import InstanceState
from nexus.infrastructure.praxis_connector.praxis_inbound import PraxisInbound
from nexus.infrastructure.praxis_connector.trade_outcome import TradeOutcome
from nexus.strategy.action import Action
from nexus.strategy.context import StrategyContext
from nexus.strategy.params import StrategyParams
from nexus.strategy.runner import StrategyRunner

__all__ = [
    'ActionSubmitter',
    'OutcomeLoop',
    'OutcomeProcessorCallback',
    'StrategyIdResolver',
]

_log = logging.getLogger(__name__)

ActionSubmitter = Callable[[list[Action], str], None]
StrategyIdResolver = Callable[[TradeOutcome], str | None]
OutcomeProcessorCallback = Callable[[TradeOutcome], None]

UNRESOLVED_RETRY_DELAYS: tuple[float, ...] = (
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    1.0,
    1.0,
    1.0,
    1.0,
)


@dataclass
class _CommandRetryQueue:
    '''Retry state for one command of unresolved outcomes.

    Args:
        due: Monotonic timestamp when the head may next attempt
            resolution.
        pending: The command's parked outcomes in arrival order, each
            with the resolution attempts made for it (only the head's
            count drives backoff and exhaustion).
    '''

    due: float
    pending: list[tuple[TradeOutcome, int]]


class OutcomeLoop:
    '''Worker-thread loop polling `PraxisInbound` for `TradeOutcome`s.

    Args:
        runner: `StrategyRunner` to dispatch `on_outcome` through.
        praxis_inbound: Queue-backed inbound connector.
        state: Live `InstanceState` (passed through for parity with
            PredictLoop/TimerLoop; reserved for future use).
        context_provider: Callable returning the current
            `StrategyContext` for a strategy_id.
        resolve_strategy_id: Callable mapping a `TradeOutcome` to the
            owning `strategy_id` or `None` when unknown.
        action_submit: Optional callback invoked with
            `(actions, strategy_id)` after each dispatch_outcome. When
            `None`, returned actions are discarded.
        process_outcome: Optional callback invoked with the outcome
            after `resolve_strategy_id` succeeds but before
            `dispatch_outcome` runs. The launcher uses this to apply
            venue-lifecycle effects to the per-account
            `CapitalController` via `OutcomeProcessor.process(...)` so
            capital state is current when the strategy callback runs.
            Exceptions are caught and logged; the strategy callback
            still fires so the strategy stays in lockstep with the
            outcome stream even if capital reconciliation degrades.
    '''

    def __init__(
        self,
        runner: StrategyRunner,
        praxis_inbound: PraxisInbound,
        state: InstanceState,
        context_provider: Callable[[str], StrategyContext],
        resolve_strategy_id: StrategyIdResolver,
        action_submit: ActionSubmitter | None = None,
        process_outcome: OutcomeProcessorCallback | None = None,
    ) -> None:
        self._runner = runner
        self._praxis_inbound = praxis_inbound
        self._state = state
        self._context_provider = context_provider
        self._resolve_strategy_id = resolve_strategy_id
        self._action_submit = action_submit
        self._process_outcome = process_outcome
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._unresolved_retries: dict[str, _CommandRetryQueue] = {}

    @property
    def running(self) -> bool:
        '''Whether the worker thread is currently running.'''

        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        '''Spawn the worker thread. Idempotent.'''

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return

            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                daemon=True,
                name='outcome-loop',
            )
            self._thread.start()

    def stop(self, join_timeout: float = 5.0) -> None:
        '''Signal the worker to exit and join it. Idempotent.

        Logs an error and keeps `self._thread` pointing at the live
        worker when `thread.join(timeout=...)` expires while the worker
        is still alive. A subsequent `start()` therefore no-ops (rather
        than spawning a second consumer that would race the orphan on
        the same `PraxisInbound` queue). At MMVP scale the worker exits
        within one `PraxisInbound.poll_timeout` after `stop_event` is
        set, so this branch should never fire in practice.
        '''

        with self._lock:
            thread = self._thread

            if thread is None:
                return

            self._stop_event.set()

        thread.join(timeout=join_timeout)

        if thread.is_alive():
            _log.error(
                'outcome loop worker did not exit within join_timeout; '
                'keeping thread slot to block restart',
                extra={'join_timeout': join_timeout},
            )
            return

        with self._lock:
            if self._thread is thread:
                self._thread = None

    def tick_once(self) -> bool:
        '''Service due unresolved retries, then consume at most one outcome.

        A fresh outcome whose command already has parked unresolved
        siblings is funnelled behind them instead of dispatched inline:
        dispatching it directly would reorder the pair (e.g. FILLED
        before its raced ACK, which `CapitalController.order_fill`
        rejects because only the ACK transitions the order to WORKING).
        All of a command's outcomes drain through the single ordered
        retry path until the command resolves or exhausts.

        Returns:
            `True` when a queued outcome was consumed (regardless of
            whether dispatch succeeded), `False` when the queue was
            empty within the inbound connector's poll timeout. Retry
            servicing does not affect the return value.
        '''

        self._service_unresolved_retries()

        outcome = self._praxis_inbound.receive_outcome()

        if outcome is None:
            return False

        retry_queue = self._unresolved_retries.get(outcome.command_id)

        if retry_queue is not None:
            retry_queue.pending.append((outcome, 0))
            _log.warning(
                'outcome queued behind unresolved sibling',
                extra={
                    'command_id': outcome.command_id,
                    'outcome_id': outcome.outcome_id,
                    'queued_behind': len(retry_queue.pending) - 1,
                },
            )
            return True

        if not self._try_dispatch(outcome):
            self._park(outcome)

        return True

    def _park(self, outcome: TradeOutcome) -> None:
        '''Open a per-command retry queue headed by an unresolved outcome.

        Called immediately after the outcome's first (inline)
        resolution attempt failed: the head enters with one attempt
        recorded and the first backoff delay scheduled.

        Args:
            outcome: The unresolved outcome becoming the queue head.
        '''

        self._unresolved_retries[outcome.command_id] = _CommandRetryQueue(
            due=time.monotonic() + UNRESOLVED_RETRY_DELAYS[0],
            pending=[(outcome, 1)],
        )
        _log.warning(
            'outcome with unresolved strategy_id; retry scheduled',
            extra={
                'command_id': outcome.command_id,
                'outcome_id': outcome.outcome_id,
                'attempt': 1,
                'max_attempts': len(UNRESOLVED_RETRY_DELAYS) + 1,
            },
        )

    def _service_unresolved_retries(self) -> None:
        '''Drive the per-command retry queues whose backoff has elapsed.

        Only each command's head outcome attempts resolution — its
        siblings stay parked behind it regardless of their own arrival
        times, so a command's outcomes can never reorder across the
        retry detour. A resolved head drains the remainder of its queue
        in order; an exhausted head drops the whole queue (every parked
        sibling is equally orphaned when the command never registers).
        Only the worker thread (or a test harness driving `tick_once`)
        touches these structures, so no lock is required.
        '''

        if not self._unresolved_retries:
            return

        now = time.monotonic()

        for command_id in list(self._unresolved_retries):
            retry_queue = self._unresolved_retries[command_id]

            if retry_queue.due > now:
                continue

            head_outcome, head_attempts = retry_queue.pending[0]

            if self._try_dispatch(head_outcome):
                del self._unresolved_retries[command_id]
                self._drain_resolved_queue(
                    command_id,
                    retry_queue.pending[1:],
                    now,
                )
                continue

            if head_attempts >= len(UNRESOLVED_RETRY_DELAYS):
                del self._unresolved_retries[command_id]
                self._log_retries_exhausted(
                    retry_queue.pending,
                    head_attempts + 1,
                )
                continue

            retry_queue.pending[0] = (head_outcome, head_attempts + 1)
            retry_queue.due = now + UNRESOLVED_RETRY_DELAYS[head_attempts]

    def _drain_resolved_queue(
        self,
        command_id: str,
        siblings: list[tuple[TradeOutcome, int]],
        now: float,
    ) -> None:
        '''Dispatch a resolved command's parked siblings in order.

        Resolution can vanish mid-drain — terminal outcome processing
        pops the strategy registry, so a sibling parked behind a
        terminal one may no longer resolve. Such a sibling is re-parked
        as the new queue head (backoff restarted, remaining tail kept
        behind it in order) instead of being silently dropped, keeping
        the retry contract intact for every parked outcome.

        Args:
            command_id: The command whose queue resolved.
            siblings: Parked entries behind the resolved head, in order.
            now: Monotonic timestamp of the current service pass.
        '''

        for index, (sibling, _) in enumerate(siblings):
            if self._try_dispatch(sibling):
                continue

            self._unresolved_retries[command_id] = _CommandRetryQueue(
                due=now + UNRESOLVED_RETRY_DELAYS[0],
                pending=[(sibling, 1), *siblings[index + 1:]],
            )
            _log.warning(
                'sibling unresolved mid-drain; re-parked as queue head',
                extra={
                    'command_id': command_id,
                    'outcome_id': sibling.outcome_id,
                    'requeued_behind': len(siblings) - index - 1,
                },
            )
            return

    def _log_retries_exhausted(
        self,
        pending: list[tuple[TradeOutcome, int]],
        attempts: int,
    ) -> None:
        '''Log the terminal drop of a command's entire retry queue.

        Args:
            pending: The dropped queue, head first.
            attempts: Total resolution attempts made for the head,
                including the inline first attempt.
        '''

        for outcome, _ in pending:
            _log.error(
                'outcome strategy_id unresolved after retries exhausted; '
                'dropping — the command was never registered and its '
                'accounting will only recover via boot replay',
                extra={
                    'command_id': outcome.command_id,
                    'outcome_id': outcome.outcome_id,
                    'outcome_type': outcome.outcome_type.value,
                    'attempts': attempts,
                    'retry_window_seconds': sum(UNRESOLVED_RETRY_DELAYS),
                    'outcome_age_seconds': max(
                        0.0,
                        (
                            datetime.now(tz=timezone.utc) - outcome.timestamp
                        ).total_seconds(),
                    ),
                    'dropped_queue_size': len(pending),
                },
            )

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.tick_once()
            except Exception:  # noqa: BLE001 - worker must never die
                _log.exception('outcome loop tick failed')

    def _try_dispatch(self, outcome: TradeOutcome) -> bool:
        '''Dispatch when the strategy resolves; report whether it did.

        Returns:
            `False` when `resolve_strategy_id` returned `None` (the
            caller decides parking/exhaustion), `True` otherwise —
            including dispatches whose downstream stages failed, which
            are logged and not retried.
        '''

        strategy_id = self._resolve_strategy_id(outcome)

        if strategy_id is None:
            return False

        if self._process_outcome is not None:
            try:
                self._process_outcome(outcome)
            except Exception:  # noqa: BLE001 - processor must not kill the loop
                _log.exception(
                    'process_outcome raised for command %s',
                    outcome.command_id,
                )

        try:
            context = self._context_provider(strategy_id)
        except Exception:  # noqa: BLE001 - provider failure must not kill the loop
            _log.exception(
                'context_provider raised for strategy %s',
                strategy_id,
            )
            return True

        try:
            actions = self._runner.dispatch_outcome(
                strategy_id,
                outcome,
                StrategyParams(raw={}),
                context,
            )
        except Exception:  # noqa: BLE001 - dispatch failure must not kill the loop
            _log.exception(
                'dispatch_outcome raised for strategy %s',
                strategy_id,
            )
            return True

        if self._action_submit is None or not actions:
            return True

        try:
            self._action_submit(actions, strategy_id)
        except Exception:  # noqa: BLE001 - submitter failure must not kill the loop
            _log.exception(
                'action_submit raised for outcome strategy %s',
                strategy_id,
            )

        return True
