'''Inbound connector protocol for Trading sub-system result consumption.

Defines the interface for receiving TradeOutcomes from the Trading
sub-system (Praxis). Concrete implementations handle transport
details — queue-based for production, mock for testing.
'''

from __future__ import annotations

from typing import Protocol

from nexus.infrastructure.praxis_connector.trade_outcome import TradeOutcome

__all__ = ['InboundConnector']


class InboundConnector(Protocol):
    '''Protocol for receiving TradeOutcomes from the Trading sub-system.'''

    def receive_outcome(self) -> TradeOutcome | None:
        '''Receive next TradeOutcome from Trading sub-system.

        Returns:
            TradeOutcome if available, None if no outcome pending.
        '''
        ...
