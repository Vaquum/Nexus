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
        self._unresolved_retries: list[tuple[TradeOutcome, int, float]] = []

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

        self._dispatch(outcome)
        return True

    def _service_unresolved_retries(self) -> None:
        '''Re-dispatch retry-scheduled outcomes whose backoff has elapsed.

        Entries are kept in arrival order so sibling outcomes of the
        same command (ACK then FILLED) re-dispatch in their original
        relative order once the submitter's registration becomes
        visible. Only the worker thread (or a test harness driving
        `tick_once`) touches the retry list, so no lock is required.
        '''

        if not self._unresolved_retries:
            return

        now = time.monotonic()
        due = [entry for entry in self._unresolved_retries if entry[2] <= now]

        if not due:
            return

        self._unresolved_retries = [
            entry for entry in self._unresolved_retries if entry[2] > now
        ]

        for outcome, attempts, _ in due:
            self._dispatch(outcome, attempts)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.tick_once()
            except Exception:  # noqa: BLE001 - worker must never die
                _log.exception('outcome loop tick failed')

    def _dispatch(self, outcome: TradeOutcome, attempts: int = 0) -> None:
        strategy_id = self._resolve_strategy_id(outcome)

        if strategy_id is None:
            if attempts < len(UNRESOLVED_RETRY_DELAYS):
                self._unresolved_retries.append((
                    outcome,
                    attempts + 1,
                    time.monotonic() + UNRESOLVED_RETRY_DELAYS[attempts],
                ))
                _log.warning(
                    'outcome with unresolved strategy_id; retry scheduled',
                    extra={
                        'command_id': outcome.command_id,
                        'outcome_id': outcome.outcome_id,
                        'attempt': attempts + 1,
                        'max_attempts': len(UNRESOLVED_RETRY_DELAYS),
                    },
                )
                return

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
                    'outcome_age_seconds': (
                        datetime.now(tz=timezone.utc) - outcome.timestamp
                    ).total_seconds(),
                },
            )
            return

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
            return

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
            return

        if self._action_submit is None or not actions:
            return

        try:
            self._action_submit(actions, strategy_id)
        except Exception:  # noqa: BLE001 - submitter failure must not kill the loop
            _log.exception(
                'action_submit raised for outcome strategy %s',
                strategy_id,
            )
