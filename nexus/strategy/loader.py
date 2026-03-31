'''Strategy loading and instantiation from manifest specifications.'''

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from nexus.infrastructure.manifest import StrategySpec
from nexus.strategy.base import Strategy

__all__ = ['instantiate_strategy', 'load_strategy_class']


def instantiate_strategy(spec: StrategySpec, base_path: Path) -> Strategy:
    '''Load and instantiate a Strategy from a manifest specification.

    Args:
        spec: Strategy specification from manifest.
        base_path: Base directory for resolving the strategy file path.

    Returns:
        Instantiated Strategy with strategy_id from spec.

    Raises:
        ValueError: If loading or instantiation fails.
    '''

    strategy_class = load_strategy_class(Path(spec.file), base_path)

    try:
        return strategy_class(spec.strategy_id)
    except Exception as e:
        msg = f'Failed to instantiate strategy {spec.strategy_id!r}'
        raise ValueError(msg) from e


def load_strategy_class(file_path: Path, base_path: Path) -> type[Strategy]:
    '''Load a Strategy class from a Python file.

    The file must define a class named ``Strategy`` that inherits from
    the Strategy ABC.

    Args:
        file_path: Relative path to the strategy .py file.
        base_path: Base directory for resolving the file path.

    Returns:
        The Strategy subclass defined in the file.

    Raises:
        ValueError: If path escapes base, file missing, or invalid Strategy.
    '''

    if file_path.is_absolute():
        msg = f'Strategy file path must be relative: {file_path}'
        raise ValueError(msg)

    if file_path.suffix != '.py':
        msg = f'Strategy file must be a .py file: {file_path}'
        raise ValueError(msg)

    base_resolved = base_path.resolve()
    full_path = (base_resolved / file_path).resolve()

    try:
        full_path.relative_to(base_resolved)
    except ValueError as e:
        msg = f'Strategy file escapes base path: {full_path}'
        raise ValueError(msg) from e

    if not full_path.is_file():
        msg = f'Strategy file not found: {full_path}'
        raise ValueError(msg)

    module = _load_module(full_path)

    if not hasattr(module, 'Strategy'):
        msg = f'Strategy file missing Strategy class: {full_path}'
        raise ValueError(msg)

    strategy_class = module.Strategy

    if not isinstance(strategy_class, type):
        msg = f'Strategy attribute is not a class: {full_path}'
        raise ValueError(msg)

    if not issubclass(strategy_class, Strategy):
        msg = f'Strategy class does not inherit from Strategy ABC: {full_path}'
        raise ValueError(msg)

    if strategy_class is Strategy:
        msg = f'Strategy file exports the ABC, not a concrete subclass: {full_path}'
        raise ValueError(msg)

    return strategy_class


def _load_module(path: Path) -> ModuleType:
    '''Load a Python module from a file path.'''

    module_name = path.stem
    spec = importlib.util.spec_from_file_location(module_name, path)

    if spec is None or spec.loader is None:
        msg = f'Failed to load module spec: {path}'
        raise ValueError(msg)

    module = importlib.util.module_from_spec(spec)

    try:
        spec.loader.exec_module(module)
    except Exception as e:
        msg = f'Failed to execute strategy module: {path}'
        raise ValueError(msg) from e

    return module
