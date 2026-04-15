'''Typed result for capital lifecycle operations.

Replaces bare boolean returns with structured reason codes and
failure categories for observability (TD-003).
'''

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = ['FailureCategory', 'LifecycleResult']


class FailureCategory(Enum):
    '''Classification of lifecycle operation failures.'''

    EXPECTED_MISS = 'expected_miss'
    INVARIANT_BREACH = 'invariant_breach'


@dataclass(frozen=True)
class LifecycleResult:
    '''Result of a capital lifecycle state transition.

    Args:
        success: Whether the operation succeeded.
        reason: Human-readable failure reason. None on success.
        category: Failure classification. None on success.
    '''

    success: bool
    reason: str | None = None
    category: FailureCategory | None = None
