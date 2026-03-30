'''Manifest loading and validation for strategy configuration.

Provides frozen dataclasses for manifest structure and a loader
that parses YAML and validates all constraints including file
existence and importability.
'''

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

__all__ = ['Manifest', 'StrategySpec']

_ZERO = Decimal('0')
_ONE_HUNDRED = Decimal('100')


@dataclass(frozen=True)
class StrategySpec:
    '''Immutable specification for a single strategy.

    Args:
        strategy_id: Unique identifier for this strategy.
        file: Path to the Python strategy implementation.
        predictor_fn_ids: Predictor IDs this strategy depends on (from Limen).
        capital_pct: Capital allocation percentage for this strategy.
    '''

    strategy_id: str
    file: str
    predictor_fn_ids: tuple[str, ...]
    capital_pct: Decimal

    def __post_init__(self) -> None:
        '''Validate strategy specification invariants.'''

        if not isinstance(self.strategy_id, str) or not self.strategy_id.strip():
            msg = 'StrategySpec.strategy_id must be a non-empty string'
            raise ValueError(msg)

        if not isinstance(self.file, str) or not self.file.strip():
            msg = 'StrategySpec.file must be a non-empty string'
            raise ValueError(msg)

        if not isinstance(self.predictor_fn_ids, tuple) or not self.predictor_fn_ids:
            msg = 'StrategySpec.predictor_fn_ids must be a non-empty tuple'
            raise ValueError(msg)

        for predictor_id in self.predictor_fn_ids:
            if not isinstance(predictor_id, str) or not predictor_id.strip():
                msg = 'StrategySpec.predictor_fn_ids must contain non-empty strings'
                raise ValueError(msg)

        if (
            not isinstance(self.capital_pct, Decimal)
            or not self.capital_pct.is_finite()
        ):
            msg = 'StrategySpec.capital_pct must be a finite Decimal'
            raise ValueError(msg)

        if self.capital_pct <= _ZERO or self.capital_pct > _ONE_HUNDRED:
            msg = 'StrategySpec.capital_pct must be in (0, 100]'
            raise ValueError(msg)


@dataclass(frozen=True)
class Manifest:
    '''Immutable manifest for a Manager instance.

    Args:
        capital_pool: Total capital in quote asset for this instance.
        strategies: Strategy specifications for this instance.
    '''

    capital_pool: Decimal
    strategies: tuple[StrategySpec, ...]

    def __post_init__(self) -> None:
        '''Validate manifest invariants.'''

        if (
            not isinstance(self.capital_pool, Decimal)
            or not self.capital_pool.is_finite()
        ):
            msg = 'Manifest.capital_pool must be a finite Decimal'
            raise ValueError(msg)

        if self.capital_pool <= _ZERO:
            msg = 'Manifest.capital_pool must be positive'
            raise ValueError(msg)

        if not isinstance(self.strategies, tuple) or not self.strategies:
            msg = 'Manifest.strategies must be a non-empty tuple'
            raise ValueError(msg)

        seen_ids: set[str] = set()
        total_pct = _ZERO

        for spec in self.strategies:
            if not isinstance(spec, StrategySpec):
                msg = 'Manifest.strategies must contain StrategySpec instances'
                raise ValueError(msg)

            if spec.strategy_id in seen_ids:
                msg = f'Manifest.strategies contains duplicate strategy_id: {spec.strategy_id!r}'
                raise ValueError(msg)

            seen_ids.add(spec.strategy_id)
            total_pct += spec.capital_pct

        if total_pct > _ONE_HUNDRED:
            msg = f'Manifest.strategies capital_pct sum {total_pct} exceeds 100'
            raise ValueError(msg)
