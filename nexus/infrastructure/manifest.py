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

__all__ = ['Manifest', 'SensorSpec', 'StrategySpec', 'load_manifest']

_ZERO = Decimal('0')
_ONE_HUNDRED = Decimal('100')


@dataclass(frozen=True)
class SensorSpec:
    '''Specification for making a Limen Sensor into a signal source.

    Args:
        experiment_dir: Path to completed Limen experiment directory.
        permutation_ids: Round IDs from experiment to train as Sensors.
        interval_seconds: How often to call predict() in seconds.
    '''

    experiment_dir: Path
    permutation_ids: tuple[int, ...]
    interval_seconds: int

    def __post_init__(self) -> None:
        '''Validate predictor function specification invariants.'''

        if not isinstance(self.experiment_dir, Path):
            msg = 'SensorSpec.experiment_dir must be a Path'
            raise ValueError(msg)

        if not self.experiment_dir.is_dir():
            msg = f'SensorSpec.experiment_dir not found: {self.experiment_dir}'
            raise ValueError(msg)

        if not isinstance(self.permutation_ids, tuple) or not self.permutation_ids:
            msg = 'SensorSpec.permutation_ids must be a non-empty tuple'
            raise ValueError(msg)

        for pid in self.permutation_ids:
            if not isinstance(pid, int) or isinstance(pid, bool):
                msg = f'SensorSpec.permutation_ids must contain ints, got {pid!r}'
                raise ValueError(msg)

        if not isinstance(self.interval_seconds, int) or isinstance(self.interval_seconds, bool):
            msg = 'SensorSpec.interval_seconds must be an int'
            raise ValueError(msg)

        if self.interval_seconds <= 0:
            msg = f'SensorSpec.interval_seconds must be positive: {self.interval_seconds}'
            raise ValueError(msg)


@dataclass(frozen=True)
class StrategySpec:
    '''Immutable specification for a single strategy.

    Args:
        strategy_id: Unique identifier for this strategy.
        file: Relative path string to the Python strategy implementation.
        sensors: Sensor specifications for signal sources.
        capital_pct: Capital allocation percentage for this strategy.
    '''

    strategy_id: str
    file: str
    sensors: tuple[SensorSpec, ...]
    capital_pct: Decimal

    def __post_init__(self) -> None:
        '''Validate strategy specification invariants.'''

        if (
            not isinstance(self.strategy_id, str)
            or not self.strategy_id.strip()
            or self.strategy_id != self.strategy_id.strip()
        ):
            msg = 'StrategySpec.strategy_id must be a non-empty string without surrounding whitespace'
            raise ValueError(msg)

        if not isinstance(self.file, str) or not self.file.strip() or self.file != self.file.strip():
            msg = 'StrategySpec.file must be a non-empty string without surrounding whitespace'
            raise ValueError(msg)

        if not isinstance(self.sensors, tuple) or not self.sensors:
            msg = 'StrategySpec.sensors must be a non-empty tuple'
            raise ValueError(msg)

        for pfn in self.sensors:
            if not isinstance(pfn, SensorSpec):
                msg = 'StrategySpec.sensors must contain SensorSpec instances'
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

    with path.open(encoding='utf-8') as f:
        data: Any = yaml.safe_load(f)

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

    if (
        not isinstance(allocated_capital, Decimal)
        or not allocated_capital.is_finite()
    ):
        msg = 'allocated_capital must be a finite Decimal'
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

        raw_sensors = raw_spec.get('sensors')
        if not isinstance(raw_sensors, list) or not raw_sensors:
            msg = f'Strategy {strategy_id!r} missing or empty sensors'
            raise ValueError(msg)

        sensor_specs: list[SensorSpec] = []

        for raw_sensor in raw_sensors:
            if not isinstance(raw_sensor, dict):
                msg = f'Strategy {strategy_id!r} sensors entries must be mappings'
                raise ValueError(msg)

            raw_experiment = raw_sensor.get('experiment')
            if raw_experiment is None:
                msg = f'Strategy {strategy_id!r} sensor missing required field: experiment'
                raise ValueError(msg)

            raw_pids = raw_sensor.get('permutation_ids')
            if not isinstance(raw_pids, list) or not raw_pids:
                msg = f'Strategy {strategy_id!r} sensor missing or empty permutation_ids'
                raise ValueError(msg)

            raw_interval = raw_sensor.get('interval_seconds')
            if raw_interval is None:
                msg = f'Strategy {strategy_id!r} sensor missing required field: interval_seconds'
                raise ValueError(msg)

            sensor_specs.append(
                SensorSpec(
                    experiment_dir=Path(str(raw_experiment)),
                    permutation_ids=tuple(int(pid) for pid in raw_pids),
                    interval_seconds=int(raw_interval),
                )
            )

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
                sensors=tuple(sensor_specs),
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
            ast.parse(file_path.read_text(encoding='utf-8'))
        except SyntaxError as e:
            msg = f'Strategy {spec.strategy_id!r} file has syntax error: {file_path}: {e}'
            raise ValueError(msg) from e
