'''Strategy context for event callbacks.'''

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from nexus.core.domain.enums import OperationalMode
from nexus.core.domain.position import Position

_ZERO = Decimal(0)


@dataclass(frozen=True)
class StrategyContext:
    '''Context provided to strategy event callbacks.

    NOTE: Position objects are mutable by design (size/unrealized_pnl change).
    Strategies must not mutate positions; any mutations are unsupported and
    ignored by the Strategy Runner.

    Args:
        positions: Current open positions for this strategy.
        capital_available: Capital available for new positions in quote asset.
        operational_mode: Current operational state of the strategy.
    '''

    positions: tuple[Position, ...]
    capital_available: Decimal
    operational_mode: OperationalMode

    def __post_init__(self) -> None:
        '''Validate invariants at construction time.'''

        if not isinstance(self.positions, tuple):
            msg = 'positions must be a tuple'
            raise ValueError(msg)

        for pos in self.positions:
            if not isinstance(pos, Position):
                msg = 'positions must contain only Position instances'
                raise ValueError(msg)

        if not isinstance(self.capital_available, Decimal):
            msg = 'capital_available must be a Decimal'
            raise ValueError(msg)

        if not self.capital_available.is_finite() or self.capital_available < _ZERO:
            msg = 'capital_available must be a finite non-negative value'
            raise ValueError(msg)

        if not isinstance(self.operational_mode, OperationalMode):
            msg = 'operational_mode must be an OperationalMode'
            raise ValueError(msg)
