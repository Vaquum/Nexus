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
from decimal import Decimal

from nexus.core.domain.enums import OperationalMode
from nexus.core.domain.instance_state import InstanceState
from nexus.core.domain.operational_mode import ModeState
from nexus.core.domain.risk_breaker_thresholds import RiskBreakerThresholds

__all__ = ['ModeController']

_HALTED = OperationalMode.HALTED
_HEALTH_TRIGGER = 'health'
_ZERO = Decimal('0')


def _utc_now() -> datetime:
    '''Return the current UTC time.'''

    return datetime.now(tz=timezone.utc)


class ModeController:
    '''The only sanctioned writer of `state.mode` and `state.mode_holds`.

    Args:
        state: Instance state whose mode and holds this controller owns.
        lock: Lock serialising mode and hold writes with state snapshots.
        clock: Source of UTC time for transition and hold timestamps.
        risk_thresholds: Limits the risk breakers evaluate each tick.
    '''

    def __init__(
        self,
        state: InstanceState,
        lock: threading.Lock,
        clock: Callable[[], datetime] = _utc_now,
        risk_thresholds: RiskBreakerThresholds | None = None,
    ) -> None:
        self._state = state
        self._lock = lock
        self._clock = clock
        self._health_mode = OperationalMode.ACTIVE
        self._risk_thresholds = risk_thresholds or RiskBreakerThresholds()

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

    def evaluate_risk(self) -> None:
        '''Trip or lift the risk breakers from the current RiskState.

        The daily-loss breaker sums the per-strategy 24h losses and
        auto-lifts when they decay back under the limit. The drawdown
        breaker trips on the lifetime-peak total drawdown and does not
        auto-lift, so a recovered mark cannot silently resume trading.
        '''

        with self._lock:
            self._evaluate_daily_loss_locked()
            self._evaluate_drawdown_locked()

    def reconcile(self) -> None:
        '''Re-derive the mode after a restart, before trading resumes.

        Seeds the health mode from a recovered health-driven mode so a
        health halt is not lifted, re-trips the risk breakers from the
        recovered RiskState, and recommits — so a halt that outlived a
        crash is in force before any startup actions drain.
        '''

        with self._lock:

            if self._state.mode.trigger == _HEALTH_TRIGGER:
                self._health_mode = self._state.mode.mode

            self._evaluate_daily_loss_locked()
            self._evaluate_drawdown_locked()
            self._recommit()

    def _evaluate_daily_loss_locked(self) -> None:
        limit = self._risk_thresholds.max_daily_loss

        if limit is None:
            return

        daily_loss = sum(
            (srs.rolling_loss_24h for srs in self._state.risk.per_strategy.values()),
            _ZERO,
        )

        if daily_loss >= limit:
            self._set_hold_locked('risk_daily_loss', f'24h loss {daily_loss} >= limit {limit}')

        else:
            self._clear_hold_locked('risk_daily_loss')

    def _evaluate_drawdown_locked(self) -> None:
        risk = self._state.risk
        limit_pct = self._risk_thresholds.max_drawdown_pct

        if limit_pct is not None and risk.max_total_drawdown_pct >= limit_pct:
            self._set_hold_locked(
                'risk_drawdown', f'drawdown {risk.max_total_drawdown_pct} >= limit {limit_pct}',
            )

        limit_abs = self._risk_thresholds.max_drawdown

        if limit_abs is not None and risk.max_total_drawdown >= limit_abs:
            self._set_hold_locked(
                'risk_drawdown', f'drawdown {risk.max_total_drawdown} >= limit {limit_abs}',
            )

    def _set_hold(self, name: str, reason: str) -> bool:
        with self._lock:
            return self._set_hold_locked(name, reason)

    def _clear_hold(self, name: str) -> bool:
        with self._lock:
            return self._clear_hold_locked(name)

    def _set_hold_locked(self, name: str, reason: str) -> bool:
        hold = getattr(self._state.mode_holds, name)

        if hold.active:
            return False

        hold.active = True
        hold.reason = reason
        hold.since = self._clock()

        return self._recommit()

    def _clear_hold_locked(self, name: str) -> bool:
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
