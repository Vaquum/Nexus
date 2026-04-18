'''Tests for TimerLoop strategy timer dispatch.'''

from __future__ import annotations

import threading
import time
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

from nexus.core.domain.enums import OperationalMode
from nexus.infrastructure.manifest import TimerSpec
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
