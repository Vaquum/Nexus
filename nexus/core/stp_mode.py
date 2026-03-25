'''Self-trade prevention mode enum.

Separate module to avoid circular imports with InstanceConfig.
'''

from __future__ import annotations

from enum import Enum

__all__ = ['STPMode']


class STPMode(Enum):
    '''Self-trade prevention mode for order submission.

    Determines behavior when a new order would match against
    the account's own resting order. CANCEL_MAKER cancels the
    resting order. CANCEL_TAKER cancels the incoming order.
    CANCEL_BOTH cancels both sides.
    '''

    CANCEL_MAKER = 'CANCEL_MAKER'
    CANCEL_TAKER = 'CANCEL_TAKER'
    CANCEL_BOTH = 'CANCEL_BOTH'
