'''Trade command type enum for Praxis Connector outbound translation.

Maps ValidationAction intents to Trading sub-system command semantics.
ENTER/EXIT become NEW_ORDER, MODIFY becomes AMEND_ORDER, ABORT/CANCEL
become CANCEL_ORDER.
'''

from __future__ import annotations

from enum import Enum

__all__ = ['TradeCommandType']


class TradeCommandType(Enum):
    '''Command type for Trading sub-system dispatch.

    NEW_ORDER submits a new order (ENTER/EXIT actions).
    AMEND_ORDER modifies an in-flight order (MODIFY action).
    CANCEL_ORDER cancels an order (ABORT/CANCEL actions).
    '''

    NEW_ORDER = 'NEW_ORDER'
    AMEND_ORDER = 'AMEND_ORDER'
    CANCEL_ORDER = 'CANCEL_ORDER'
