'''Outbound connector protocol for Trading sub-system dispatch.

Defines the interface for submitting TradeCommands to the Trading
sub-system (Praxis). Concrete implementations handle transport
details — in-process async bridge for production, mock for testing.
'''

from __future__ import annotations

from typing import Protocol

from nexus.infrastructure.praxis_connector.trade_command import TradeCommand

__all__ = ['OutboundConnector']


class OutboundConnector(Protocol):
    '''Protocol for submitting TradeCommands to the Trading sub-system.'''

    def send_command(self, command: TradeCommand) -> str:
        '''Submit TradeCommand to Trading sub-system.

        Returns:
            Command ID assigned by Trading sub-system.
        '''
        ...
