'''Arbitrate the instance operational mode against halt holds.

Owns `state.mode_holds` and writes `state.mode` on the
health-and-hold-arbitrated path: the mode is HALTED whenever any hold
is active or health itself is HALTED, and the health-derived mode
otherwise, so a healthy tick can never lift a hold.

The startup and shutdown sequencers still write `state.mode` directly
and are not yet routed through this controller; until they are, it is
the sole writer only on the health/hold path, not process-wide.
'''

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime, timezone

from nexus.core.domain.enums import OperationalMode
from nexus.core.domain.instance_state import InstanceState
from nexus.core.domain.operational_mode import ModeState
from nexus.core.domain.risk_breaker_thresholds import RiskBreakerThresholds

__all__ = ['ModeController']

_log = logging.getLogger(__name__)

_HALTED = OperationalMode.HALTED
_HEALTH_TRIGGER = 'health'


def _utc_now() -> datetime:
    '''Return the current UTC time.'''

    return datetime.now(tz=timezone.utc)


class ModeController:
    '''Write `state.mode` and own `state.mode_holds` on the health/hold path.

    Not yet the process-wide sole writer: `StartupSequencer` and
    `ShutdownSequencer` still set `state.mode` directly. Routing those
    through this controller is tracked as follow-on wiring.

    Args:
        state: Instance state whose mode and holds this controller owns.
        lock: Lock serialising mode and hold writes with state snapshots.
            Must not be the HealthLoop's own lock: the loop calls this
            controller while holding that lock and the controller
            re-acquires this one, so sharing a non-reentrant lock
            deadlocks.
        clock: Source of UTC time for transition and hold timestamps.
        risk_thresholds: Limits the risk breakers evaluate each tick.
        on_halt: Optional callback invoked with the halt source ('manual',
            'risk', or 'health') when the mode transitions to HALTED.
    '''

    def __init__(
        self,
        state: InstanceState,
        lock: threading.Lock,
        clock: Callable[[], datetime] = _utc_now,
        risk_thresholds: RiskBreakerThresholds | None = None,
        on_halt: Callable[[str], None] | None = None,
    ) -> None:
        self._state = state
        self._lock = lock
        self._clock = clock
        self._health_mode = OperationalMode.ACTIVE
        self._risk_thresholds = risk_thresholds or RiskBreakerThresholds()
        self._on_halt = on_halt
        self._pending_halt: str | None = None

    def apply_health_mode(self, health_mode: OperationalMode, notify: bool = True) -> bool:
        '''Record the latest health-derived mode and recommit the mode.

        Args:
            health_mode: Mode the health evaluator derived this tick.
            notify: Whether to fire a pending on-halt callback here. A caller
                holding its own lock passes False and drains later via
                `notify_pending_halt` once that lock is released.

        Returns:
            Whether the mode value changed; a trigger-only recompute
            returns False.
        '''

        with self._lock:
            self._health_mode = health_mode
            changed = self._recommit()

        if notify:
            self.notify_pending_halt()

        return changed

    def apply_health_and_risk(self, health_mode: OperationalMode, notify: bool = True) -> bool:
        '''Set the health mode and evaluate the risk breakers in one commit.

        The health tick uses this so the risk-breaker re-evaluation and the
        health-mode write land under a single hold of the lock. Splitting
        them into `evaluate_risk` then `apply_health_mode` releases the lock
        between the two, leaving a window where a reader taking this lock
        (but not the caller's) sees an intermediate mode the same tick then
        overwrites.

        Args:
            health_mode: Mode the health evaluator derived this tick.
            notify: Whether to fire a pending on-halt callback here. A caller
                holding its own lock passes False and drains later via
                `notify_pending_halt`.

        Returns:
            Whether the mode value changed; a trigger-only recompute
            returns False.
        '''

        with self._lock, self._state.risk.lock_cm():
            previous = self._state.mode.mode
            self._health_mode = health_mode
            self._evaluate_daily_loss_locked()
            self._evaluate_drawdown_locked()
            self._recommit()
            changed = self._state.mode.mode is not previous

        if notify:
            self.notify_pending_halt()

        return changed

    def set_manual_halt(self, reason: str) -> bool:
        '''Place the manual hold and recommit the mode.

        Returns:
            Whether the mode value changed; a trigger-only recompute
            returns False.
        '''

        return self._set_hold('manual_hold', reason)

    def clear_manual_halt(self) -> bool:
        '''Lift the manual hold and recommit; never writes ACTIVE directly.

        Returns:
            Whether the mode value changed; a trigger-only recompute
            returns False.
        '''

        return self._clear_hold('manual_hold')

    def set_daily_loss_halt(self, reason: str) -> bool:
        '''Place the daily-loss hold and recommit the mode.

        Returns:
            Whether the mode value changed; a trigger-only recompute
            returns False.
        '''

        return self._set_hold('risk_daily_loss', reason)

    def clear_daily_loss_halt(self) -> bool:
        '''Lift the daily-loss hold and recommit the mode.

        Returns:
            Whether the mode value changed; a trigger-only recompute
            returns False.
        '''

        return self._clear_hold('risk_daily_loss')

    def set_drawdown_halt(self, reason: str) -> bool:
        '''Place the drawdown hold and recommit the mode.

        Returns:
            Whether the mode value changed; a trigger-only recompute
            returns False.
        '''

        return self._set_hold('risk_drawdown', reason)

    def clear_drawdown_halt(self) -> bool:
        '''Lift the drawdown hold and recommit the mode.

        Returns:
            Whether the mode value changed; a trigger-only recompute
            returns False.
        '''

        return self._clear_hold('risk_drawdown')

    def evaluate_risk(self, notify: bool = True) -> None:
        '''Trip or lift the risk breakers from the current RiskState.

        The daily-loss breaker sums the per-strategy 24h losses and
        auto-lifts when they decay back under the limit. The drawdown
        breaker trips on the lifetime-peak total drawdown and does not
        auto-lift, so a recovered mark cannot silently resume trading.

        The RiskState reads run under `state.risk.lock_cm()` so the
        per-strategy iteration and drawdown reads see a consistent
        snapshot relative to concurrent OutcomeProcessor / MtmLoop
        writers (FINAL-MAJOR-02).

        Args:
            notify: Whether to fire a pending on-halt callback here. A caller
                holding its own lock passes False and drains later via
                `notify_pending_halt`.
        '''

        with self._lock, self._state.risk.lock_cm():
            self._evaluate_daily_loss_locked()
            self._evaluate_drawdown_locked()

        if notify:
            self.notify_pending_halt()

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

            with self._state.risk.lock_cm():
                self._evaluate_daily_loss_locked()
                self._evaluate_drawdown_locked()

            self._recommit()

        self.notify_pending_halt()

    def notify_pending_halt(self) -> None:
        '''Fire the on-halt callback outside the lock for a pending halt.

        Skips when the mode is no longer HALTED by the time it drains, so a
        halt lifted before the notifier ran does not raise a stale alert.
        '''

        with self._lock:
            source = self._pending_halt
            self._pending_halt = None
            still_halted = self._state.mode.mode is _HALTED

        if source is None or not still_halted or self._on_halt is None:
            return

        try:
            self._on_halt(source)
        except Exception:  # noqa: BLE001 - alerting must not break mode control
            _log.exception('mode-halt alert callback failed')

    def _evaluate_daily_loss_locked(self) -> None:
        limit = self._risk_thresholds.max_daily_loss

        if limit is None:
            return

        daily_loss = self._state.risk.rolling_loss_24h

        if daily_loss > limit:
            self._set_hold_locked('risk_daily_loss', f"24h loss {daily_loss} > limit {limit}")

        else:
            self._clear_hold_locked('risk_daily_loss')

    def _evaluate_drawdown_locked(self) -> None:
        risk = self._state.risk
        limit_pct = self._risk_thresholds.max_drawdown_pct

        if limit_pct is not None and risk.max_total_drawdown_pct > limit_pct:
            self._set_hold_locked(
                'risk_drawdown', f"drawdown {risk.max_total_drawdown_pct} > limit {limit_pct}",
            )

        limit_abs = self._risk_thresholds.max_drawdown

        if limit_abs is not None and risk.max_total_drawdown > limit_abs:
            self._set_hold_locked(
                'risk_drawdown', f"drawdown {risk.max_total_drawdown} > limit {limit_abs}",
            )

    def _set_hold(self, name: str, reason: str) -> bool:
        with self._lock:
            changed = self._set_hold_locked(name, reason)

        self.notify_pending_halt()

        return changed

    def _clear_hold(self, name: str) -> bool:
        with self._lock:
            changed = self._clear_hold_locked(name)

        self.notify_pending_halt()

        return changed

    def _set_hold_locked(self, name: str, reason: str) -> bool:
        hold = getattr(self._state.mode_holds, name)

        if hold.active:
            return self._recommit()

        hold.active = True
        hold.reason = reason
        hold.since = self._clock()

        return self._recommit()

    def _clear_hold_locked(self, name: str) -> bool:
        hold = getattr(self._state.mode_holds, name)

        if not hold.active:
            return self._recommit()

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

        if mode_changed and new_mode is _HALTED:
            self._pending_halt = source

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
