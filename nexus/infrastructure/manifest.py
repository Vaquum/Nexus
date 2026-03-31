'''Manifest loading and validation for strategy configuration.

Provides frozen dataclasses for manifest structure and a loader
that parses YAML and validates all constraints including file
existence and Python syntax.
'''

from __future__ import annotations

import ast
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

__all__ = ['Manifest', 'StrategySpec', 'load_manifest']

_ZERO = Decimal('0')
_ONE_HUNDRED = Decimal('100')


@dataclass(frozen=True)
class StrategySpec:
    '''Immutable specification for a single strategy.

    Args:
        strategy_id: Unique identifier for this strategy.
        file: Relative path string to the Python strategy implementation.
        permutation_ids: Limen permutation IDs for Trainer/Cohort signal sources.
        capital_pct: Capital allocation percentage for this strategy.
    '''

    strategy_id: str
    file: str
    permutation_ids: tuple[str, ...]
    capital_pct: Decimal

    def __post_init__(self) -> None:
        '''Validate strategy specification invariants.'''

        if not isinstance(self.strategy_id, str) or not self.strategy_id.strip():
            msg = 'StrategySpec.strategy_id must be a non-empty string'
            raise ValueError(msg)

        if not isinstance(self.file, str) or not self.file.strip():
            msg = 'StrategySpec.file must be a non-empty string'
            raise ValueError(msg)

        if not isinstance(self.permutation_ids, tuple) or not self.permutation_ids:
            msg = 'StrategySpec.permutation_ids must be a non-empty tuple'
            raise ValueError(msg)

        for perm_id in self.permutation_ids:
            if not isinstance(perm_id, str) or not perm_id.strip():
                msg = 'StrategySpec.permutation_ids must contain non-empty strings'
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


def load_manifest(path: Path, allocated_capital: Decimal) -> Manifest:
    '''Load and validate a manifest from a YAML file.

    Args:
        path: Path to the YAML manifest file.
        allocated_capital: Hard ceiling for capital_pool validation.

    Returns:
        Validated Manifest instance.

    Raises:
        ValueError: If manifest is invalid or capital_pool exceeds allocated_capital.
        FileNotFoundError: If manifest file does not exist.
        yaml.YAMLError: If the file contains malformed YAML.
    '''

    if not path.is_file():
        msg = f'Manifest file not found: {path}'
        raise FileNotFoundError(msg)

    with path.open() as f:
        data: dict[str, Any] = yaml.safe_load(f)

    if not isinstance(data, dict):
        msg = 'Manifest must be a YAML mapping'
        raise ValueError(msg)

    raw_capital_pool = data.get('capital_pool')
    if raw_capital_pool is None:
        msg = 'Manifest missing required field: capital_pool'
        raise ValueError(msg)

    try:
        capital_pool = Decimal(str(raw_capital_pool))
    except InvalidOperation as e:
        msg = f'Manifest capital_pool is not a valid number: {raw_capital_pool!r}'
        raise ValueError(msg) from e

    if not capital_pool.is_finite() or capital_pool <= _ZERO:
        msg = f'Manifest capital_pool must be a finite positive number: {capital_pool}'
        raise ValueError(msg)

    if capital_pool > allocated_capital:
        msg = (
            f'Manifest capital_pool {capital_pool} exceeds '
            f'allocated_capital {allocated_capital}'
        )
        raise ValueError(msg)

    raw_strategies = data.get('strategies')
    if not isinstance(raw_strategies, list) or not raw_strategies:
        msg = 'Manifest missing or empty strategies list'
        raise ValueError(msg)

    specs: list[StrategySpec] = []

    for raw_spec in raw_strategies:
        if not isinstance(raw_spec, dict):
            msg = 'Each strategy must be a YAML mapping'
            raise ValueError(msg)

        strategy_id = raw_spec.get('id')
        if strategy_id is None:
            msg = 'Strategy missing required field: id'
            raise ValueError(msg)

        file = raw_spec.get('file')
        if file is None:
            msg = f'Strategy {strategy_id!r} missing required field: file'
            raise ValueError(msg)

        raw_permutation_ids = raw_spec.get('permutation_ids')
        if not isinstance(raw_permutation_ids, list) or not raw_permutation_ids:
            msg = f'Strategy {strategy_id!r} missing or empty permutation_ids'
            raise ValueError(msg)

        permutation_ids = tuple(raw_permutation_ids)

        raw_capital_pct = raw_spec.get('capital_pct')
        if raw_capital_pct is None:
            msg = f'Strategy {strategy_id!r} missing required field: capital_pct'
            raise ValueError(msg)

        try:
            capital_pct = Decimal(str(raw_capital_pct))
        except InvalidOperation as e:
            msg = f'Strategy {strategy_id!r} capital_pct is not a valid number: {raw_capital_pct!r}'
            raise ValueError(msg) from e

        specs.append(
            StrategySpec(
                strategy_id=strategy_id,
                file=file,
                permutation_ids=permutation_ids,
                capital_pct=capital_pct,
            )
        )

    manifest = Manifest(capital_pool=capital_pool, strategies=tuple(specs))

    _validate_strategy_files(manifest, path.parent)

    return manifest


def _validate_strategy_files(manifest: Manifest, base_path: Path) -> None:
    '''Validate all strategy files exist and are valid Python.

    Args:
        manifest: Manifest with strategies to validate.
        base_path: Base path for resolving relative file paths.

    Raises:
        ValueError: If any strategy file is missing, escapes base path, or has syntax errors.
    '''

    base_resolved = base_path.resolve()

    for spec in manifest.strategies:
        raw_path = Path(spec.file)

        if raw_path.is_absolute():
            msg = f'Strategy {spec.strategy_id!r} file must be relative: {raw_path}'
            raise ValueError(msg)

        file_path = (base_resolved / raw_path).resolve()

        try:
            file_path.relative_to(base_resolved)
        except ValueError as e:
            msg = f'Strategy {spec.strategy_id!r} file escapes base path: {file_path}'
            raise ValueError(msg) from e

        if not file_path.is_file():
            msg = f'Strategy {spec.strategy_id!r} file not found: {file_path}'
            raise ValueError(msg)

        try:
            ast.parse(file_path.read_text())
        except SyntaxError as e:
            msg = f'Strategy {spec.strategy_id!r} file has syntax error: {file_path}: {e}'
            raise ValueError(msg) from e
