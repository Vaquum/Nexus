'''Tests for `nexus.core.outcome_loop.OutcomeLoop` (PT.3.1).'''

from __future__ import annotations

import queue
import time
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from nexus.core.domain.instance_state import InstanceState
from nexus.core.outcome_loop import OutcomeLoop
from nexus.infrastructure.praxis_connector.praxis_inbound import PraxisInbound
from nexus.infrastructure.praxis_connector.trade_outcome import TradeOutcome
from nexus.infrastructure.praxis_connector.trade_outcome_type import TradeOutcomeType
from nexus.strategy.action import Action, ActionType
from nexus.strategy.context import StrategyContext
from nexus.core.domain.enums import OperationalMode


def _ack_outcome(command_id: str = 'cmd_1') -> TradeOutcome:
    return TradeOutcome(
        outcome_id=f'outcome_{command_id}',
        command_id=command_id,
        outcome_type=TradeOutcomeType.ACK,
        timestamp=datetime(2026, 4, 22, 12, 0, 0, tzinfo=timezone.utc),
    )


def _state() -> InstanceState:
    return InstanceState.fresh(Decimal('10000'))


def _context(_strategy_id: str) -> StrategyContext:
    return StrategyContext(
        positions=(),
        capital_available=Decimal('1000'),
        operational_mode=OperationalMode.ACTIVE,
    )


def _runner_stub(actions: list[Action] | None = None) -> MagicMock:
    runner = MagicMock()
    runner.dispatch_outcome.return_value = actions or []
    return runner


def _inbound_with(*outcomes: TradeOutcome, poll_timeout: float = 0.01) -> PraxisInbound:
    q: queue.Queue[TradeOutcome] = queue.Queue()

    for outcome in outcomes:
        q.put(outcome)

    return PraxisInbound(outcome_queue=q, poll_timeout=poll_timeout)


class TestTickOnce:

    def test_tick_once_returns_false_when_queue_empty(self) -> None:
        inbound = _inbound_with()
        runner = _runner_stub()

        loop = OutcomeLoop(
            runner=runner,
            praxis_inbound=inbound,
            state=_state(),
            context_provider=_context,
            resolve_strategy_id=lambda _outcome: 'strat_a',
        )

        assert loop.tick_once() is False
        runner.dispatch_outcome.assert_not_called()

    def test_tick_once_dispatches_known_outcome(self) -> None:
        outcome = _ack_outcome('cmd_known')
        inbound = _inbound_with(outcome)
        runner = _runner_stub()

        loop = OutcomeLoop(
            runner=runner,
            praxis_inbound=inbound,
            state=_state(),
            context_provider=_context,
            resolve_strategy_id=lambda _o: 'strat_a',
        )

        assert loop.tick_once() is True

        runner.dispatch_outcome.assert_called_once()
        call_args = runner.dispatch_outcome.call_args
        assert call_args.args[0] == 'strat_a'
        assert call_args.args[1] is outcome

    def test_tick_once_skips_when_strategy_id_unresolved(self) -> None:
        inbound = _inbound_with(_ack_outcome('cmd_orphan'))
        runner = _runner_stub()

        loop = OutcomeLoop(
            runner=runner,
            praxis_inbound=inbound,
            state=_state(),
            context_provider=_context,
            resolve_strategy_id=lambda _o: None,
        )

        assert loop.tick_once() is True
        runner.dispatch_outcome.assert_not_called()

    def test_tick_once_forwards_actions_to_action_submit(self) -> None:
        reply_action = Action(action_type=ActionType.ABORT, command_id='cmd_reply')
        inbound = _inbound_with(_ack_outcome('cmd_trigger'))
        runner = _runner_stub(actions=[reply_action])

        submitter = MagicMock()

        loop = OutcomeLoop(
            runner=runner,
            praxis_inbound=inbound,
            state=_state(),
            context_provider=_context,
            resolve_strategy_id=lambda _o: 'strat_a',
            action_submit=submitter,
        )

        loop.tick_once()

        submitter.assert_called_once_with([reply_action], 'strat_a')

    def test_tick_once_skips_action_submit_when_no_actions_returned(self) -> None:
        inbound = _inbound_with(_ack_outcome('cmd_noop'))
        runner = _runner_stub(actions=[])
        submitter = MagicMock()

        loop = OutcomeLoop(
            runner=runner,
            praxis_inbound=inbound,
            state=_state(),
            context_provider=_context,
            resolve_strategy_id=lambda _o: 'strat_a',
            action_submit=submitter,
        )

        loop.tick_once()

        submitter.assert_not_called()

    def test_tick_once_absorbs_dispatch_exception(self) -> None:
        inbound = _inbound_with(_ack_outcome('cmd_boom'))
        runner = MagicMock()
        runner.dispatch_outcome.side_effect = RuntimeError('synthetic')

        loop = OutcomeLoop(
            runner=runner,
            praxis_inbound=inbound,
            state=_state(),
            context_provider=_context,
            resolve_strategy_id=lambda _o: 'strat_a',
        )

        consumed = loop.tick_once()

        assert consumed is True
        runner.dispatch_outcome.assert_called_once()


