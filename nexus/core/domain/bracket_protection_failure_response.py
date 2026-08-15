'''Per-account response policy for a bracket protection failure.'''

from __future__ import annotations

from enum import Enum

__all__ = ['BracketProtectionFailureResponse']


class BracketProtectionFailureResponse(Enum):
    '''How an account reacts when a bracket protective-OCO amend leaves the
    position unprotected.

    Chosen after protection cannot be confirmed within the amend's deadline
    and the position is naked. FLATTEN_THEN_HALT is the fail-safe default:
    market-flatten the reconciled remaining position, then halt the account.
    REDUCE_ONLY is an explicit override for a supervised account that has
    accepted temporary naked inventory: block new entries and alert, but
    leave the position open. A plain halt is not an option: it stops the
    engine without addressing the unprotected inventory.

    Args:
        FLATTEN_THEN_HALT: Market-flatten the reconciled remaining position,
            then drive the account to HALTED.
        REDUCE_ONLY: Force REDUCE_ONLY (block new entries; leave the position)
            and alert.
    '''

    FLATTEN_THEN_HALT = 'FLATTEN_THEN_HALT'
    REDUCE_ONLY = 'REDUCE_ONLY'
