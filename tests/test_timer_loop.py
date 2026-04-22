'''Tests for TimerLoop strategy timer dispatch.'''

from __future__ import annotations

import threading
import time
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

from nexus.core.domain.enums import OperationalMode, OrderSide
from nexus.core.domain.order_types import ExecutionMode, OrderType
from nexus.infrastructure.manifest import TimerSpec
from nexus.strategy.action import Action, ActionType
from nexus.strategy.context import StrategyContext
from nexus.strategy.runner import StrategyRunner
from nexus.strategy.timer_loop import TimerLoop


def _mock_context_provider(_strategy_id: str) -> StrategyContext:
    return StrategyContext(
        positions=(),
        capital_available=Decimal('10000'),
        operational_mode=OperationalMode.ACTIVE,
    )


class TestTimerLoop:

    def test_start_and_stop(self) -> None:
        '''TimerLoop starts and stops without error.'''

        runner = MagicMock(spec=StrategyRunner)
        timers = {'strat_a': (TimerSpec(timer_id='check', interval_seconds=10),)}

        loop = TimerLoop(
            runner=runner,
            strategy_timers=timers,
            context_provider=_mock_context_provider,
        )

        loop.start()
        assert loop.running is True

        loop.stop()
        assert loop.running is False

    def test_dispatches_timer(self) -> None:
        '''TimerLoop dispatches on_timer to runner.'''

        runner = MagicMock(spec=StrategyRunner)
        dispatched = threading.Event()

        def track_dispatch(*_args: Any, **_kwargs: Any) -> list:
            dispatched.set()
            return []

        runner.dispatch_timer.side_effect = track_dispatch
        timers = {'strat_a': (TimerSpec(timer_id='check', interval_seconds=1),)}

        loop = TimerLoop(
            runner=runner,
            strategy_timers=timers,
            context_provider=_mock_context_provider,
        )

        loop.start()
        dispatched.wait(timeout=3)
        loop.stop()

        assert runner.dispatch_timer.called
        call_args = runner.dispatch_timer.call_args
        assert call_args[0][0] == 'strat_a'
        assert call_args[0][1] == 'check'

    def test_multiple_timers_for_strategy(self) -> None:
        '''Multiple timers for one strategy all fire.'''

        runner = MagicMock(spec=StrategyRunner)
        timer_ids_seen: list[str] = []
        both_fired = threading.Event()

        def track_dispatch(*args: Any, **_kwargs: Any) -> list:
            timer_ids_seen.append(args[1])
            if len(set(timer_ids_seen)) >= 2:
                both_fired.set()
            return []

        runner.dispatch_timer.side_effect = track_dispatch

        timers = {
            'strat_a': (
                TimerSpec(timer_id='fast', interval_seconds=1),
                TimerSpec(timer_id='slow', interval_seconds=1),
            ),
        }

        loop = TimerLoop(
            runner=runner,
            strategy_timers=timers,
            context_provider=_mock_context_provider,
        )

        loop.start()
        both_fired.wait(timeout=3)
        loop.stop()

        assert 'fast' in timer_ids_seen
        assert 'slow' in timer_ids_seen

    def test_stop_prevents_dispatch(self) -> None:
        '''After stop, no more timer events dispatched.'''

        runner = MagicMock(spec=StrategyRunner)
        timers = {'strat_a': (TimerSpec(timer_id='check', interval_seconds=1),)}

        loop = TimerLoop(
            runner=runner,
            strategy_timers=timers,
            context_provider=_mock_context_provider,
        )

        loop.start()
        loop.stop()

        runner.dispatch_timer.reset_mock()

        time.sleep(1.5)

        assert not runner.dispatch_timer.called

    def test_empty_timers_is_noop(self) -> None:
        '''Empty strategy_timers starts and stops without error.'''

        runner = MagicMock(spec=StrategyRunner)

        loop = TimerLoop(
            runner=runner,
            strategy_timers={},
            context_provider=_mock_context_provider,
        )

        loop.start()
        assert loop.running is True

        loop.stop()
        assert loop.running is False
        assert not runner.dispatch_timer.called

    def test_error_in_callback_does_not_crash(self) -> None:
        '''Exception in dispatch_timer is caught, loop continues.'''

        runner = MagicMock(spec=StrategyRunner)
        call_count = 0
        second_call = threading.Event()

        def failing_then_ok(*_args: Any, **_kwargs: Any) -> list:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                msg = 'strategy bug'
                raise RuntimeError(msg)
            second_call.set()
            return []

        runner.dispatch_timer.side_effect = failing_then_ok
        timers = {'strat_a': (TimerSpec(timer_id='check', interval_seconds=1),)}

        loop = TimerLoop(
            runner=runner,
            strategy_timers=timers,
            context_provider=_mock_context_provider,
        )

        loop.start()
        second_call.wait(timeout=4)
        loop.stop()

        assert call_count >= 2

    def test_action_submit_called_with_returned_actions(self) -> None:
        '''Actions returned from dispatch_timer reach the submitter with strategy_id.'''

        action = Action(
            action_type=ActionType.ENTER,
            direction=OrderSide.BUY,
            size=Decimal('0.01'),
            execution_mode=ExecutionMode.SINGLE_SHOT,
            order_type=OrderType.MARKET,
            deadline=300,
        )

        runner = MagicMock(spec=StrategyRunner)
        runner.dispatch_timer.return_value = [action]

        submitted = threading.Event()
        captured: list[tuple[list[Action], str]] = []

        def submitter(actions: list[Action], strategy_id: str) -> None:
            captured.append((actions, strategy_id))
            submitted.set()

        timers = {'strat_a': (TimerSpec(timer_id='check', interval_seconds=1),)}

        loop = TimerLoop(
            runner=runner,
            strategy_timers=timers,
            context_provider=_mock_context_provider,
            action_submit=submitter,
        )

        loop.start()
        submitted.wait(timeout=3)
        loop.stop()

        assert captured
        actions_arg, strategy_id_arg = captured[0]
        assert strategy_id_arg == 'strat_a'
        assert actions_arg == [action]

    def test_action_submit_not_called_for_empty_actions(self) -> None:
        '''dispatch_timer returning [] does not invoke action_submit.'''

        runner = MagicMock(spec=StrategyRunner)

        dispatched = threading.Event()

        def track(*_args: Any, **_kwargs: Any) -> list:
            dispatched.set()
            return []

        runner.dispatch_timer.side_effect = track
        submitter = MagicMock()

        timers = {'strat_a': (TimerSpec(timer_id='check', interval_seconds=1),)}

        loop = TimerLoop(
            runner=runner,
            strategy_timers=timers,
            context_provider=_mock_context_provider,
            action_submit=submitter,
        )

        loop.start()
        dispatched.wait(timeout=3)
        loop.stop()

        assert submitter.call_count == 0

    def test_action_submit_exception_does_not_kill_loop(self) -> None:
        '''Submitter raising every tick still leaves the loop scheduling future ticks.'''

        runner = MagicMock(spec=StrategyRunner)
        runner.dispatch_timer.return_value = [
            Action(action_type=ActionType.ABORT, command_id='cmd_x'),
        ]

        call_count = threading.Event()
        calls = {'n': 0}

        def submitter(_actions: list[Action], _strategy_id: str) -> None:
            calls['n'] += 1
            if calls['n'] >= 2:
                call_count.set()
            raise RuntimeError('submitter blew up')

        timers = {'strat_a': (TimerSpec(timer_id='check', interval_seconds=1),)}

        loop = TimerLoop(
            runner=runner,
            strategy_timers=timers,
            context_provider=_mock_context_provider,
            action_submit=submitter,
        )

        loop.start()
        call_count.wait(timeout=5)
        loop.stop()

        assert calls['n'] >= 2
