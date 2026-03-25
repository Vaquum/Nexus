'''Outbound connector protocol for Trading sub-system dispatch.'''

from __future__ import annotations

from typing import Protocol

from nexus.infrastructure.praxis_connector.trade_command import TradeCommand

__all__ = ['OutboundConnector']


class OutboundConnector(Protocol):
    '''Protocol for submitting TradeCommands to the Trading sub-system.

    Concrete implementations handle transport details (HTTP, queue, etc.).
    '''

    def send_command(self, command: TradeCommand) -> None:
        '''Submit TradeCommand to Trading sub-system.'''
        ...
