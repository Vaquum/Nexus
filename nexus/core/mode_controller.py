'''Single writer of instance operational mode.

Arbitrates the health-derived mode against the sticky manual and risk
holds so a healthy tick can never lift a hold. The mode is
HALTED whenever any hold is active or health itself is HALTED, and the
health-derived mode otherwise.
'''

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime, timezone

from nexus.core.domain.enums import OperationalMode
from nexus.core.domain.instance_state import InstanceState
from nexus.core.domain.operational_mode import ModeState

__all__ = ['ModeController']

_HALTED = OperationalMode.HALTED


def _utc_now() -> datetime:
    '''Return the current UTC time.'''

    return datetime.now(tz=timezone.utc)


class ModeController:
    '''The only sanctioned writer of `state.mode` and `state.mode_holds`.

    Args:
        state: Instance state whose mode and holds this controller owns.
        lock: Lock serialising mode and hold writes with state snapshots.
        clock: Source of UTC time for transition and hold timestamps.
    '''

    def __init__(
        self,
        state: InstanceState,
        lock: threading.Lock,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._state = state
        self._lock = lock
        self._clock = clock
        self._health_mode = OperationalMode.ACTIVE

    def apply_health_mode(self, health_mode: OperationalMode) -> bool:
        '''Record the latest health-derived mode and recommit the mode.

        Args:
            health_mode: Mode the health evaluator derived this tick.

        Returns:
            Whether the mode changed.
        '''

        with self._lock:
            self._health_mode = health_mode

            return self._recommit()

    def set_manual_halt(self, reason: str) -> bool:
        '''Place the manual hold and recommit the mode.'''

        return self._set_hold('manual_hold', reason)

    def clear_manual_halt(self) -> bool:
        '''Lift the manual hold and recommit; never writes ACTIVE directly.'''

        return self._clear_hold('manual_hold')

    def set_daily_loss_halt(self, reason: str) -> bool:
        '''Place the daily-loss hold and recommit the mode.'''

        return self._set_hold('risk_daily_loss', reason)

    def clear_daily_loss_halt(self) -> bool:
        '''Lift the daily-loss hold and recommit the mode.'''

        return self._clear_hold('risk_daily_loss')

    def set_drawdown_halt(self, reason: str) -> bool:
        '''Place the drawdown hold and recommit the mode.'''

        return self._set_hold('risk_drawdown', reason)

    def clear_drawdown_halt(self) -> bool:
        '''Lift the drawdown hold and recommit the mode.'''

        return self._clear_hold('risk_drawdown')

    def _set_hold(self, name: str, reason: str) -> bool:
        with self._lock:
            hold = getattr(self._state.mode_holds, name)

            if hold.active:
                return False

            hold.active = True
            hold.reason = reason
            hold.since = self._clock()

            return self._recommit()

    def _clear_hold(self, name: str) -> bool:
        with self._lock:
            hold = getattr(self._state.mode_holds, name)

            if not hold.active:
                return False

            hold.active = False
            hold.reason = ''
            hold.since = None

            return self._recommit()

    def _recommit(self) -> bool:
        holds = self._state.mode_holds
        halted = holds.any_active() or self._health_mode is _HALTED
        new_mode = _HALTED if halted else self._health_mode

        source = self._mode_source(new_mode)
        mode_changed = new_mode is not self._state.mode.mode

        if not mode_changed and source == self._state.mode.trigger:
            return False

        transitioned_at = (
            self._clock() if mode_changed else self._state.mode.transitioned_at
        )
        self._state.mode = ModeState(
            mode=new_mode,
            trigger=source,
            transitioned_at=transitioned_at,
        )

        return mode_changed

    def _mode_source(self, mode: OperationalMode) -> str:
        '''Return which input drives the mode: manual, risk, or health.'''

        holds = self._state.mode_holds

        if mode is _HALTED:

            if holds.manual_hold.active:
                return 'manual'

            if holds.risk_daily_loss.active or holds.risk_drawdown.active:
                return 'risk'

        return 'health'
