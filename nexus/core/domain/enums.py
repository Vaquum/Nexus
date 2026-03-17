'''Enumerated types for the Nexus Manager domain.

Defines operational mode, order side, and breach level enums
used across Instance State, Capital Controller, Validator, and Strategy Runner.
'''

from __future__ import annotations

from enum import Enum

__all__ = ['BreachLevel', 'OperationalMode', 'OrderSide']


class OperationalMode(Enum):
    '''Instance or strategy operational state.

    ACTIVE allows all trading. REDUCE_ONLY blocks new entries
    but allows closes. HALTED stops all trading until manual
    approval to resume.
    '''

    ACTIVE = 'ACTIVE'
    REDUCE_ONLY = 'REDUCE_ONLY'
    HALTED = 'HALTED'


class OrderSide(Enum):
    '''Buy or sell direction for orders and positions.'''

    BUY = 'BUY'
    SELL = 'SELL'


class BreachLevel(Enum):
    '''Risk limit breach severity.

    NONE is normal. WARN triggers alerts. BREACH triggers
    breach_action (reduce_only or reject). HALT stops trading.
    '''

    NONE = 'NONE'
    WARN = 'WARN'
    BREACH = 'BREACH'
    HALT = 'HALT'
