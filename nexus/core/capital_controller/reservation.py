'''Capital reservation records for TOCTOU race prevention.

A Reservation locks quote capital between validation and order
submission. ReservationResult carries the outcome of an atomic
check-and-reserve attempt.
'''

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

__all__ = ['Reservation', 'ReservationResult']

_ZERO = Decimal(0)


@dataclass(frozen=True)
class Reservation:
    '''An immutable capital lock for a pending BUY order.

    Args:
        reservation_id: Unique identifier for this reservation.
        strategy_id: Which strategy requested the reservation.
        notional: Quote capital locked for the order.
        estimated_fees: Quote capital locked for estimated transaction fees.
        created_at: When this reservation was created.
        expires_at: When this reservation auto-expires if unused.
    '''

    reservation_id: str
    strategy_id: str
    notional: Decimal
    estimated_fees: Decimal
    created_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        '''Validate invariants at construction time.'''

        if not isinstance(self.reservation_id, str) or not self.reservation_id.strip():
            msg = 'Reservation.reservation_id must be a non-empty string'
            raise ValueError(msg)

        if not isinstance(self.strategy_id, str) or not self.strategy_id.strip():
            msg = 'Reservation.strategy_id must be a non-empty string'
            raise ValueError(msg)

        if (
            not isinstance(self.notional, Decimal)
            or not self.notional.is_finite()
            or self.notional < _ZERO
        ):
            msg = 'Reservation.notional must be a finite non-negative Decimal'
            raise ValueError(msg)

        if (
            not isinstance(self.estimated_fees, Decimal)
            or not self.estimated_fees.is_finite()
            or self.estimated_fees < _ZERO
        ):
            msg = 'Reservation.estimated_fees must be a finite non-negative Decimal'
            raise ValueError(msg)

        if not isinstance(self.created_at, datetime):
            msg = 'Reservation.created_at must be a datetime'
            raise ValueError(msg)

        if (
            self.created_at.tzinfo is None
            or self.created_at.tzinfo.utcoffset(self.created_at) is None
        ):
            msg = 'Reservation.created_at must be timezone-aware'
            raise ValueError(msg)

        if not isinstance(self.expires_at, datetime):
            msg = 'Reservation.expires_at must be a datetime'
            raise ValueError(msg)

        if (
            self.expires_at.tzinfo is None
            or self.expires_at.tzinfo.utcoffset(self.expires_at) is None
        ):
            msg = 'Reservation.expires_at must be timezone-aware'
            raise ValueError(msg)

        if self.expires_at <= self.created_at:
            msg = 'Reservation.expires_at must be after created_at'
            raise ValueError(msg)

    @property
    def total(self) -> Decimal:
        '''Total quote capital locked by this reservation.'''

        return self.notional + self.estimated_fees

    def is_expired(self, now: datetime) -> bool:
        '''Check whether this reservation has passed its TTL.

        Args:
            now: Timezone-aware current time to compare against expires_at.
        '''

        if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
            msg = 'Reservation.is_expired requires timezone-aware datetime'
            raise ValueError(msg)

        return now >= self.expires_at


@dataclass(frozen=True)
class ReservationResult:
    '''Outcome of an atomic check-and-reserve attempt.

    Args:
        granted: Whether the reservation was created.
        reservation: The reservation if granted, None if denied.
        denial_reason: Human-readable reason if denied, None if granted.
    '''

    granted: bool
    reservation: Reservation | None = None
    denial_reason: str | None = None

    def __post_init__(self) -> None:
        '''Validate granted/reservation/denial_reason consistency.'''

        if self.granted and self.reservation is None:
            msg = 'ReservationResult: granted=True requires a reservation'
            raise ValueError(msg)

        if not self.granted and self.reservation is not None:
            msg = 'ReservationResult: granted=False must not have a reservation'
            raise ValueError(msg)

        if not self.granted and self.denial_reason is None:
            msg = 'ReservationResult: granted=False requires a denial_reason'
            raise ValueError(msg)

        if self.granted and self.denial_reason is not None:
            msg = 'ReservationResult: granted=True must not have a denial_reason'
            raise ValueError(msg)
