'''Atomic check-and-reserve capital controller.

Guards CapitalState mutations behind a threading lock to prevent
TOCTOU races when multiple strategies compete for the same pool.
'''

from __future__ import annotations

import heapq
import logging
import threading
import uuid
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from nexus.core.capital_controller.lifecycle_result import (
    FailureCategory,
    LifecycleResult,
)
from nexus.core.capital_controller.reservation import (
    Reservation,
    ReservationResult,
)
from nexus.core.capital_controller.tracked_order import (
    OrderLifecycleState,
    TrackedOrder,
)
from nexus.core.domain.capital_state import CapitalState
from nexus.core.domain.position import Position

__all__ = ['CapitalController']

_logger = logging.getLogger(__name__)

MAX_ALLOCATION_PER_TRADE_PCT = Decimal('0.15')
MAX_CAPITAL_UTILIZATION_PCT = Decimal('0.80')
DEFAULT_TTL_SECONDS = 60

_ZERO = Decimal(0)
_ONE_HUNDRED = Decimal('100')


class CapitalController:
    '''Thread-safe capital reservation manager.

    Args:
        capital_state: Mutable capital state to guard.
        max_allocation_per_trade_pct: Maximum fraction of `capital_pool`
            a single order may consume. Defaults to
            `MAX_ALLOCATION_PER_TRADE_PCT`. Operators that run higher-Kelly
            strategies override this at construction; the default
            preserves prior behaviour bit-for-bit.
    '''

    def __init__(
        self,
        capital_state: CapitalState,
        *,
        max_allocation_per_trade_pct: Decimal = MAX_ALLOCATION_PER_TRADE_PCT,
    ) -> None:
        if not isinstance(max_allocation_per_trade_pct, Decimal):
            msg = (
                f'max_allocation_per_trade_pct must be Decimal, '
                f'got {type(max_allocation_per_trade_pct).__name__}'
            )
            raise TypeError(msg)
        if max_allocation_per_trade_pct <= _ZERO:
            msg = (
                f'max_allocation_per_trade_pct must be > 0, '
                f'got {max_allocation_per_trade_pct}'
            )
            raise ValueError(msg)
        self._state = capital_state
        self._max_allocation_per_trade_pct = max_allocation_per_trade_pct
        self._lock = threading.Lock()
        self._reservations: dict[str, Reservation] = {}
        self._expiry_heap: list[tuple[datetime, str]] = []
        self._orders: dict[str, TrackedOrder] = {}

    def reconcile_at_boot(
        self,
        positions: Iterable[Position] | None = None,
    ) -> CapitalState:
        '''Reset stranded in-flight / working / reservation aggregates at boot.

        PT-FIX-35: `_reservations` and `_orders` are in-memory only —
        they are rebuilt fresh per `CapitalController` construction.
        The persisted `CapitalState` carries the AGGREGATE
        `reservation_notional`, `in_flight_order_notional`, and
        `working_order_notional` from the prior boot. After a clean
        shutdown those aggregates are zero (every command reached a
        terminal outcome via `_wait_terminal`), so this method is a
        no-op. After a crash / SIGKILL between `submit_command` and
        the venue ACK / FILL, the aggregates carry non-zero values
        with no corresponding tracked orders to release them via
        `order_ack` / `order_fill` / `order_reject` / `order_cancel`.

        The pragmatic recovery path: declare the in-flight from the
        prior boot lost — reset the aggregates to zero so capital is
        again available for the freshly-booting strategy. Tradeoff:
        if the venue still has stale orders open from the prior boot,
        the strategy may double-spend. For paper-trade testnet this
        is acceptable. A production deployment should pair this with
        a venue `query_open_orders` reconciliation pass.

        PT-FIX-41: when `positions` is provided, also rebuild
        `per_strategy_deployed` from the live positions so it sums
        to `position_notional` (the only non-zero aggregate after
        this method runs). Without this rebuild, the persisted
        per-strategy attribution still includes the pre-crash
        reservation/in-flight/working amounts; the next
        `check_and_reserve` then fires the
        `'Per-strategy deployed attribution mismatch for non-flat
        state'` denial and permanently blocks all new ENTERs.

        Callers that omit `positions` get only the aggregate reset
        (the original PT-FIX-35 behavior) — used by tests that don't
        need the per-strategy invariant rebuilt.

        Args:
            positions: live `Position`s recovered from snapshot/WAL,
                used to rebuild `per_strategy_deployed`. The launcher's
                `_build_nexus_runtime` passes
                `state.positions.values()` after `_reconcile_capital`
                settles `position_notional`.

        Returns:
            CapitalState: snapshot of the (possibly mutated) state for
                callers that want to log the change.
        '''

        with self._lock:
            if self._reservations or self._orders:
                msg = (
                    'reconcile_at_boot called after orders or reservations '
                    'were tracked; expected empty in-memory state at boot'
                )
                raise RuntimeError(msg)

            stranded_reservation = self._state.reservation_notional
            stranded_in_flight = self._state.in_flight_order_notional
            stranded_working = self._state.working_order_notional

            if stranded_reservation > _ZERO:
                _logger.warning(
                    'reconcile_at_boot resetting stranded reservation_notional: %s',
                    stranded_reservation,
                )
                self._state.reservation_notional = _ZERO

            if stranded_in_flight > _ZERO:
                _logger.warning(
                    'reconcile_at_boot resetting stranded in_flight_order_notional: %s',
                    stranded_in_flight,
                )
                self._state.in_flight_order_notional = _ZERO

            if stranded_working > _ZERO:
                _logger.warning(
                    'reconcile_at_boot resetting stranded working_order_notional: %s',
                    stranded_working,
                )
                self._state.working_order_notional = _ZERO

            if positions is not None:
                rebuilt: dict[str, Decimal] = {}
                for pos in positions:
                    if pos.pending_exit > _ZERO:
                        _logger.warning(
                            'reconcile_at_boot resetting stranded '
                            'pending_exit: trade_id=%s strategy_id=%s '
                            'size=%s pending_exit=%s — in-memory _orders '
                            'is empty post-boot so no in-flight EXIT '
                            'matches; the prior stuck value would deny '
                            'future EXITs via INTAKE_EXIT_SIZE_EXCEEDS_'
                            'REMAINING. Closes the shutdown REJECT/CANCEL/'
                            'EXPIRED leak (MAJOR-I) and the boot-orphan '
                            'REJECTED leak (MAJOR-R).',
                            pos.trade_id,
                            pos.strategy_id,
                            pos.size,
                            pos.pending_exit,
                        )
                        pos.pending_exit = _ZERO

                    cost_basis = pos.avg_cost_basis
                    if pos.size > _ZERO and cost_basis == _ZERO:
                        fallback_basis = pos.entry_price
                        if fallback_basis != _ZERO:
                            _logger.warning(
                                'reconcile_at_boot using entry_price '
                                'fallback for position with zero '
                                'avg_cost_basis: strategy_id=%s size=%s '
                                'entry_price=%s',
                                pos.strategy_id,
                                pos.size,
                                fallback_basis,
                            )
                            cost_basis = fallback_basis
                        else:
                            _logger.warning(
                                'reconcile_at_boot found non-flat position '
                                'with zero avg_cost_basis and zero '
                                'entry_price; deployed capital will be '
                                'understated: strategy_id=%s size=%s',
                                pos.strategy_id,
                                pos.size,
                            )
                    contribution = pos.size * cost_basis
                    rebuilt[pos.strategy_id] = (
                        rebuilt.get(pos.strategy_id, _ZERO) + contribution
                    )

                prior = dict(self._state.per_strategy_deployed)
                if prior != rebuilt:
                    _logger.warning(
                        'reconcile_at_boot rebuilding per_strategy_deployed '
                        'from positions: prior=%s, rebuilt=%s',
                        prior, rebuilt,
                    )
                self._state.per_strategy_deployed.clear()
                self._state.per_strategy_deployed.update(rebuilt)

            return self._state

    def check_and_reserve(
        self,
        strategy_id: str,
        order_notional: Decimal,
        estimated_fees: Decimal,
        strategy_budget: Decimal,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> ReservationResult:
        '''Atomically validate capital checks and create a reservation.

        Args:
            strategy_id: Which strategy is requesting capital.
            order_notional: Quote capital for the order.
            estimated_fees: Estimated transaction fees.
            strategy_budget: Budget ceiling for this strategy.
            ttl_seconds: Seconds before reservation auto-expires.

        Returns:
            ReservationResult with granted reservation or denial reason.
        '''

        for name, val in (
            ('order_notional', order_notional),
            ('estimated_fees', estimated_fees),
            ('strategy_budget', strategy_budget),
        ):
            if not isinstance(val, Decimal) or not val.is_finite():
                msg = f'Invalid {name}: {val}'
                raise ValueError(msg)

        if not strategy_id or not strategy_id.strip():
            msg = 'strategy_id must be a non-empty string'
            raise ValueError(msg)

        strategy_id = strategy_id.strip()

        if order_notional < _ZERO:
            msg = f'order_notional must be non-negative: {order_notional}'
            raise ValueError(msg)

        if estimated_fees < _ZERO:
            msg = f'estimated_fees must be non-negative: {estimated_fees}'
            raise ValueError(msg)

        if ttl_seconds <= 0:
            msg = f'ttl_seconds must be positive: {ttl_seconds}'
            raise ValueError(msg)

        total = order_notional + estimated_fees

        with self._lock:
            self._purge_expired()
            strategy_deployed = self._state.per_strategy_deployed.get(
                strategy_id, _ZERO
            )
            total_deployed = (
                self._state.position_notional
                + self._state.working_order_notional
                + self._state.in_flight_order_notional
                + self._state.reservation_notional
            )

            per_strategy_deployed = self._state.per_strategy_deployed
            denial_reason: str | None = None
            if total_deployed > _ZERO or per_strategy_deployed:
                if not per_strategy_deployed:
                    if total_deployed > _ZERO:
                        denial_reason = (
                            'Per-strategy deployed attribution is unknown for non-flat '
                            'state; reconcile strategy deployment before new reservations'
                        )
                else:
                    attributed_deployed = sum(per_strategy_deployed.values(), _ZERO)
                    if attributed_deployed != total_deployed:
                        if total_deployed > _ZERO:
                            denial_reason = (
                                'Per-strategy deployed attribution mismatch for non-flat '
                                'state; reconcile strategy deployment before new reservations'
                            )
                        else:
                            denial_reason = (
                                'Per-strategy deployed attribution mismatch for flat state; '
                                'reconcile strategy deployment before new reservations'
                            )

                if denial_reason is not None:
                    return ReservationResult(
                        granted=False,
                        denial_reason=denial_reason,
                    )

            allocation_pct = order_notional / self._state.capital_pool

            if allocation_pct > self._max_allocation_per_trade_pct:
                return ReservationResult(
                    granted=False,
                    denial_reason=(
                        f'Per-trade allocation {allocation_pct:.4f} exceeds '
                        f'limit {self._max_allocation_per_trade_pct}'
                    ),
                )

            if strategy_deployed + total > strategy_budget:
                return ReservationResult(
                    granted=False,
                    denial_reason=(
                        f'Strategy deployed {strategy_deployed} + order {total} '
                        f'exceeds budget {strategy_budget}'
                    ),
                )

            if self._state.available < total:
                return ReservationResult(
                    granted=False,
                    denial_reason=(
                        f'Insufficient available capital {self._state.available} '
                        f'for order {total}'
                    ),
                )

            utilization = (total_deployed + total) / self._state.capital_pool

            if utilization > MAX_CAPITAL_UTILIZATION_PCT:
                return ReservationResult(
                    granted=False,
                    denial_reason=(
                        f'Total utilization {utilization:.4f} exceeds '
                        f'limit {MAX_CAPITAL_UTILIZATION_PCT}'
                    ),
                )

            now = datetime.now(tz=timezone.utc)
            reservation = Reservation(
                reservation_id=str(uuid.uuid4()),
                strategy_id=strategy_id,
                notional=order_notional,
                estimated_fees=estimated_fees,
                created_at=now,
                expires_at=now + timedelta(seconds=ttl_seconds),
            )

            self._reservations[reservation.reservation_id] = reservation
            heapq.heappush(self._expiry_heap, (reservation.expires_at, reservation.reservation_id))
            self._state.reservation_notional += total
            self._adjust_strategy_deployed(strategy_id, total)

            return ReservationResult(granted=True, reservation=reservation)

    def compute_strategy_budget(
        self,
        strategy_id: str,
        capital_pct: Decimal,
        *,
        auto_compound: bool = False,
        strategy_realized_pnl: Decimal = _ZERO,
    ) -> Decimal:
        '''Compute strategy budget from capital pool and allocation percentage.

        Args:
            strategy_id: Strategy identifier for validation and diagnostics.
            capital_pct: Strategy allocation percentage in (0, 100].
            auto_compound: Whether to include realized PnL adjustment.
            strategy_realized_pnl: Realized PnL adjustment applied when
                auto_compound is enabled.

        Returns:
            Computed strategy budget in quote capital units.
        '''

        if not strategy_id or not strategy_id.strip():
            msg = 'strategy_id must be a non-empty string'
            raise ValueError(msg)

        strategy_id = strategy_id.strip()

        if not isinstance(capital_pct, Decimal) or not capital_pct.is_finite():
            msg = f'capital_pct must be a finite Decimal: {capital_pct}'
            raise ValueError(msg)

        if capital_pct <= _ZERO or capital_pct > _ONE_HUNDRED:
            msg = f'capital_pct must be in (0, 100]: {capital_pct}'
            raise ValueError(msg)

        base_budget = self._state.capital_pool * (capital_pct / _ONE_HUNDRED)

        if not auto_compound:
            return base_budget

        if (
            not isinstance(strategy_realized_pnl, Decimal)
            or not strategy_realized_pnl.is_finite()
        ):
            msg = (
                'strategy_realized_pnl must be a finite Decimal: '
                f'{strategy_realized_pnl}'
            )
            raise ValueError(msg)

        return base_budget + strategy_realized_pnl

    def _purge_expired(self, now: datetime | None = None) -> None:
        if now is None:
            now = datetime.now(tz=timezone.utc)

        while self._expiry_heap and self._expiry_heap[0][0] <= now:
            _, rid = heapq.heappop(self._expiry_heap)
            reservation = self._reservations.pop(rid, None)

            if reservation is None:
                continue

            self._state.reservation_notional -= reservation.total
            self._adjust_strategy_deployed(
                reservation.strategy_id,
                -reservation.total,
            )
            held_seconds = (now - reservation.created_at).total_seconds()
            _logger.warning(
                'Reservation expired: id=%s strategy=%s total=%s held=%.1fs',
                reservation.reservation_id,
                reservation.strategy_id,
                reservation.total,
                held_seconds,
            )

    def release_reservation(self, reservation_id: str) -> LifecycleResult:
        '''Release a reservation and return its capital to the available pool.

        Args:
            reservation_id: ID of the reservation to release.

        Returns:
            LifecycleResult with reason on failure.
        '''

        with self._lock:
            reservation = self._reservations.pop(reservation_id, None)

            if reservation is None:
                return LifecycleResult(
                    success=False,
                    reason=f'reservation {reservation_id!r} not found (expired or already released)',
                    category=FailureCategory.EXPECTED_MISS,
                )

            self._state.reservation_notional -= reservation.total
            self._adjust_strategy_deployed(
                reservation.strategy_id,
                -reservation.total,
            )
            return LifecycleResult(success=True)

    def send_order(self, reservation_id: str, order_id: str) -> LifecycleResult:
        '''Convert a reservation into an in-flight order.

        Consumes the reservation and creates a TrackedOrder in IN_FLIGHT state.
        Capital moves from reservation_notional to in_flight_order_notional.

        Args:
            reservation_id: ID of the reservation to consume.
            order_id: Venue order ID for tracking.

        Returns:
            LifecycleResult with reason on failure.
        '''

        if not order_id or not order_id.strip():
            msg = 'order_id must be a non-empty string'
            raise ValueError(msg)

        with self._lock:
            now = datetime.now(tz=timezone.utc)

            if order_id in self._orders:
                msg = f'order_id already tracked: {order_id}'
                raise ValueError(msg)

            reservation = self._reservations.pop(reservation_id, None)

            if reservation is None:
                self._purge_expired(now)
                return LifecycleResult(
                    success=False,
                    reason=f'reservation {reservation_id!r} not found (expired or already consumed)',
                    category=FailureCategory.EXPECTED_MISS,
                )

            if now > reservation.expires_at:
                self._state.reservation_notional -= reservation.total
                self._adjust_strategy_deployed(
                    reservation.strategy_id, -reservation.total,
                )
                self._purge_expired(now)
                return LifecycleResult(
                    success=False,
                    reason=(
                        f'reservation {reservation_id!r} expired at '
                        f'{reservation.expires_at.isoformat()} (now={now.isoformat()})'
                    ),
                    category=FailureCategory.EXPECTED_MISS,
                )

            self._purge_expired(now)

            order = TrackedOrder(
                order_id=order_id,
                reservation_id=reservation_id,
                strategy_id=reservation.strategy_id,
                notional=reservation.notional,
                estimated_fees=reservation.estimated_fees,
                remaining_notional=reservation.notional,
                state=OrderLifecycleState.IN_FLIGHT,
                created_at=now,
            )

            self._orders[order_id] = order
            self._state.reservation_notional -= reservation.total
            self._state.in_flight_order_notional += reservation.total

            return LifecycleResult(success=True)

    def order_ack(self, order_id: str) -> LifecycleResult:
        '''Acknowledge an in-flight order as working on venue.

        Transitions the order from IN_FLIGHT to WORKING state.
        Capital moves from in_flight_order_notional to working_order_notional.

        Args:
            order_id: ID of the order to acknowledge.

        Returns:
            LifecycleResult with reason on failure.
        '''

        with self._lock:
            order = self._orders.get(order_id)

            if order is None:
                return LifecycleResult(
                    success=False,
                    reason=f'order {order_id!r} not found',
                    category=FailureCategory.INVARIANT_BREACH,
                )

            if order.state != OrderLifecycleState.IN_FLIGHT:
                return LifecycleResult(
                    success=False,
                    reason=f'order {order_id!r} in {order.state.value}, expected IN_FLIGHT',
                    category=FailureCategory.INVARIANT_BREACH,
                )

            updated = TrackedOrder(
                order_id=order.order_id,
                reservation_id=order.reservation_id,
                strategy_id=order.strategy_id,
                notional=order.notional,
                estimated_fees=order.estimated_fees,
                remaining_notional=order.remaining_notional,
                state=OrderLifecycleState.WORKING,
                created_at=order.created_at,
            )

            self._orders[order_id] = updated
            self._state.in_flight_order_notional -= order.total
            self._state.working_order_notional += order.total

            return LifecycleResult(success=True)

    def order_reject(self, order_id: str) -> LifecycleResult:
        '''Handle venue rejection of an order.

        Removes the order and releases capital back to available.
        Accepts both IN_FLIGHT (the typical case — venue rejects at
        submission before ACK) AND WORKING (PT-FIX-40 — venue may
        queue a reject while an ACK is in transit, transitioning the
        tracked order to WORKING before the REJECTED arrives;
        without this branch the WORKING capital would stay parked
        permanently because TTL eviction only covers
        `_reservations`, not `_orders`).

        Args:
            order_id: ID of the rejected order.

        Returns:
            LifecycleResult with reason on failure.
        '''

        with self._lock:
            order = self._orders.get(order_id)

            if order is None:
                return LifecycleResult(
                    success=False,
                    reason=f'order {order_id!r} not found',
                    category=FailureCategory.INVARIANT_BREACH,
                )

            if order.state == OrderLifecycleState.IN_FLIGHT:
                self._state.in_flight_order_notional -= order.remaining_total
            elif order.state == OrderLifecycleState.WORKING:
                self._state.working_order_notional -= order.remaining_total
            else:
                return LifecycleResult(
                    success=False,
                    reason=(
                        f'order {order_id!r} in {order.state.value}, '
                        'expected IN_FLIGHT or WORKING'
                    ),
                    category=FailureCategory.INVARIANT_BREACH,
                )

            self._orders.pop(order_id)
            self._adjust_strategy_deployed(order.strategy_id, -order.remaining_total)

            return LifecycleResult(success=True)

    def order_exit(
        self,
        strategy_id: str,
        cost_basis_released: Decimal,
    ) -> LifecycleResult:
        '''Decrement capital aggregates by the cost basis of an EXIT FILL.

        Mirrors `order_fill` for the EXIT direction. Where `order_fill`
        increments `position_notional` and `per_strategy_deployed` by
        `fill_notional + actual_fees` (the cost basis added for an entry
        fill), `order_exit` decrements both aggregates by the cost basis
        released by an exit fill.

        The caller (`OutcomeProcessor._handle_fill` exit branch) computes
        `cost_basis_released = position.avg_cost_basis * fill_size`. The
        `Position.avg_cost_basis` field is the volume-weighted average
        cost per unit INCLUDING entry fees (maintained by `_grow_position`
        on every entry FILL), so the round-trip conservation holds:
        every entry FILL adds `fill_notional + actual_fees` to
        `position_notional`; the matching exit FILLs collectively remove
        the same amount.

        Exit fees are NOT touched here. Under the current model they
        are not represented in capital aggregates and are also NOT
        deducted from `realized_pnl` by `_reduce_position`, which uses
        a gross PnL calculation `(fill_price - entry_price) * fill_size`
        without subtracting `outcome.actual_fees`. If a future risk
        extension needs exit-fee tracking, add a separate ledger such
        as `realized_fees` and wire that accounting in explicitly.

        Args:
            strategy_id: Strategy whose deployed total is decremented.
            cost_basis_released: Cost basis of the closed portion in
                quote asset, computed by the caller as
                `position.avg_cost_basis * outcome.fill_size`. Must be
                positive.

        Returns:
            LifecycleResult with reason on failure. INVARIANT_BREACH
            when `cost_basis_released > position_notional` OR when
            `cost_basis_released > per_strategy_deployed[strategy_id]`
            (either would drive the corresponding aggregate negative).
        '''

        if not isinstance(cost_basis_released, Decimal) or not cost_basis_released.is_finite():
            msg = f'cost_basis_released must be a finite Decimal: {cost_basis_released}'
            raise ValueError(msg)

        if cost_basis_released <= _ZERO:
            msg = f'cost_basis_released must be positive: {cost_basis_released}'
            raise ValueError(msg)

        if not strategy_id or not strategy_id.strip():
            msg = 'strategy_id must be a non-empty string'
            raise ValueError(msg)

        strategy_id = strategy_id.strip()

        with self._lock:
            if cost_basis_released > self._state.position_notional:
                return LifecycleResult(
                    success=False,
                    reason=(
                        f'cost_basis_released {cost_basis_released} exceeds '
                        f'position_notional {self._state.position_notional}'
                    ),
                    category=FailureCategory.INVARIANT_BREACH,
                )

            strategy_deployed = self._state.per_strategy_deployed.get(
                strategy_id, _ZERO,
            )
            if cost_basis_released > strategy_deployed:
                return LifecycleResult(
                    success=False,
                    reason=(
                        f'cost_basis_released {cost_basis_released} exceeds '
                        f'per_strategy_deployed[{strategy_id}] {strategy_deployed}'
                    ),
                    category=FailureCategory.INVARIANT_BREACH,
                )

            self._state.position_notional -= cost_basis_released
            self._adjust_strategy_deployed(strategy_id, -cost_basis_released)

            return LifecycleResult(success=True)

    def order_fill(
        self,
        order_id: str,
        fill_notional: Decimal,
        actual_fees: Decimal,
    ) -> LifecycleResult:
        '''Handle a fill (partial or full) on a working order.

        Moves capital from working_order_notional to position_notional.
        Working decreases by estimated amount; position increases by actual
        cost (fill_notional + actual_fees). Fee variance is reconciled against
        fee_reserve: surplus adds, deficit draws.

        Args:
            order_id: ID of the filled order.
            fill_notional: Quote capital filled (excluding fees).
            actual_fees: Actual fees charged by venue for this fill.

        Returns:
            LifecycleResult with reason on failure.
        '''

        if not isinstance(fill_notional, Decimal) or not fill_notional.is_finite():
            msg = f'fill_notional must be a finite Decimal: {fill_notional}'
            raise ValueError(msg)

        if fill_notional <= _ZERO:
            msg = f'fill_notional must be positive: {fill_notional}'
            raise ValueError(msg)

        if not isinstance(actual_fees, Decimal) or not actual_fees.is_finite():
            msg = f'actual_fees must be a finite Decimal: {actual_fees}'
            raise ValueError(msg)

        if actual_fees < _ZERO:
            msg = f'actual_fees must be non-negative: {actual_fees}'
            raise ValueError(msg)

        with self._lock:
            order = self._orders.get(order_id)

            if order is None:
                return LifecycleResult(
                    success=False,
                    reason=f'order {order_id!r} not found',
                    category=FailureCategory.INVARIANT_BREACH,
                )

            if order.state != OrderLifecycleState.WORKING:
                return LifecycleResult(
                    success=False,
                    reason=f'order {order_id!r} in {order.state.value}, expected WORKING',
                    category=FailureCategory.INVARIANT_BREACH,
                )

            if fill_notional > order.remaining_notional:
                return LifecycleResult(
                    success=False,
                    reason=(
                        f'order {order_id!r} fill_notional {fill_notional} '
                        f'exceeds remaining {order.remaining_notional}'
                    ),
                    category=FailureCategory.INVARIANT_BREACH,
                )

            pre_fill_remaining = order.remaining_total
            new_remaining = order.remaining_notional - fill_notional

            if new_remaining == _ZERO:
                fill_with_estimated = pre_fill_remaining
                proportional_estimated = pre_fill_remaining - order.remaining_notional
            else:
                updated = TrackedOrder(
                    order_id=order.order_id,
                    reservation_id=order.reservation_id,
                    strategy_id=order.strategy_id,
                    notional=order.notional,
                    estimated_fees=order.estimated_fees,
                    remaining_notional=new_remaining,
                    state=OrderLifecycleState.WORKING,
                    created_at=order.created_at,
                )
                fill_with_estimated = pre_fill_remaining - updated.remaining_total
                proportional_estimated = fill_with_estimated - fill_notional

            fee_delta = proportional_estimated - actual_fees

            if fee_delta < _ZERO and abs(fee_delta) > self._state.fee_reserve:
                return LifecycleResult(
                    success=False,
                    reason=(
                        f'order {order_id!r} fee deficit {abs(fee_delta)} '
                        f'exceeds fee_reserve {self._state.fee_reserve}'
                    ),
                    category=FailureCategory.EXPECTED_MISS,
                )

            if new_remaining == _ZERO:
                self._orders.pop(order_id)
            else:
                self._orders[order_id] = updated

            self._state.working_order_notional -= fill_with_estimated
            self._state.position_notional += fill_notional + actual_fees
            self._state.fee_reserve += fee_delta

            if fee_delta != _ZERO:
                self._adjust_strategy_deployed(order.strategy_id, -fee_delta)

            return LifecycleResult(success=True)

    def order_cancel(self, order_id: str) -> LifecycleResult:
        '''Handle cancellation or expiration of an order.

        Removes the order and releases remaining capital back to available.
        Accepts both WORKING (typical case — venue cancels a resting
        order) AND IN_FLIGHT (PT-FIX-43, mirrors PT-FIX-40 for
        `order_reject` — venue may EXPIRE / CANCEL an order that
        never received an ACK; without this branch the IN_FLIGHT
        capital would stay parked permanently because TTL eviction
        only covers `_reservations`, not `_orders`).

        Args:
            order_id: ID of the canceled order.

        Returns:
            LifecycleResult with reason on failure.
        '''

        with self._lock:
            order = self._orders.get(order_id)

            if order is None:
                return LifecycleResult(
                    success=False,
                    reason=f'order {order_id!r} not found (completed or unknown)',
                    category=FailureCategory.EXPECTED_MISS,
                )

            if order.state == OrderLifecycleState.WORKING:
                self._state.working_order_notional -= order.remaining_total
            elif order.state == OrderLifecycleState.IN_FLIGHT:
                self._state.in_flight_order_notional -= order.remaining_total
            else:
                return LifecycleResult(
                    success=False,
                    reason=(
                        f'order {order_id!r} in {order.state.value}, '
                        'expected WORKING or IN_FLIGHT'
                    ),
                    category=FailureCategory.EXPECTED_MISS,
                )

            self._orders.pop(order_id)
            self._adjust_strategy_deployed(order.strategy_id, -order.remaining_total)

            return LifecycleResult(success=True)

    def _adjust_strategy_deployed(self, strategy_id: str, delta: Decimal) -> None:
        current = self._state.per_strategy_deployed.get(strategy_id, _ZERO)
        updated = current + delta

        if updated < _ZERO:
            _logger.warning(
                'Per-strategy deployed underflow: strategy=%s current=%s delta=%s updated=%s',
                strategy_id,
                current,
                delta,
                updated,
            )
            self._state.per_strategy_deployed.pop(strategy_id, None)
            return

        if updated == _ZERO:
            self._state.per_strategy_deployed.pop(strategy_id, None)
            return

        self._state.per_strategy_deployed[strategy_id] = updated
