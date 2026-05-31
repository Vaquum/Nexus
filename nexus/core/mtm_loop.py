'''Periodic mark-to-market loop for open positions.

Sets `position.unrealized_pnl` on every open position from a
caller-provided mark-price source, then sums the result into
`state.risk.unrealized_pnl` via `RiskState.update_unrealized_pnl`
(which recomputes `equity`, `equity_hwm`, `total_drawdown`,
`total_drawdown_pct`, `unrealized_drawdown` deterministically).

Per-strategy attribution is preserved: each `Position` carries its
own `strategy_id`, so the loop also buckets per-position unrealized
P&L by `strategy_id` and writes the per-strategy aggregate into
`StrategyRiskState.strategy_unrealized_pnl`. Per-strategy risk gates
can therefore see per-strategy unrealized exposure, not just the
instance aggregate.

Without this loop, `Position.unrealized_pnl` is set on entry
(typically to 0) and never re-touched until close. `state.risk.equity`
/ `equity_hwm` / `total_drawdown` therefore run blind to open-book
P&L; risk gates that consult drawdown derivatives operate on
realized-only data and cannot react to an adverse mark move on the
open book until each position closes individually.
'''

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from decimal import Decimal

from nexus.core.domain.enums import OrderSide
from nexus.core.domain.instance_state import InstanceState
from nexus.core.domain.risk_state import StrategyRiskState

__all__ = ['MtmLoop']

_log = logging.getLogger(__name__)
_ZERO = Decimal(0)


