'''Atomic check-and-reserve capital controller.

Guards CapitalState mutations behind a threading lock to prevent
TOCTOU races when multiple strategies compete for the same pool.
'''

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from nexus.core.capital_controller.reservation import (
    Reservation,
    ReservationResult,
)
from nexus.core.capital_controller.tracked_order import (
    OrderLifecycleState,
    TrackedOrder,
)
from nexus.core.domain.capital_state import CapitalState

__all__ = ['CapitalController']

_logger = logging.getLogger(__name__)

MAX_ALLOCATION_PER_TRADE_PCT = Decimal('0.15')
MAX_CAPITAL_UTILIZATION_PCT = Decimal('0.80')
DEFAULT_TTL_SECONDS = 30

_ZERO = Decimal(0)
_ONE_HUNDRED = Decimal('100')


class CapitalController:
    '''Thread-safe capital reservation manager.

    Args:
        capital_state: Mutable capital state to guard.
    '''

    def __init__(self, capital_state: CapitalState) -> None:
        self._state = capital_state
        self._lock = threading.Lock()
        self._reservations: dict[str, Reservation] = {}
        self._orders: dict[str, TrackedOrder] = {}

    def check_and_reserve(
        self,
        strategy_id: str,
        order_notional: Decimal,
        estimated_fees: Decimal,
        strategy_budget: Decimal,
        strategy_deployed: Decimal,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> ReservationResult:
        '''Atomically validate capital checks and create a reservation.

        Args:
            strategy_id: Which strategy is requesting capital.
            order_notional: Quote capital for the order.
            estimated_fees: Estimated transaction fees.
            strategy_budget: Budget ceiling for this strategy.
            strategy_deployed: Capital already deployed by this strategy.
            ttl_seconds: Seconds before reservation auto-expires.

        Returns:
            ReservationResult with granted reservation or denial reason.
        '''

        for name, val in (
            ('order_notional', order_notional),
            ('estimated_fees', estimated_fees),
            ('strategy_budget', strategy_budget),
            ('strategy_deployed', strategy_deployed),
        ):
            if not isinstance(val, Decimal) or not val.is_finite():
                msg = f'Invalid {name}: {val}'
                raise ValueError(msg)

        if not strategy_id or not strategy_id.strip():
            msg = 'strategy_id must be a non-empty string'
            raise ValueError(msg)

        if order_notional < _ZERO:
            msg = f'order_notional must be non-negative: {order_notional}'
            raise ValueError(msg)

        if estimated_fees < _ZERO:
            msg = f'estimated_fees must be non-negative: {estimated_fees}'
            raise ValueError(msg)

        if strategy_deployed < _ZERO:
            msg = f'strategy_deployed must be non-negative: {strategy_deployed}'
            raise ValueError(msg)

        if ttl_seconds <= 0:
            msg = f'ttl_seconds must be positive: {ttl_seconds}'
            raise ValueError(msg)

        total = order_notional + estimated_fees

        with self._lock:
            self._purge_expired()

            allocation_pct = order_notional / self._state.capital_pool

            if allocation_pct > MAX_ALLOCATION_PER_TRADE_PCT:
                return ReservationResult(
                    granted=False,
                    denial_reason=(
                        f'Per-trade allocation {allocation_pct:.4f} exceeds '
                        f'limit {MAX_ALLOCATION_PER_TRADE_PCT}'
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

            total_deployed = (
                self._state.position_notional
                + self._state.working_order_notional
                + self._state.in_flight_order_notional
                + self._state.reservation_notional
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
            self._state.reservation_notional += total

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

        if not isinstance(capital_pct, Decimal) or not capital_pct.is_finite():
            msg = f'capital_pct must be a finite Decimal: {capital_pct}'
            raise ValueError(msg)

        if capital_pct <= _ZERO or capital_pct > _ONE_HUNDRED:
            msg = f'capital_pct must be in (0, 100]: {capital_pct}'
            raise ValueError(msg)

        if (
            not isinstance(strategy_realized_pnl, Decimal)
            or not strategy_realized_pnl.is_finite()
        ):
            msg = (
                'strategy_realized_pnl must be a finite Decimal: '
                f'{strategy_realized_pnl}'
            )
            raise ValueError(msg)

        base_budget = self._state.capital_pool * (capital_pct / _ONE_HUNDRED)

        if not auto_compound:
            return base_budget

        return base_budget + strategy_realized_pnl

    def _purge_expired(self, now: datetime | None = None) -> None:
        if now is None:
            now = datetime.now(tz=timezone.utc)
        expired = [rid for rid, r in self._reservations.items() if r.is_expired(now)]

        for rid in expired:
            reservation = self._reservations.pop(rid)
            self._state.reservation_notional -= reservation.total
            held_seconds = (now - reservation.created_at).total_seconds()
            _logger.warning(
                'Reservation expired: id=%s strategy=%s total=%s held=%.1fs',
                reservation.reservation_id,
                reservation.strategy_id,
                reservation.total,
                held_seconds,
            )

    def release_reservation(self, reservation_id: str) -> bool:
        '''Release a reservation and return its capital to the available pool.

        Args:
            reservation_id: ID of the reservation to release.

        Returns:
            True if the reservation was found and released.
        '''

        with self._lock:
            reservation = self._reservations.pop(reservation_id, None)

            if reservation is None:
                return False

            self._state.reservation_notional -= reservation.total
            return True

    def send_order(self, reservation_id: str, order_id: str) -> bool:
        '''Convert a reservation into an in-flight order.

        Consumes the reservation and creates a TrackedOrder in IN_FLIGHT state.
        Capital moves from reservation_notional to in_flight_order_notional.

        Args:
            reservation_id: ID of the reservation to consume.
            order_id: Venue order ID for tracking.

        Returns:
            True if successful, False if reservation not found or expired.
        '''

        if not order_id or not order_id.strip():
            msg = 'order_id must be a non-empty string'
            raise ValueError(msg)

        with self._lock:
            now = datetime.now(tz=timezone.utc)
            self._purge_expired(now)

            if order_id in self._orders:
                msg = f'order_id already tracked: {order_id}'
                raise ValueError(msg)

            reservation = self._reservations.pop(reservation_id, None)

            if reservation is None:
                return False

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

            return True

    def order_ack(self, order_id: str) -> bool:
        '''Acknowledge an in-flight order as working on venue.

        Transitions the order from IN_FLIGHT to WORKING state.
        Capital moves from in_flight_order_notional to working_order_notional.

        Args:
            order_id: ID of the order to acknowledge.

        Returns:
            True if successful, False if order not found or not IN_FLIGHT.
        '''

        with self._lock:
            order = self._orders.get(order_id)

            if order is None:
                return False

            if order.state != OrderLifecycleState.IN_FLIGHT:
                return False

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

            return True

    def order_reject(self, order_id: str) -> bool:
        '''Handle venue rejection of an in-flight order.

        Removes the order and releases capital back to available.

        Args:
            order_id: ID of the rejected order.

        Returns:
            True if successful, False if order not found or not IN_FLIGHT.
        '''

        with self._lock:
            order = self._orders.get(order_id)

            if order is None:
                return False

            if order.state != OrderLifecycleState.IN_FLIGHT:
                return False

            self._orders.pop(order_id)
            self._state.in_flight_order_notional -= order.total

            return True

    def order_fill(self, order_id: str, fill_notional: Decimal) -> bool:
        '''Handle a fill (partial or full) on a working order.

        Moves capital from working_order_notional to position_notional.
        The moved amount includes the fill plus its proportional share of
        estimated fees. Partial fills update remaining_notional; full fills
        remove the order.

        Args:
            order_id: ID of the filled order.
            fill_notional: Quote capital filled (excluding fees). The
                proportional fee component is computed and added.

        Returns:
            True if successful, False if order not found, wrong state,
            or fill_notional exceeds remaining.
        '''

        if not isinstance(fill_notional, Decimal) or not fill_notional.is_finite():
            msg = f'fill_notional must be a finite Decimal: {fill_notional}'
            raise ValueError(msg)

        if fill_notional <= _ZERO:
            msg = f'fill_notional must be positive: {fill_notional}'
            raise ValueError(msg)

        with self._lock:
            order = self._orders.get(order_id)

            if order is None:
                return False

            if order.state != OrderLifecycleState.WORKING:
                return False

            if fill_notional > order.remaining_notional:
                return False

            pre_fill_remaining = order.remaining_total
            new_remaining = order.remaining_notional - fill_notional

            if new_remaining == _ZERO:
                fill_with_fees = pre_fill_remaining
                self._orders.pop(order_id)
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
                fill_with_fees = pre_fill_remaining - updated.remaining_total
                self._orders[order_id] = updated

            self._state.working_order_notional -= fill_with_fees
            self._state.position_notional += fill_with_fees

            return True

    def order_cancel(self, order_id: str) -> bool:
        '''Handle cancellation of a working order.

        Removes the order and releases remaining capital back to available.

        Args:
            order_id: ID of the canceled order.

        Returns:
            True if successful, False if order not found or not WORKING.
        '''

        with self._lock:
            order = self._orders.get(order_id)

            if order is None:
                return False

            if order.state != OrderLifecycleState.WORKING:
                return False

            self._orders.pop(order_id)
            self._state.working_order_notional -= order.remaining_total

            return True
