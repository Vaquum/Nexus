'''Order-related enums mirrored from the Trading sub-system.

These enums mirror the Praxis domain contract so the Strategy layer can
express typed intents without depending on the Trading sub-system package.
String values must stay identical to the Trading sub-system definitions.
'''

from __future__ import annotations

from enum import Enum

__all__ = ['ExecutionMode', 'MakerPreference', 'OrderType']


class ExecutionMode(Enum):
    '''Execution mode for an order submission.'''

    SINGLE_SHOT = 'SINGLE_SHOT'
    BRACKET = 'BRACKET'
    TWAP = 'TWAP'
    SCHEDULED_VWAP = 'SCHEDULED_VWAP'
    ICEBERG = 'ICEBERG'
    TIME_DCA = 'TIME_DCA'
    LADDER_DCA = 'LADDER_DCA'


class OrderType(Enum):
    '''Order type accepted by the venue adapter.'''

    MARKET = 'MARKET'
    LIMIT = 'LIMIT'
    LIMIT_IOC = 'LIMIT_IOC'
    STOP = 'STOP'
    STOP_LIMIT = 'STOP_LIMIT'
    TAKE_PROFIT = 'TAKE_PROFIT'
    TP_LIMIT = 'TP_LIMIT'
    OCO = 'OCO'


class MakerPreference(Enum):
    '''Maker/taker preference for order placement.'''

    MAKER_ONLY = 'MAKER_ONLY'
    MAKER_PREFERRED = 'MAKER_PREFERRED'
    NO_PREFERENCE = 'NO_PREFERENCE'
