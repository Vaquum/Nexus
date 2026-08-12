'''Per-account response policy for a reconciliation mismatch.'''

from __future__ import annotations

from enum import Enum

__all__ = ['ReconciliationMismatchResponse']


class ReconciliationMismatchResponse(Enum):
    '''How an account reacts when Praxis reports a balance mismatch.

    Args:
        HALT: Drive the account to HALTED until an operator clears it.
        REDUCE_ONLY: Force REDUCE_ONLY (block new entries; allow exits).
        ALERT_ONLY: Log and notify only; do not change operational mode.
    '''

    HALT = 'HALT'
    REDUCE_ONLY = 'REDUCE_ONLY'
    ALERT_ONLY = 'ALERT_ONLY'
