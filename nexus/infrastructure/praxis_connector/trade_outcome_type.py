'''Trade outcome type enum for Praxis Connector inbound processing.

Classifies execution results from Trading sub-system. Terminal outcomes
(FILLED, REJECTED, EXPIRED, CANCELED) end the order lifecycle.
Non-terminal outcomes (ACK, PARTIAL) indicate ongoing order state.
'''

from __future__ import annotations

from enum import Enum

__all__ = ['TradeOutcomeType']


class TradeOutcomeType(Enum):
    '''Outcome type for Trading sub-system execution results.

    ACK indicates order accepted and working on venue.
    PARTIAL indicates partial fill with order still working.
    FILLED indicates full fill and order complete.
    REJECTED indicates venue rejected the order.
    EXPIRED indicates order expired (TTL or session).
    CANCELED indicates order canceled (by request or venue).
    '''

    ACK = 'ACK'
    PARTIAL = 'PARTIAL'
    FILLED = 'FILLED'
    REJECTED = 'REJECTED'
    EXPIRED = 'EXPIRED'
    CANCELED = 'CANCELED'

    @property
    def is_terminal(self) -> bool:
        '''Return True if this outcome ends the order lifecycle.'''

        return self in (
            TradeOutcomeType.FILLED,
            TradeOutcomeType.REJECTED,
            TradeOutcomeType.EXPIRED,
            TradeOutcomeType.CANCELED,
        )

    @property
    def is_fill(self) -> bool:
        '''Return True if this outcome represents a fill (partial or full).'''

        return self in (TradeOutcomeType.PARTIAL, TradeOutcomeType.FILLED)
