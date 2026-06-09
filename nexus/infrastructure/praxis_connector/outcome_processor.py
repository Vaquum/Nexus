'''OutcomeProcessor for routing TradeOutcomes to Capital Controller.

Routes inbound TradeOutcomes to appropriate Capital Controller lifecycle
methods and updates positions on fill outcomes.
'''

from __future__ import annotations

import logging
import threading
from contextlib import AbstractContextManager, nullcontext
from decimal import Decimal
from typing import Any

from nexus.core.capital_controller.capital_controller import CapitalController
from nexus.core.domain.enums import OrderSide
from nexus.core.domain.instance_state import InstanceState
from nexus.core.domain.risk_state import StrategyRiskState
from nexus.infrastructure.praxis_connector.order_context import OrderContext
from nexus.infrastructure.praxis_connector.process_result import ProcessResult
from nexus.infrastructure.praxis_connector.trade_outcome import TradeOutcome
from nexus.infrastructure.praxis_connector.trade_outcome_type import TradeOutcomeType
from nexus.infrastructure.state_store import StateStore
from nexus.infrastructure.strategy_event import StrategyEvent

__all__ = ['OutcomeProcessor']

_log = logging.getLogger(__name__)

_ZERO = Decimal(0)


class OutcomeProcessor:
    '''Routes TradeOutcomes to Capital Controller and updates positions.

    Args:
        capital_controller: Capital lifecycle manager.
        instance_state: Runtime state containing positions.
        state_store: Persistence facade for WAL and snapshots.
        positions_lock: Optional `threading.Lock` shared with the launcher's
            `_build_strategy_context` reader and `_ensure_entry_position`
            writer. When provided, every `state.positions` dict-size
            mutation AND every Position field assignment performed by
            this class (`_grow_position`, `_reduce_position`,
            `_clear_pending_exit`) wraps its writes under it so a
            concurrent PredictLoop / TimerLoop iteration of
            `state.positions.values()` cannot fire `RuntimeError:
            dictionary changed size during iteration` (silently swallowed
            by PredictLoop's top-level except → silently dropped strategy
            ticks) and cannot read torn field values. When None (legacy
            single-threaded test paths) no locking is performed.
    '''

    def __init__(
        self,
        capital_controller: CapitalController,
        instance_state: InstanceState,
        state_store: StateStore,
        positions_lock: threading.Lock | None = None,
    ) -> None:
        self._capital = capital_controller
        self._state = instance_state
        self._store = state_store
        self._positions_lock = positions_lock
        self._positions_cm: AbstractContextManager[Any] = (
            positions_lock if positions_lock is not None else nullcontext()
        )
        self._processed_outcome_ids: set[str] = set()
        self._processed_dust_close_ids: set[str] = set()
        self._dedup_lock = threading.Lock()

    def process(
        self,
        outcome: TradeOutcome,
        context: OrderContext,
    ) -> ProcessResult:
        '''Process a TradeOutcome and update state accordingly.

        Contract — `outcome.command_id` ↔ `CapitalController.send_order`:
            Every ACK / FILL / REJECT / CANCEL handled here looks up
            `self._capital._orders[outcome.command_id]`. That dict is
            populated only by `CapitalController.send_order(reservation_id,
            order_id)`, where `order_id == outcome.command_id`. The
            launcher is responsible for calling `send_order` for every
            SUBMITTED action (use `nexus.strategy.action_submit.bridge_to_capital`
            to centralise the wiring). Renaming `outcome.command_id`
            without renaming `send_order(order_id=...)` (or vice
            versa) silently breaks the round trip — every outcome
            returns `INVARIANT_BREACH: order not found` and capital
            stays parked in `reservation_notional`.

        Idempotency (round-18 MAJOR-004): if an outcome with the same
        `outcome.outcome_id` has already been processed successfully,
        return success without mutating state. The Praxis side may
        retry delivery on transient errors (e.g., a failed
        `OutcomeAcked` append) and replay un-acked outcomes from
        EventSpine at boot; both paths can drive `process` with the
        same outcome more than once. A previously failed attempt does
        NOT poison the dedup set — the caller may legitimately retry
        and the second attempt runs normally.

        Args:
            outcome: Inbound outcome from Trading sub-system.
            context: Metadata for outcome processing.

        Returns:
            ProcessResult indicating success and what was updated.
            On dedup hit, `position_updated` and `capital_updated` are
            False so the launcher does not re-trigger
            `state_store.append_mutation`.
        '''

        with self._dedup_lock:
            if outcome.outcome_id in self._processed_outcome_ids:
                _log.debug(
                    'duplicate outcome dropped: outcome_id=%s command_id=%s',
                    outcome.outcome_id,
                    outcome.command_id,
                )
                return ProcessResult(
                    success=True,
                    outcome_type=outcome.outcome_type,
                )

        if context.command_id != outcome.command_id:
            return ProcessResult(
                success=False,
                outcome_type=outcome.outcome_type,
                error_reason=(
                    f'context.command_id {context.command_id!r} does not match '
                    f'outcome.command_id {outcome.command_id!r}'
                ),
            )

        if outcome.outcome_type == TradeOutcomeType.ACK:
            result = self._handle_ack(outcome)
        elif outcome.outcome_type in (
            TradeOutcomeType.PARTIAL, TradeOutcomeType.FILLED,
        ):
            result = self._handle_fill(outcome, context)
        elif outcome.outcome_type == TradeOutcomeType.REJECTED:
            result = self._handle_reject(outcome, context)
        else:
            result = self._handle_cancel(outcome, context)

        if result.success:
            with self._dedup_lock:
                self._processed_outcome_ids.add(outcome.outcome_id)

        return result

    def _handle_ack(self, outcome: TradeOutcome) -> ProcessResult:
        result = self._capital.order_ack(outcome.command_id)

        if not result.success:
            return ProcessResult(
                success=False,
                outcome_type=outcome.outcome_type,
                error_reason=f'order_ack failed: {result.reason}',
            )

        return ProcessResult(
            success=True,
            outcome_type=outcome.outcome_type,
            capital_updated=True,
        )

    def _handle_fill(
        self,
        outcome: TradeOutcome,
        context: OrderContext,
    ) -> ProcessResult:
        assert outcome.fill_notional is not None
        assert outcome.actual_fees is not None
        assert outcome.fill_size is not None
        assert outcome.fill_price is not None

        capital_updated = False

        if context.is_entry:
            result = self._capital.order_fill(
                outcome.command_id,
                outcome.fill_notional,
                outcome.actual_fees,
                terminal=outcome.outcome_type == TradeOutcomeType.FILLED,
            )

            if not result.success:
                return ProcessResult(
                    success=False,
                    outcome_type=outcome.outcome_type,
                    error_reason=f'order_fill failed: {result.reason}',
                )

            capital_updated = True

        else:
            cost_basis_released = self._compute_exit_cost_basis(outcome, context)
            if cost_basis_released is not None:
                exit_result = self._capital.order_exit(
                    context.strategy_id,
                    cost_basis_released,
                )
                if not exit_result.success:
                    return ProcessResult(
                        success=False,
                        outcome_type=outcome.outcome_type,
                        error_reason=f'order_exit failed: {exit_result.reason}',
                    )
                capital_updated = True

        position_updated, realized_pnl = (
            self._update_position_on_fill(outcome, context)
        )

        if realized_pnl is not None:
            event = StrategyEvent(
                strategy_id=context.strategy_id,
                event_type='trade_outcome',
                realized_pnl=realized_pnl,
                timestamp=outcome.timestamp,
                outcome_id=outcome.outcome_id,
            )
            with self._state.risk.lock_cm():
                # PR #55 round-10 review: WAL append FIRST under the
                # held risk.lock, then in-memory mutation. Pre-fix the
                # order was mutate-then-append; if append_event raised
                # (transient I/O / WAL validation failure), the
                # per-strategy and cumulative risk fields had already
                # been mutated but the event was never persisted —
                # `refresh_rolling_losses` only rebuilds rolling-loss
                # windows, NOT strategy_realized_pnl /
                # cumulative_realized_pnl, so the in-memory risk state
                # would stay inconsistent until restart.
                # Post-fix: append raises → no in-memory change, caller
                # sees the exception, in-memory + WAL stay in sync.
                # The single risk.lock acquisition still closes the
                # round-7 race (refresher cannot interleave between
                # append and in-memory mutation because it would block
                # on the lock).
                self._store.append_event(event)
                self._update_strategy_risk_state_locked(
                    context.strategy_id, realized_pnl,
                )
                self._state.risk.update_cumulative_realized_pnl(
                    self._state.risk.realized_pnl,
                )

        return ProcessResult(
            success=True,
            outcome_type=outcome.outcome_type,
            position_updated=position_updated,
            capital_updated=capital_updated,
        )

    def _handle_reject(
        self,
        outcome: TradeOutcome,
        context: OrderContext,
    ) -> ProcessResult:
        result = self._capital.order_reject(outcome.command_id)

        position_updated = False
        if context.is_exit:
            assert context.order_size is not None
            position_updated = self._clear_pending_exit(context, context.order_size)

        if not result.success and not context.is_exit:
            return ProcessResult(
                success=False,
                outcome_type=outcome.outcome_type,
                error_reason=f'order_reject failed: {result.reason}',
            )

        return ProcessResult(
            success=True,
            outcome_type=outcome.outcome_type,
            position_updated=position_updated,
            capital_updated=result.success,
        )

    def _handle_cancel(
        self,
        outcome: TradeOutcome,
        context: OrderContext,
    ) -> ProcessResult:
        result = self._capital.order_cancel(outcome.command_id)

        position_updated = False
        if context.is_exit:
            assert context.order_size is not None
            clear_size = min(
                outcome.remaining_size
                if outcome.remaining_size is not None
                else context.order_size,
                context.order_size,
            )
            position_updated = self._clear_pending_exit(context, clear_size)

        if not result.success and not context.is_exit:
            return ProcessResult(
                success=False,
                outcome_type=outcome.outcome_type,
                error_reason=f'order_cancel failed: {result.reason}',
            )

        return ProcessResult(
            success=True,
            outcome_type=outcome.outcome_type,
            position_updated=position_updated,
            capital_updated=result.success,
        )

    def _compute_exit_cost_basis(  # noqa: PLR0911
        self,
        outcome: TradeOutcome,
        context: OrderContext,
    ) -> Decimal | None:
        '''Compute cost-basis-released for an EXIT fill before any mutation.

        Reads `position.avg_cost_basis` while the position is still in
        `state.positions` so `_handle_fill` can call
        `CapitalController.order_exit` BEFORE `_reduce_position` mutates
        or removes the position. The pre-mutation order matters: if
        `order_exit` returns `INVARIANT_BREACH` (cost basis would drive
        `position_notional` or `per_strategy_deployed[strategy_id]`
        negative), the function returns `success=False` BEFORE the
        position is touched. Pre-fix the position was already
        reduced/deleted at the time of the breach, leaving capital
        aggregates and position state divergent and unrecoverable.

        Current `StartupSequencer._reconcile_capital` rebuilds
        `position_notional` from `qty * nexus_pos.avg_cost_basis` for
        positions present in both Nexus and Praxis (with a Praxis-price
        fallback that also persists onto `nexus_pos.avg_cost_basis`),
        and `_import_praxis_position` initializes Praxis-only imports
        with `avg_cost_basis = price` so the imported notional matches
        what later EXITs decrement. INVARIANT_BREACH on the
        `position_notional` aggregate is therefore unlikely on the
        post-fix reconcile path. The guard still fires on the
        per-strategy bucket — `per_strategy_deployed[strategy_id]`
        can drift from `position_notional` when an unpersisted
        non-fill terminal releases capital that the next snapshot does
        not see (current `append_mutation` is gated on
        `position_updated` only, so ACK / non-fill REJECT / non-fill
        CANCEL aggregate releases are not durable until the next
        position-updating fill).

        Returns None only when the EXIT fill should not trigger a
        capital decrement. That includes non-EXIT fills,
        `avg_cost_basis == 0` (placeholder reused as real, legacy
        snapshot pre-`avg_cost_basis`, or invariant break in
        `_grow_position` — `_reduce_position` logs a WARNING in this
        case), `fill_size > position.size` (overfill — letting
        `order_exit` decrement capital before `_reduce_position` raises
        would leave `CapitalState` inconsistent with `positions`), and
        `position.strategy_id != context.strategy_id` (cross-strategy
        attribution mismatch — decrementing the wrong
        `per_strategy_deployed` bucket would create attribution drift /
        underflow on the owning strategy).

        A missing `trade_id` or absent position also yields None from
        this helper, but those are EXIT-flow invariant violations:
        this method suppresses the capital decrement only, and the
        subsequent `_update_position_on_fill` will still raise
        `RuntimeError` in `_reduce_position` for them.
        '''

        assert outcome.fill_size is not None

        if context.is_entry:
            return None
        if context.trade_id is None:
            return None
        position = self._state.positions.get(context.trade_id)
        if position is None:
            return None
        if position.avg_cost_basis == _ZERO:
            return None
        if outcome.fill_size > position.size:
            return None
        if position.strategy_id != context.strategy_id:
            _log.warning(
                'EXIT fill strategy_id mismatch; capital decrement '
                'skipped to avoid wrong-bucket attribution',
                extra={
                    'command_id': outcome.command_id,
                    'trade_id': context.trade_id,
                    'context_strategy_id': context.strategy_id,
                    'position_strategy_id': position.strategy_id,
                },
            )
            return None
        return position.avg_cost_basis * outcome.fill_size

    def _update_position_on_fill(
        self,
        outcome: TradeOutcome,
        context: OrderContext,
    ) -> tuple[bool, Decimal | None]:
        assert outcome.fill_size is not None
        assert outcome.fill_price is not None

        if context.is_entry:
            return self._grow_position(outcome, context), None

        return self._reduce_position(outcome, context)

    def _grow_position(
        self,
        outcome: TradeOutcome,
        context: OrderContext,
    ) -> bool:
        assert outcome.fill_size is not None
        assert outcome.fill_price is not None
        assert outcome.fill_notional is not None
        assert outcome.actual_fees is not None

        if context.trade_id is None:
            msg = f'entry fill without trade_id: command_id={outcome.command_id!r}'
            raise RuntimeError(msg)

        position = self._state.positions.get(context.trade_id)

        if position is None:
            msg = f'entry fill for missing position: trade_id={context.trade_id!r}'
            raise RuntimeError(msg)

        old_size = position.size
        fill_size = outcome.fill_size
        fill_price = outcome.fill_price
        fill_notional = outcome.fill_notional
        actual_fees = outcome.actual_fees

        new_size = old_size + fill_size
        new_entry_price = (
            old_size * position.entry_price + fill_size * fill_price
        ) / new_size
        new_avg_cost_basis = (
            old_size * position.avg_cost_basis + fill_notional + actual_fees
        ) / new_size

        with self._positions_cm:
            position.size = new_size
            position.entry_price = new_entry_price
            position.avg_cost_basis = new_avg_cost_basis

        return True

    def _reduce_position(
        self,
        outcome: TradeOutcome,
        context: OrderContext,
    ) -> tuple[bool, Decimal | None]:
        assert outcome.fill_size is not None
        assert outcome.fill_price is not None
        assert outcome.actual_fees is not None

        if context.trade_id is None:
            msg = f'exit fill without trade_id: command_id={outcome.command_id!r}'
            raise RuntimeError(msg)

        position = self._state.positions.get(context.trade_id)

        if position is None:
            msg = f'exit fill for missing position: trade_id={context.trade_id!r}'
            raise RuntimeError(msg)

        fill_size = outcome.fill_size
        fill_price = outcome.fill_price

        if fill_size > position.size:
            msg = (
                f'exit fill_size {fill_size} exceeds position size '
                f'{position.size}: trade_id={context.trade_id!r}'
            )
            raise RuntimeError(msg)

        entry_price = position.entry_price
        side_multiplier = Decimal(-1) if position.side == OrderSide.SELL else Decimal(1)
        gross_pnl = side_multiplier * (fill_price - entry_price) * fill_size
        # FINAL-MAJOR-08: subtract exit fees so realized_pnl is NET, not GROSS.
        # The net value flows verbatim into strategy_realized_pnl,
        # cumulative_realized_pnl, equity, equity_hwm, and the rolling-loss
        # windows; rolling-loss / drawdown gates fire at the correct net-PnL
        # threshold instead of LATER by the cumulative fee total. On testnet
        # `fee_rate=0` so this is a no-op; mainnet-fatal once fee_rate flips
        # non-zero (couples with Praxis TD-030).
        realized_pnl = gross_pnl - outcome.actual_fees

        if position.avg_cost_basis == _ZERO:
            _log.warning(
                'exit fill on position with avg_cost_basis=0; capital '
                'decrement was skipped by `_handle_fill`. Position grew '
                'through a path that did not populate avg_cost_basis '
                '(legacy snapshot, placeholder reused as real, or '
                'invariant break in _grow_position) — capital aggregates '
                'will leak by the cost basis of this fill until '
                'avg_cost_basis is repopulated by a later entry fill or '
                'the position is rehydrated with a non-zero cost basis '
                'during boot/import recovery (reconcile_at_boot can no '
                'longer recover this case; it now rebuilds from '
                'pos.avg_cost_basis directly)',
                extra={
                    'command_id': outcome.command_id,
                    'trade_id': context.trade_id,
                    'fill_size': str(fill_size),
                    'position_size': str(position.size),
                    'position_entry_price': str(entry_price),
                    'position_avg_cost_basis': str(position.avg_cost_basis),
                },
            )

        with self._positions_cm:
            position.size = position.size - fill_size
            position.pending_exit = max(_ZERO, position.pending_exit - fill_size)

            if (
                outcome.outcome_type == TradeOutcomeType.FILLED
                and context.intended_full_close
                and position.size > _ZERO
            ):
                residue = position.size
                self._state.account_dust[position.symbol] = (
                    self._state.account_dust.get(position.symbol, _ZERO) + residue
                )
                position.size = _ZERO
                _log.info(
                    'sub-lot residue moved to account_dust after full-close EXIT fill',
                    extra={
                        'command_id': outcome.command_id,
                        'trade_id': context.trade_id,
                        'symbol': position.symbol,
                        'residue_qty': str(residue),
                        'account_dust_total': str(
                            self._state.account_dust[position.symbol],
                        ),
                    },
                )

            if position.is_closed:
                del self._state.positions[context.trade_id]

        return True, realized_pnl

    def _clear_pending_exit(self, context: OrderContext, size: Decimal) -> bool:
        if context.trade_id is None:
            msg = f'clear pending exit without trade_id: command_id={context.command_id!r}'
            raise RuntimeError(msg)

        with self._positions_cm:
            position = self._state.positions.get(context.trade_id)

            if position is None:
                return False

            position.pending_exit = max(_ZERO, position.pending_exit - size)

        return True

    def close_as_dust(
        self,
        trade_id: str,
        reason: str,
        dust_close_id: str,
    ) -> bool:
        '''Close a position as dust without venue interaction.

        Called by the Praxis launcher when intake-time venue quantization
        rejects a full-close EXIT as sub-lot (the position is so small
        that even the lot-snapped exit qty falls below `LOT_SIZE.minQty`).
        Removes the position from `state.positions` and adds the residual
        `position.size` to `state.account_dust[position.symbol]` so the
        base-asset residue stays accounted for. Unlike the post-fill
        residue path in `_reduce_position`, no venue order ever
        existed — no `TradeClosed` / `TradeOutcomeProduced` is emitted
        here.

        Idempotent on `dust_close_id`. Replay from WAL or a launcher
        retry with the same id is a no-op.

        Args:
            trade_id: Position identifier to close. If the position is
                not present in `state.positions` (already closed via
                replay or a prior call), returns `False` without raising.
            reason: Free-form reason string for the audit log
                (e.g., `'INTAKE_BELOW_MIN_QTY qty=0.00000842 lot_min=0.00001'`).
            dust_close_id: Deterministic dedup key. Convention is
                `f'dust-{command_id}'` where `command_id` is the EXIT
                action's command id at the launcher; that keeps the
                key stable across WAL replay.

        Returns:
            True if the position was found and closed as dust;
            False on dedup hit or missing position.
        '''

        if not isinstance(trade_id, str) or not trade_id.strip():
            msg = 'close_as_dust.trade_id must be a non-empty string'
            raise ValueError(msg)

        if not isinstance(reason, str) or not reason.strip():
            msg = 'close_as_dust.reason must be a non-empty string'
            raise ValueError(msg)

        if not isinstance(dust_close_id, str) or not dust_close_id.strip():
            msg = 'close_as_dust.dust_close_id must be a non-empty string'
            raise ValueError(msg)

        with self._dedup_lock:

            if dust_close_id in self._processed_dust_close_ids:
                _log.debug(
                    'duplicate dust close dropped: dust_close_id=%s trade_id=%s',
                    dust_close_id,
                    trade_id,
                )
                return False

        with self._positions_cm:
            position = self._state.positions.get(trade_id)

            if position is None:
                _log.debug(
                    'dust close for missing position; treating as already-closed',
                    extra={'trade_id': trade_id, 'dust_close_id': dust_close_id},
                )

                with self._dedup_lock:
                    self._processed_dust_close_ids.add(dust_close_id)

                return False

            residue = position.size
            symbol = position.symbol
            self._state.account_dust[symbol] = (
                self._state.account_dust.get(symbol, _ZERO) + residue
            )
            position.size = _ZERO
            del self._state.positions[trade_id]

            with self._dedup_lock:
                self._processed_dust_close_ids.add(dust_close_id)

        _log.info(
            'position closed as dust (no venue order)',
            extra={
                'trade_id': trade_id,
                'symbol': symbol,
                'residue_qty': str(residue),
                'account_dust_total': str(self._state.account_dust[symbol]),
                'reason': reason,
                'dust_close_id': dust_close_id,
            },
        )

        return True

    def _update_strategy_risk_state(
        self,
        strategy_id: str,
        realized_pnl: Decimal,
    ) -> None:
        '''Lock-acquiring wrapper around `_update_strategy_risk_state_locked`.

        Used by tests and any caller that wants the legacy "this
        function handles its own locking" contract. The hot path in
        `_handle_fill` calls `_update_strategy_risk_state_locked`
        directly under an already-held lock so the WAL append at the
        end of the same critical section is race-free against
        `refresh_rolling_losses`.

        Args:
            strategy_id: Strategy that realized the P&L.
            realized_pnl: P&L from exit fill (negative for losses).
        '''

        with self._state.risk.lock_cm():
            self._update_strategy_risk_state_locked(strategy_id, realized_pnl)

    def _update_strategy_risk_state_locked(
        self,
        strategy_id: str,
        realized_pnl: Decimal,
    ) -> None:
        '''Update per-strategy risk metrics after an exit fill.

        Caller MUST hold `state.risk.lock` (`threading.Lock` is non-
        reentrant, so this method does NOT re-acquire it). Per PR #55
        round-7, callers in `_handle_fill` hold the risk lock across
        the per-strategy update + `update_cumulative_realized_pnl` +
        `append_event` so a concurrent `refresh_rolling_losses` cannot
        observe a window where the WAL is missing the new event but
        `per_strategy` already reflects it (and write back stale
        WAL-derived values that overwrite the just-applied loss).

        Gets or creates StrategyRiskState for strategy_id, increments
        strategy_realized_pnl, adds to rolling loss counters if loss,
        and updates high_water_mark.

        Args:
            strategy_id: Strategy that realized the P&L.
            realized_pnl: P&L from exit fill (negative for losses).
        '''

        strategy_state = self._state.risk.per_strategy.get(strategy_id)

        if strategy_state is None:
            strategy_state = StrategyRiskState(strategy_id=strategy_id)
            self._state.risk.per_strategy[strategy_id] = strategy_state

        strategy_state.strategy_realized_pnl += realized_pnl

        if realized_pnl < _ZERO:
            loss = abs(realized_pnl)
            strategy_state.rolling_loss_24h += loss
            strategy_state.rolling_loss_7d += loss
            strategy_state.rolling_loss_30d += loss

        strategy_state.high_water_mark = max(
            strategy_state.high_water_mark,
            strategy_state.strategy_realized_pnl,
        )