class MtmLoop:
    '''Periodically mark `state.positions` to market.

    Args:
        state: The InstanceState whose `positions` are marked and whose
            `risk.unrealized_pnl` is updated each tick.
        mark_price_provider: Callable invoked once per position per
            tick. Receives the position's symbol and returns the current
            mark price as `Decimal`, or `None` to indicate the price is
            unavailable for that symbol on this tick. If `None` is
            returned for ANY open symbol, the entire tick is aborted
            (the loop does not partially update) and the existing
            `unrealized_pnl` values are retained — stale marks are
            preferred over inconsistent half-marked snapshots.
        interval_seconds: Seconds between ticks. Must be positive.
            Recommended 10-60s; default 30s. Smaller intervals tighten
            risk-gate responsiveness at the cost of more lock-acquire
            cycles per minute.
        positions_lock: Optional `threading.Lock` shared with
            OutcomeProcessor / PredictLoop / ShutdownSequencer (the
            same object stored at `state.risk.lock`). Held around the
            per-position write loop so positions cannot be added or
            removed mid-iteration. None falls back to `nullcontext()`
            for legacy single-threaded test paths.
    '''

    def __init__(
        self,
        state: InstanceState,
        mark_price_provider: Callable[[str], Decimal | None],
        interval_seconds: float = 30.0,
        positions_lock: threading.Lock | None = None,
    ) -> None:
        if (
            isinstance(interval_seconds, bool)
            or not isinstance(interval_seconds, (int, float))
            or interval_seconds <= 0
        ):
            msg = 'MtmLoop.interval_seconds must be a positive number'
            raise ValueError(msg)

        if positions_lock is not None and (
            not hasattr(state.risk, 'lock')
            or state.risk.lock is not positions_lock
        ):
            risk_lock = getattr(state.risk, 'lock', '<missing>')
            msg = (
                'MtmLoop requires `state.risk.lock is positions_lock` whenever '
                '`positions_lock` is supplied. The loop mutates '
                '`state.risk.per_strategy` (creates StrategyRiskState, writes '
                'strategy_unrealized_pnl) and calls `state.risk.update_unrealized_pnl` '
                '(which writes equity, equity_hwm, total_drawdown, max_drawdown, ...) '
                'while holding only positions_lock. The validator hot path '
                '(risk_stage.py → to_risk_check_metrics) reads those same fields '
                'under `state.risk.lock`, and OutcomeProcessor writes them under '
                '`state.risk.lock_cm()`. A non-identical lock would re-open the '
                'FINAL-MAJOR-02 `dictionary changed size during iteration` / '
                'lost-update race that the codebase closed. '
                f'Got positions_lock={positions_lock!r}, '
                f'state.risk.lock={risk_lock!r}.'
            )
            raise RuntimeError(msg)

        self._state = state
        self._mark_price_provider = mark_price_provider
        self._interval_seconds = float(interval_seconds)
        self._positions_lock = positions_lock
        self._timer: threading.Timer | None = None
        self._running = False
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        '''Whether the loop is currently scheduling ticks.'''

        return self._running

    def start(self) -> None:
        '''Start the periodic mark-to-market loop.'''

        with self._lock:
            if self._running:
                return

            self._running = True
            self._schedule_locked()

    def stop(self) -> None:
        '''Stop the loop and cancel any pending tick.'''

        with self._lock:
            self._running = False
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

    def tick_once(self) -> None:
        '''Run one mark-to-market pass without scheduling another tick.

        Useful for tests and for immediate-sync paths where the
        caller controls cadence. Bypasses the `_running` guard so
        callers driving ticks manually (without `start()`) still
        update unrealized P&L once.
        '''

        self._mark(respect_running=False)

    def _schedule_locked(self) -> None:
        if not self._running:
            return

        self._timer = threading.Timer(self._interval_seconds, self._tick)
        self._timer.daemon = True
        self._timer.start()

    def _tick(self) -> None:
        with self._lock:
            if not self._running:
                return

        self._mark(respect_running=True)

        with self._lock:
            self._schedule_locked()

    def _mark(self, respect_running: bool = True) -> None:
        '''Compute per-position unrealized P&L, aggregate, write under lock.

        Provider-side soft failures (`None` returned, non-finite mark
        returned) are logged at WARN and the tick is aborted without
        partial writes — existing `unrealized_pnl` values are retained,
        stale marks beat half-marked snapshots. An unexpected exception
        (provider raised, or math failed mid-loop) is logged at ERROR
        via `_log.exception` and the tick is aborted the same way. The
        next tick still fires either way.

        Args:
            respect_running: when False, the `_running` re-check is
                skipped — used by `tick_once()` for callers driving
                the loop manually without `start()`.
        '''

        positions_cm: AbstractContextManager[bool | None] = (
            self._positions_lock if self._positions_lock is not None else nullcontext()
        )

        try:
            with positions_cm:
                if respect_running:
                    with self._lock:
                        if not self._running:
                            return

                snapshot = list(self._state.positions.values())

                if not snapshot:
                    any_strategy_was_nonzero = any(
                        srs.strategy_unrealized_pnl != _ZERO
                        for srs in self._state.risk.per_strategy.values()
                    )

                    for srs in self._state.risk.per_strategy.values():
                        if srs.strategy_unrealized_pnl != _ZERO:
                            srs.strategy_unrealized_pnl = _ZERO

                    if self._state.risk.unrealized_pnl != _ZERO or any_strategy_was_nonzero:
                        self._state.risk.update_unrealized_pnl(_ZERO)

                    return

                marks: dict[str, Decimal] = {}
                for position in snapshot:
                    symbol = position.symbol
                    if symbol in marks:
                        continue

                    mark = self._mark_price_provider(symbol)
                    if mark is None:
                        _log.warning(
                            'MtmLoop: mark price unavailable; tick aborted',
                            extra={'symbol': symbol, 'positions': len(snapshot)},
                        )
                        return

                    if not isinstance(mark, Decimal) or not mark.is_finite():
                        _log.warning(
                            'MtmLoop: mark price is not a finite Decimal; tick aborted',
                            extra={'symbol': symbol, 'mark_repr': repr(mark)},
                        )
                        return

                    marks[symbol] = mark

                aggregate = _ZERO
                per_position: dict[str, Decimal] = {}
                per_strategy: dict[str, Decimal] = {}
                for position in snapshot:
                    mark = marks[position.symbol]
                    sign = Decimal(1) if position.side is OrderSide.BUY else Decimal(-1)
                    unrealized = (mark - position.entry_price) * position.size * sign
                    per_position[position.trade_id] = unrealized
                    per_strategy[position.strategy_id] = (
                        per_strategy.get(position.strategy_id, _ZERO) + unrealized
                    )
                    aggregate += unrealized

                for position in snapshot:
                    position.unrealized_pnl = per_position[position.trade_id]

                for strategy_id, unrealized in per_strategy.items():
                    existing = self._state.risk.per_strategy.get(strategy_id)
                    if existing is None:
                        new_srs = StrategyRiskState(strategy_id=strategy_id)
                        self._state.risk.per_strategy[strategy_id] = new_srs
                        existing = new_srs

                    existing.strategy_unrealized_pnl = unrealized

                for strategy_id, srs in self._state.risk.per_strategy.items():
                    if strategy_id not in per_strategy and srs.strategy_unrealized_pnl != _ZERO:
                        srs.strategy_unrealized_pnl = _ZERO

                self._state.risk.update_unrealized_pnl(aggregate)
        except Exception:  # noqa: BLE001 - mark failure must not abort the loop
            _log.exception('MtmLoop tick failed; next tick will retry')
