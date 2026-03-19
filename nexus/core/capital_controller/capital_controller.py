'''Atomic check-and-reserve capital controller.

Guards CapitalState mutations behind a threading lock to prevent
TOCTOU races when multiple strategies compete for the same pool.
'''

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from nexus.core.capital_controller.reservation import (
    Reservation,
    ReservationResult,
)
from nexus.core.domain.capital_state import CapitalState

__all__ = ['CapitalController']

MAX_ALLOCATION_PER_TRADE_PCT = Decimal('0.15')
MAX_CAPITAL_UTILIZATION_PCT = Decimal('0.80')
DEFAULT_TTL_SECONDS = 30

_ZERO = Decimal(0)


class CapitalController:
    '''Thread-safe capital reservation manager.

    Args:
        capital_state: Mutable capital state to guard.
    '''

    def __init__(self, capital_state: CapitalState) -> None:
        self._state = capital_state
        self._lock = threading.Lock()
        self._reservations: dict[str, Reservation] = {}

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

        total = order_notional + estimated_fees

        with self._lock:
            allocation_pct = order_notional / self._state.capital_pool

            if allocation_pct > MAX_ALLOCATION_PER_TRADE_PCT:
                return ReservationResult(
                    granted=False,
                    denial_reason=(
                        f'Per-trade allocation {allocation_pct:.4f} exceeds '
                        f'limit {MAX_ALLOCATION_PER_TRADE_PCT}'
                    ),
                )

            if strategy_deployed + order_notional > strategy_budget:
                return ReservationResult(
                    granted=False,
                    denial_reason=(
                        f'Strategy deployed {strategy_deployed} + order {order_notional} '
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
            utilization = (total_deployed + order_notional) / self._state.capital_pool

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