class TestStartStop:

    def test_start_stop_idempotent(self) -> None:
        inbound = _inbound_with(poll_timeout=0.01)
        runner = _runner_stub()

        loop = OutcomeLoop(
            runner=runner,
            praxis_inbound=inbound,
            state=_state(),
            context_provider=_context,
            resolve_strategy_id=lambda _o: 'strat_a',
        )

        loop.start()
        loop.start()
        assert loop.running

        loop.stop()
        loop.stop()
        assert not loop.running

    def test_worker_thread_consumes_queue_after_start(self) -> None:
        outcome = _ack_outcome('cmd_worker')
        q: queue.Queue[TradeOutcome] = queue.Queue()
        inbound = PraxisInbound(outcome_queue=q, poll_timeout=0.01)

        runner = _runner_stub()

        loop = OutcomeLoop(
            runner=runner,
            praxis_inbound=inbound,
            state=_state(),
            context_provider=_context,
            resolve_strategy_id=lambda _o: 'strat_a',
        )

        loop.start()

        try:
            q.put(outcome)

            deadline = time.monotonic() + 2.0

            while (
                runner.dispatch_outcome.call_count == 0
                and time.monotonic() < deadline
            ):
                time.sleep(0.02)

            assert runner.dispatch_outcome.call_count == 1
        finally:
            loop.stop()


class TestUnresolvedRetry:

    def test_retry_dispatches_once_registration_lands(self) -> None:
        registry: dict[str, str] = {}
        inbound = _inbound_with(_ack_outcome('cmd_raced'))
        runner = _runner_stub()
        processed: list[str] = []

        loop = OutcomeLoop(
            runner=runner,
            praxis_inbound=inbound,
            state=_state(),
            context_provider=_context,
            resolve_strategy_id=lambda o: registry.get(o.command_id),
            process_outcome=lambda o: processed.append(o.outcome_id),
        )

        assert loop.tick_once() is True
        runner.dispatch_outcome.assert_not_called()
        assert processed == []

        registry['cmd_raced'] = 'strat_a'

        deadline = time.monotonic() + 2.0

        while runner.dispatch_outcome.call_count == 0 and time.monotonic() < deadline:
            loop.tick_once()
            time.sleep(0.005)

        assert runner.dispatch_outcome.call_count == 1
        assert processed == ['outcome_cmd_raced']

    def test_retry_exhaustion_drops_outcome(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            'nexus.core.outcome_loop.UNRESOLVED_RETRY_DELAYS',
            (0.0, 0.0),
        )
        inbound = _inbound_with(_ack_outcome('cmd_orphan'))
        runner = _runner_stub()

        loop = OutcomeLoop(
            runner=runner,
            praxis_inbound=inbound,
            state=_state(),
            context_provider=_context,
            resolve_strategy_id=lambda _o: None,
        )

        assert loop.tick_once() is True
        assert loop.tick_once() is False
        assert loop.tick_once() is False

        assert loop.tick_once() is False
        runner.dispatch_outcome.assert_not_called()

    def test_retry_preserves_sibling_order(self) -> None:
        registry: dict[str, str] = {}
        ack = _ack_outcome('cmd_pair')
        filled = TradeOutcome(
            outcome_id='outcome_cmd_pair_filled',
            command_id='cmd_pair',
            outcome_type=TradeOutcomeType.FILLED,
            timestamp=datetime(2026, 4, 22, 12, 0, 1, tzinfo=timezone.utc),
            fill_size=Decimal('1'),
            fill_price=Decimal('100'),
            fill_notional=Decimal('100'),
            actual_fees=Decimal('0'),
        )
        inbound = _inbound_with(ack, filled)
        runner = _runner_stub()
        processed: list[str] = []

        loop = OutcomeLoop(
            runner=runner,
            praxis_inbound=inbound,
            state=_state(),
            context_provider=_context,
            resolve_strategy_id=lambda o: registry.get(o.command_id),
            process_outcome=lambda o: processed.append(o.outcome_id),
        )

        assert loop.tick_once() is True
        assert loop.tick_once() is True
        assert processed == []

        registry['cmd_pair'] = 'strat_a'

        deadline = time.monotonic() + 2.0

        while len(processed) < 2 and time.monotonic() < deadline:
            loop.tick_once()
            time.sleep(0.005)

        assert processed == ['outcome_cmd_pair', 'outcome_cmd_pair_filled']

    def test_fresh_sibling_funnels_behind_parked_outcome(self) -> None:
        registry: dict[str, str] = {}
        ack = _ack_outcome('cmd_mixed')
        filled = TradeOutcome(
            outcome_id='outcome_cmd_mixed_filled',
            command_id='cmd_mixed',
            outcome_type=TradeOutcomeType.FILLED,
            timestamp=datetime(2026, 4, 22, 12, 0, 1, tzinfo=timezone.utc),
            fill_size=Decimal('1'),
            fill_price=Decimal('100'),
            fill_notional=Decimal('100'),
            actual_fees=Decimal('0'),
        )
        q: queue.Queue[TradeOutcome] = queue.Queue()
        inbound = PraxisInbound(outcome_queue=q, poll_timeout=0.01)
        runner = _runner_stub()
        processed: list[str] = []

        loop = OutcomeLoop(
            runner=runner,
            praxis_inbound=inbound,
            state=_state(),
            context_provider=_context,
            resolve_strategy_id=lambda o: registry.get(o.command_id),
            process_outcome=lambda o: processed.append(o.outcome_id),
        )

        q.put(ack)
        assert loop.tick_once() is True
        assert processed == []

        registry['cmd_mixed'] = 'strat_a'
        q.put(filled)

        assert loop.tick_once() is True
        assert processed == []

        deadline = time.monotonic() + 2.0

        while len(processed) < 2 and time.monotonic() < deadline:
            loop.tick_once()
            time.sleep(0.005)

        assert processed == ['outcome_cmd_mixed', 'outcome_cmd_mixed_filled']

    def test_sibling_unresolved_mid_drain_is_reparked(self) -> None:
        registry: dict[str, str] = {}
        blocked: set[str] = {'outcome_cmd_drain_filled'}
        ack = _ack_outcome('cmd_drain')
        filled = TradeOutcome(
            outcome_id='outcome_cmd_drain_filled',
            command_id='cmd_drain',
            outcome_type=TradeOutcomeType.FILLED,
            timestamp=datetime(2026, 4, 22, 12, 0, 1, tzinfo=timezone.utc),
            fill_size=Decimal('1'),
            fill_price=Decimal('100'),
            fill_notional=Decimal('100'),
            actual_fees=Decimal('0'),
        )

        def _resolver(outcome: TradeOutcome) -> str | None:
            if outcome.outcome_id in blocked:
                return None

            return registry.get(outcome.command_id)

        inbound = _inbound_with(ack, filled)
        runner = _runner_stub()
        processed: list[str] = []

        loop = OutcomeLoop(
            runner=runner,
            praxis_inbound=inbound,
            state=_state(),
            context_provider=_context,
            resolve_strategy_id=_resolver,
            process_outcome=lambda o: processed.append(o.outcome_id),
        )

        assert loop.tick_once() is True
        assert loop.tick_once() is True

        registry['cmd_drain'] = 'strat_a'

        deadline = time.monotonic() + 2.0

        while len(processed) < 1 and time.monotonic() < deadline:
            loop.tick_once()
            time.sleep(0.005)

        assert processed == ['outcome_cmd_drain']

        blocked.clear()

        while len(processed) < 2 and time.monotonic() < deadline:
            loop.tick_once()
            time.sleep(0.005)

        assert processed == ['outcome_cmd_drain', 'outcome_cmd_drain_filled']


