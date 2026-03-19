'''Re-derive rolling loss counters from strategy events.

Pure function that scans StrategyEvent records and computes
per-strategy rolling losses over 24h, 7d, and 30d windows
relative to a recovery timestamp.
'''

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from nexus.infrastructure.strategy_event import StrategyEvent

__all__ = ['RollingLosses', 'derive_rolling_losses']

_ZERO = Decimal(0)
_WINDOW_24H = timedelta(hours=24)
_WINDOW_7D = timedelta(days=7)
_WINDOW_30D = timedelta(days=30)


@dataclass(frozen=True)
class RollingLosses:
    '''Computed rolling loss values for a single strategy.

    Args:
        rolling_loss_24h: Sum of negative realized P&L in the last 24 hours.
        rolling_loss_7d: Sum of negative realized P&L in the last 7 days.
        rolling_loss_30d: Sum of negative realized P&L in the last 30 days.
    '''

    rolling_loss_24h: Decimal = _ZERO
    rolling_loss_7d: Decimal = _ZERO
    rolling_loss_30d: Decimal = _ZERO


def derive_rolling_losses(
    events: list[StrategyEvent],
    recovery_time: datetime,
) -> dict[str, RollingLosses]:
    '''Compute per-strategy rolling losses from strategy events.

    Scans events whose realized_pnl is negative and whose timestamp
    falls within each rolling window relative to recovery_time.

    Args:
        events: Strategy events to scan.
        recovery_time: Reference time for window boundaries.

    Returns:
        Mapping of strategy_id to computed rolling losses.
    '''

    if (
        recovery_time.tzinfo is None
        or recovery_time.tzinfo.utcoffset(recovery_time) is None
    ):
        msg = 'recovery_time must be timezone-aware'
        raise ValueError(msg)

    cutoff_24h = recovery_time - _WINDOW_24H
    cutoff_7d = recovery_time - _WINDOW_7D
    cutoff_30d = recovery_time - _WINDOW_30D

    accum: dict[str, list[Decimal]] = {}

    for event in events:
        if event.realized_pnl >= _ZERO:
            continue

        if event.timestamp < cutoff_30d:
            continue

        loss = abs(event.realized_pnl)
        sid = event.strategy_id

        if sid not in accum:
            accum[sid] = [_ZERO, _ZERO, _ZERO]

        buckets = accum[sid]
        buckets[2] += loss

        if event.timestamp >= cutoff_7d:
            buckets[1] += loss

        if event.timestamp >= cutoff_24h:
            buckets[0] += loss

    return {
        sid: RollingLosses(
            rolling_loss_24h=b[0],
            rolling_loss_7d=b[1],
            rolling_loss_30d=b[2],
        )
        for sid, b in accum.items()
    }
