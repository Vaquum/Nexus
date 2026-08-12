'''ReconciliationMismatch dataclass for Praxis Connector inbound processing.

Represents a per-asset balance divergence the Praxis reconciliation engine
detected against the venue. Pushed to Nexus, which applies the account's
configured response.
'''

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

__all__ = ['ReconciliationMismatch']


@dataclass(frozen=True)
class ReconciliationMismatch:
    '''Immutable per-asset balance divergence against the venue.

    Args:
        account_id: Owning account.
        timestamp: When the divergence was detected (must be UTC).
        reconciliation_mismatch_id: Stable unique identifier for the mismatch.
        asset: Asset whose balance mismatched.
        expected: Praxis-projected balance for the asset (finite).
        actual: Venue-reported balance for the asset (finite).
    '''

    account_id: str
    timestamp: datetime
    reconciliation_mismatch_id: str
    asset: str
    expected: Decimal
    actual: Decimal

    @property
    def delta(self) -> Decimal:
        '''Return the venue-reported minus Praxis-projected difference.'''

        return self.actual - self.expected

    def __post_init__(self) -> None:
        '''Validate invariants at construction time.'''

        for field_name in ('account_id', 'reconciliation_mismatch_id', 'asset'):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                msg = f'ReconciliationMismatch.{field_name} must be a non-empty string'
                raise ValueError(msg)

        if (
            not isinstance(self.timestamp, datetime)
            or self.timestamp.tzinfo is not timezone.utc
        ):
            msg = 'ReconciliationMismatch.timestamp must be a UTC datetime'
            raise ValueError(msg)

        for field_name in ('expected', 'actual'):
            value = getattr(self, field_name)
            if not isinstance(value, Decimal) or not value.is_finite():
                msg = f'ReconciliationMismatch.{field_name} must be a finite Decimal'
                raise ValueError(msg)

        if self.expected == self.actual:
            msg = 'ReconciliationMismatch requires expected != actual'
            raise ValueError(msg)