class TestProcessOutcomeHook:

    def test_process_outcome_invoked_before_dispatch_outcome(self) -> None:
        inbound = _inbound_with(_ack_outcome())
        runner = _runner_stub()

        call_order: list[str] = []
        runner.dispatch_outcome.side_effect = (
            lambda *_a, **_kw: call_order.append('dispatch') or []
        )

        def process(_outcome: TradeOutcome) -> None:
            call_order.append('process')

        loop = OutcomeLoop(
            runner=runner,
            praxis_inbound=inbound,
            state=_state(),
            context_provider=_context,
            resolve_strategy_id=lambda _o: 'strat_a',
            process_outcome=process,
        )

        consumed = loop.tick_once()

        assert consumed
        assert call_order == ['process', 'dispatch']

    def test_process_outcome_exception_is_swallowed_dispatch_still_runs(self) -> None:
        inbound = _inbound_with(_ack_outcome())
        runner = _runner_stub()

        def boom(_outcome: TradeOutcome) -> None:
            raise RuntimeError('processor blew up')

        loop = OutcomeLoop(
            runner=runner,
            praxis_inbound=inbound,
            state=_state(),
            context_provider=_context,
            resolve_strategy_id=lambda _o: 'strat_a',
            process_outcome=boom,
        )

        consumed = loop.tick_once()

        assert consumed
        assert runner.dispatch_outcome.call_count == 1

    def test_process_outcome_skipped_when_strategy_id_unresolved(self) -> None:
        inbound = _inbound_with(_ack_outcome())
        runner = _runner_stub()

        process_called: list[TradeOutcome] = []

        def process(outcome: TradeOutcome) -> None:
            process_called.append(outcome)

        loop = OutcomeLoop(
            runner=runner,
            praxis_inbound=inbound,
            state=_state(),
            context_provider=_context,
            resolve_strategy_id=lambda _o: None,
            process_outcome=process,
        )

        consumed = loop.tick_once()

        assert consumed
        assert process_called == []
        assert runner.dispatch_outcome.call_count == 0
