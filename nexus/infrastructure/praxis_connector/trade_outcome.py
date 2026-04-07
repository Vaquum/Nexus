'''TradeOutcome dataclass for Praxis Connector inbound processing.

Represents execution results from Trading sub-system. Fill outcomes
(PARTIAL, FILLED) carry fill details and actual fees for reconciliation.
'''

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from nexus.infrastructure.praxis_connector.trade_outcome_type import TradeOutcomeType

__all__ = ['TradeOutcome']

_ZERO = Decimal(0)


@dataclass(frozen=True)
class TradeOutcome:
    '''Immutable execution result from Trading sub-system.

    Args:
        outcome_id: Unique outcome reference.
        command_id: Links to original TradeCommand.
        outcome_type: ACK, PARTIAL, FILLED, REJECTED, EXPIRED, or CANCELED.
        timestamp: When outcome occurred (must be timezone-aware).
        fill_size: Base asset filled; required for PARTIAL/FILLED.
        fill_price: Execution price; required for PARTIAL/FILLED.
        fill_notional: Quote amount filled; required for PARTIAL/FILLED.
        actual_fees: Actual fees charged; required for PARTIAL/FILLED.
        remaining_size: Size still working. Present on PARTIAL fills; may be
            provided on CANCELED/EXPIRED to indicate unfilled remainder.
        reject_reason: Venue rejection reason; required for REJECTED.
        cancel_reason: Cancellation reason; optional for CANCELED.
    '''

    outcome_id: str
    command_id: str
    outcome_type: TradeOutcomeType
    timestamp: datetime
    fill_size: Decimal | None = None
    fill_price: Decimal | None = None
    fill_notional: Decimal | None = None
    actual_fees: Decimal | None = None
    remaining_size: Decimal | None = None
    reject_reason: str | None = None
    cancel_reason: str | None = None

    def __post_init__(self) -> None:
        '''Validate invariants at construction time.'''

        if not isinstance(self.outcome_id, str) or not self.outcome_id.strip():
            msg = 'TradeOutcome.outcome_id must be a non-empty string'
            raise ValueError(msg)

        if not isinstance(self.command_id, str) or not self.command_id.strip():
            msg = 'TradeOutcome.command_id must be a non-empty string'
            raise ValueError(msg)

        if not isinstance(self.outcome_type, TradeOutcomeType):
            msg = 'TradeOutcome.outcome_type must be a TradeOutcomeType member'
            raise ValueError(msg)

        if not isinstance(self.timestamp, datetime):
            msg = 'TradeOutcome.timestamp must be a datetime instance'
            raise ValueError(msg)

        if self.timestamp.tzinfo is not timezone.utc:
            msg = 'TradeOutcome.timestamp must be UTC'
            raise ValueError(msg)

        if self.outcome_type.is_fill:
            self._validate_fill_fields()
        else:
            self._validate_non_fill_fields()

        if self.outcome_type == TradeOutcomeType.REJECTED:
            if self.reject_reason is None or not self.reject_reason.strip():
                msg = 'TradeOutcome.reject_reason required for REJECTED outcome'
                raise ValueError(msg)
        elif self.reject_reason is not None:
            msg = 'TradeOutcome.reject_reason must be None for non-REJECTED outcomes'
            raise ValueError(msg)

        if self.outcome_type == TradeOutcomeType.CANCELED:
            if self.cancel_reason is not None and not self.cancel_reason.strip():
                msg = (
                    'TradeOutcome.cancel_reason must be non-empty when provided '
                    'for CANCELED outcome'
                )
                raise ValueError(msg)
        elif self.cancel_reason is not None:
            msg = 'TradeOutcome.cancel_reason must be None for non-CANCELED outcomes'
            raise ValueError(msg)

        if self.remaining_size is not None:
            if (
                not isinstance(self.remaining_size, Decimal)
                or not self.remaining_size.is_finite()
            ):
                msg = 'TradeOutcome.remaining_size must be a finite Decimal'
                raise ValueError(msg)

            if self.remaining_size < _ZERO:
                msg = 'TradeOutcome.remaining_size must be non-negative'
                raise ValueError(msg)

    def _validate_fill_fields(self) -> None:
        '''Validate fill-specific fields for PARTIAL/FILLED outcomes.'''

        if self.fill_size is None:
            msg = 'TradeOutcome.fill_size required for fill outcomes'
            raise ValueError(msg)

        if not isinstance(self.fill_size, Decimal) or not self.fill_size.is_finite():
            msg = 'TradeOutcome.fill_size must be a finite Decimal'
            raise ValueError(msg)

        if self.fill_size <= _ZERO:
            msg = 'TradeOutcome.fill_size must be positive'
            raise ValueError(msg)

        if self.fill_price is None:
            msg = 'TradeOutcome.fill_price required for fill outcomes'
            raise ValueError(msg)

        if not isinstance(self.fill_price, Decimal) or not self.fill_price.is_finite():
            msg = 'TradeOutcome.fill_price must be a finite Decimal'
            raise ValueError(msg)

        if self.fill_price <= _ZERO:
            msg = 'TradeOutcome.fill_price must be positive'
            raise ValueError(msg)

        if self.fill_notional is None:
            msg = 'TradeOutcome.fill_notional required for fill outcomes'
            raise ValueError(msg)

        if (
            not isinstance(self.fill_notional, Decimal)
            or not self.fill_notional.is_finite()
        ):
            msg = 'TradeOutcome.fill_notional must be a finite Decimal'
            raise ValueError(msg)

        if self.fill_notional <= _ZERO:
            msg = 'TradeOutcome.fill_notional must be positive'
            raise ValueError(msg)

        if self.actual_fees is None:
            msg = 'TradeOutcome.actual_fees required for fill outcomes'
            raise ValueError(msg)

        if (
            not isinstance(self.actual_fees, Decimal)
            or not self.actual_fees.is_finite()
        ):
            msg = 'TradeOutcome.actual_fees must be a finite Decimal'
            raise ValueError(msg)

        if self.actual_fees < _ZERO:
            msg = 'TradeOutcome.actual_fees must be non-negative'
            raise ValueError(msg)

    def _validate_non_fill_fields(self) -> None:
        '''Validate that fill-specific fields are absent for non-fill outcomes.'''

        if self.fill_size is not None:
            msg = 'TradeOutcome.fill_size must be None for non-fill outcomes'
            raise ValueError(msg)

        if self.fill_price is not None:
            msg = 'TradeOutcome.fill_price must be None for non-fill outcomes'
            raise ValueError(msg)

        if self.fill_notional is not None:
            msg = 'TradeOutcome.fill_notional must be None for non-fill outcomes'
            raise ValueError(msg)

        if self.actual_fees is not None:
            msg = 'TradeOutcome.actual_fees must be None for non-fill outcomes'
            raise ValueError(msg)
