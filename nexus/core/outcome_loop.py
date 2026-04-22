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
per-account registry populated at submission time. Returning `None`
makes the loop log and skip the outcome; it never raises.
'''

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from nexus.core.domain.instance_state import InstanceState
from nexus.infrastructure.praxis_connector.praxis_inbound import PraxisInbound
from nexus.infrastructure.praxis_connector.trade_outcome import TradeOutcome
from nexus.strategy.action import Action
from nexus.strategy.context import StrategyContext
from nexus.strategy.params import StrategyParams
from nexus.strategy.runner import StrategyRunner

__all__ = ['ActionSubmitter', 'OutcomeLoop', 'StrategyIdResolver']

_log = logging.getLogger(__name__)

ActionSubmitter = Callable[[list[Action], str], None]
StrategyIdResolver = Callable[[TradeOutcome], str | None]


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
    '''

    def __init__(
        self,
        runner: StrategyRunner,
        praxis_inbound: PraxisInbound,
        state: InstanceState,
        context_provider: Callable[[str], StrategyContext],
        resolve_strategy_id: StrategyIdResolver,
        action_submit: ActionSubmitter | None = None,
    ) -> None:
        self._runner = runner
        self._praxis_inbound = praxis_inbound
        self._state = state
        self._context_provider = context_provider
        self._resolve_strategy_id = resolve_strategy_id
        self._action_submit = action_submit
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

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
        '''Signal the worker to exit and join it. Idempotent.'''

        with self._lock:
            thread = self._thread

            if thread is None:
                return

            self._stop_event.set()

        thread.join(timeout=join_timeout)

        with self._lock:
            if self._thread is thread:
                self._thread = None

    def tick_once(self) -> bool:
        '''Consume at most one outcome and dispatch it.

        Returns:
            `True` when an outcome was consumed (regardless of whether
            dispatch succeeded), `False` when the queue was empty within
            the inbound connector's poll timeout.
        '''

        outcome = self._praxis_inbound.receive_outcome()

        if outcome is None:
            return False

        self._dispatch(outcome)
        return True

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.tick_once()
            except Exception:  # noqa: BLE001 - worker must never die
                _log.exception('outcome loop tick failed')

    def _dispatch(self, outcome: TradeOutcome) -> None:
        strategy_id = self._resolve_strategy_id(outcome)

        if strategy_id is None:
            _log.warning(
                'outcome with unresolved strategy_id; skipping',
                extra={
                    'command_id': outcome.command_id,
                    'outcome_id': outcome.outcome_id,
                },
            )
            return

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
