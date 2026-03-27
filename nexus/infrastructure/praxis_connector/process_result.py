'''ProcessResult dataclass for outcome processing results.

Captures the result of processing a TradeOutcome, including success
status and any state changes that occurred.
'''

from __future__ import annotations

from dataclasses import dataclass

from nexus.infrastructure.praxis_connector.trade_outcome_type import TradeOutcomeType

__all__ = ['ProcessResult']


@dataclass(frozen=True)
class ProcessResult:
    '''Result of processing a TradeOutcome.

    Args:
        success: Whether processing completed successfully.
        outcome_type: The type of outcome that was processed.
        position_updated: Whether position was modified.
        capital_updated: Whether capital state was modified.
        error_reason: Reason for failure if success is False.
    '''

    success: bool
    outcome_type: TradeOutcomeType
    position_updated: bool = False
    capital_updated: bool = False
    error_reason: str | None = None

    def __post_init__(self) -> None:
        '''Validate invariants at construction time.'''

        if not isinstance(self.outcome_type, TradeOutcomeType):
            msg = 'ProcessResult.outcome_type must be a TradeOutcomeType member'
            raise ValueError(msg)

        if not self.success and self.error_reason is None:
            msg = 'ProcessResult.error_reason required when success is False'
            raise ValueError(msg)

        if self.success and self.error_reason is not None:
            msg = 'ProcessResult.error_reason must be None when success is True'
            raise ValueError(msg)
