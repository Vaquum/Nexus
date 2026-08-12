'''FundTransaction dataclass for Praxis Connector inbound processing.

Represents a deposit or withdrawal detected by the Praxis reconciliation
engine. Pushed to Nexus for Manager awareness only; it does not adjust the
capital pool.
'''

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

__all__ = ['FundTransaction']

_ZERO = Decimal(0)
_DIRECTIONS = frozenset({'DEPOSIT', 'WITHDRAWAL'})


@dataclass(frozen=True)
class FundTransaction:
    '''Immutable deposit/withdrawal detected on an account.

    Args:
        account_id: Owning account.
        timestamp: Venue-reported time (must be UTC).
        fund_transaction_id: Stable unique identifier for the transaction.
        amount: Quote-asset amount moved (positive, finite).
        direction: 'DEPOSIT' or 'WITHDRAWAL'.
    '''

    account_id: str
    timestamp: datetime
    fund_transaction_id: str
    amount: Decimal
    direction: str

    def __post_init__(self) -> None:
        '''Validate invariants at construction time.'''

        for field_name in ('account_id', 'fund_transaction_id'):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                msg = f'FundTransaction.{field_name} must be a non-empty string'
                raise ValueError(msg)

        if (
            not isinstance(self.timestamp, datetime)
            or self.timestamp.tzinfo is not timezone.utc
        ):
            msg = 'FundTransaction.timestamp must be a UTC datetime'
            raise ValueError(msg)

        if (
            not isinstance(self.amount, Decimal)
            or not self.amount.is_finite()
            or self.amount <= _ZERO
        ):
            msg = 'FundTransaction.amount must be a positive finite Decimal'
            raise ValueError(msg)

        if self.direction not in _DIRECTIONS:
            allowed = ', '.join(sorted(_DIRECTIONS))
            msg = f'FundTransaction.direction must be one of {allowed}'
            raise ValueError(msg)
