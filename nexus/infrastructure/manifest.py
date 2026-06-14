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

__all__ = ['Manifest', 'SignalSpec', 'StrategySpec', 'TimerSpec', 'load_manifest']

_ZERO = Decimal('0')
_ONE_HUNDRED = Decimal('100')


@dataclass(frozen=True)
class SignalSpec:
    '''Specification binding a strategy to a Conduit prediction series.

    Args:
        series: Conduit series identifier (e.g. 'time_15m') read from the
            serving manifest.
        interval_seconds: How often to poll Conduit for a new prediction.
        stale_policy: Behaviour when no fresh usable prediction is
            available. Only 'skip' is supported.
        name: Optional human-readable cohort/series label for logs.
    '''

    series: str
    interval_seconds: int
    stale_policy: str = 'skip'
    name: str | None = None

    def __post_init__(self) -> None:
        '''Validate signal specification invariants.'''

        if (
            not isinstance(self.series, str)
            or not self.series.strip()
            or self.series != self.series.strip()
        ):
            msg = 'SignalSpec.series must be a non-empty string without surrounding whitespace'
            raise ValueError(msg)

        if not isinstance(self.interval_seconds, int) or isinstance(self.interval_seconds, bool):
            msg = 'SignalSpec.interval_seconds must be an int'
            raise ValueError(msg)

        if self.interval_seconds <= 0:
            msg = f'SignalSpec.interval_seconds must be positive: {self.interval_seconds}'
            raise ValueError(msg)

        if self.stale_policy != 'skip':
            msg = f"SignalSpec.stale_policy must be 'skip', got {self.stale_policy!r}"
            raise ValueError(msg)

        if self.name is not None and not isinstance(self.name, str):
            msg = 'SignalSpec.name must be a string or None'
            raise ValueError(msg)


@dataclass(frozen=True)
class TimerSpec:
    '''Specification for a strategy timer.

    Args:
        timer_id: Unique identifier for this timer within the strategy.
        interval_seconds: How often the timer fires in seconds.
    '''

    timer_id: str
    interval_seconds: int

    def __post_init__(self) -> None:
        '''Validate timer specification invariants.'''

        if (
            not isinstance(self.timer_id, str)
            or not self.timer_id.strip()
            or self.timer_id != self.timer_id.strip()
        ):
            msg = 'TimerSpec.timer_id must be a non-empty string without surrounding whitespace'
            raise ValueError(msg)

        if not isinstance(self.interval_seconds, int) or isinstance(self.interval_seconds, bool):
            msg = 'TimerSpec.interval_seconds must be an int'
            raise ValueError(msg)

        if self.interval_seconds <= 0:
            msg = f'TimerSpec.interval_seconds must be positive: {self.interval_seconds}'
            raise ValueError(msg)


@dataclass(frozen=True)
class StrategySpec:
    '''Immutable specification for a single strategy.

    Args:
        strategy_id: Unique identifier for this strategy.
        file: Relative path string to the Python strategy implementation.
        signal: Conduit signal-source specification.
        capital_pct: Capital allocation percentage for this strategy.
        timers: Optional timer specifications for on_timer callbacks.
    '''

    strategy_id: str
    file: str
    signal: SignalSpec
    capital_pct: Decimal
    timers: tuple[TimerSpec, ...] = ()

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

        if not isinstance(self.signal, SignalSpec):
            msg = 'StrategySpec.signal must be a SignalSpec instance'
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

        if not isinstance(self.timers, tuple):
            msg = 'StrategySpec.timers must be a tuple'
            raise ValueError(msg)

        seen_timer_ids: set[str] = set()

        for t in self.timers:
            if not isinstance(t, TimerSpec):
                msg = 'StrategySpec.timers must contain TimerSpec instances'
                raise ValueError(msg)

            if t.timer_id in seen_timer_ids:
                msg = f'StrategySpec.timers contains duplicate timer_id: {t.timer_id!r}'
                raise ValueError(msg)

            seen_timer_ids.add(t.timer_id)


@dataclass(frozen=True)
class Manifest:
    '''Immutable manifest for a Manager instance.

    Args:
        account_id: Trading account this Manager instance is bound to.
        allocated_capital: Hard infrastructure ceiling on capital this
            instance can use, in quote asset. The manifest's capital_pool
            must not exceed this value.
        capital_pool: Operational allocation in quote asset for this instance.
        strategies: Strategy specifications for this instance.
    '''

    account_id: str
    allocated_capital: Decimal
    capital_pool: Decimal
    strategies: tuple[StrategySpec, ...]

    def __post_init__(self) -> None:
        '''Validate manifest invariants.'''

        if not isinstance(self.account_id, str) or not self.account_id.strip():
            msg = 'Manifest.account_id must be a non-empty string'
            raise ValueError(msg)

        object.__setattr__(self, 'account_id', self.account_id.strip())

        if (
            not isinstance(self.allocated_capital, Decimal)
            or not self.allocated_capital.is_finite()
        ):
            msg = 'Manifest.allocated_capital must be a finite Decimal'
            raise ValueError(msg)

        if self.allocated_capital <= _ZERO:
            msg = 'Manifest.allocated_capital must be positive'
            raise ValueError(msg)

        if (
            not isinstance(self.capital_pool, Decimal)
            or not self.capital_pool.is_finite()
        ):
            msg = 'Manifest.capital_pool must be a finite Decimal'
            raise ValueError(msg)

        if self.capital_pool <= _ZERO:
            msg = 'Manifest.capital_pool must be positive'
            raise ValueError(msg)

        if self.capital_pool > self.allocated_capital:
            msg = (
                f'Manifest.capital_pool {self.capital_pool} exceeds '
                f'allocated_capital {self.allocated_capital}'
            )
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


def load_manifest(path: Path) -> Manifest:
    '''Load and validate a manifest from a YAML file.

    Args:
        path: Path to the YAML manifest file.

    Returns:
        Validated Manifest instance.

    Raises:
        ValueError: If manifest is invalid (missing fields, capital_pool >
            allocated_capital, etc.).
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

    raw_account_id = data.get('account_id')
    if not isinstance(raw_account_id, str) or not raw_account_id.strip():
        msg = 'Manifest missing or invalid required field: account_id'
        raise ValueError(msg)

    account_id = raw_account_id.strip()

    raw_allocated_capital = data.get('allocated_capital')
    if raw_allocated_capital is None:
        msg = 'Manifest missing required field: allocated_capital'
        raise ValueError(msg)

    try:
        allocated_capital = Decimal(str(raw_allocated_capital))
    except InvalidOperation as e:
        msg = (
            f'Manifest allocated_capital is not a valid number: '
            f'{raw_allocated_capital!r}'
        )
        raise ValueError(msg) from e

    if not allocated_capital.is_finite() or allocated_capital <= _ZERO:
        msg = (
            f'Manifest allocated_capital must be a finite positive number: '
            f'{allocated_capital}'
        )
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

        raw_signal = raw_spec.get('signal')
        if not isinstance(raw_signal, dict):
            msg = f'Strategy {strategy_id!r} missing or invalid signal mapping'
            raise ValueError(msg)

        raw_series = raw_signal.get('series')
        if raw_series is None:
            msg = f'Strategy {strategy_id!r} signal missing required field: series'
            raise ValueError(msg)

        if not isinstance(raw_series, str):
            msg = f'Strategy {strategy_id!r} signal series must be a string, got {raw_series!r}'
            raise ValueError(msg)

        raw_interval = raw_signal.get('interval_seconds')
        if raw_interval is None:
            msg = f'Strategy {strategy_id!r} signal missing required field: interval_seconds'
            raise ValueError(msg)

        if isinstance(raw_interval, bool) or not isinstance(raw_interval, int):
            msg = f'Strategy {strategy_id!r} signal interval_seconds must be an int, got {raw_interval!r}'
            raise ValueError(msg)

        raw_stale_policy = raw_signal.get('stale_policy', 'skip')
        raw_name = raw_signal.get('name')

        signal_spec = SignalSpec(
            series=raw_series,
            interval_seconds=raw_interval,
            stale_policy=raw_stale_policy,
            name=raw_name,
        )

        timer_specs: list[TimerSpec] = []
        raw_timers = raw_spec.get('timers', [])

        if not isinstance(raw_timers, list):
            msg = f'Strategy {strategy_id!r} timers must be a list'
            raise ValueError(msg)

        for raw_timer in raw_timers:
            if not isinstance(raw_timer, dict):
                msg = f'Strategy {strategy_id!r} timer entries must be mappings'
                raise ValueError(msg)

            timer_id = raw_timer.get('id')
            if timer_id is None:
                msg = f'Strategy {strategy_id!r} timer missing required field: id'
                raise ValueError(msg)

            timer_interval = raw_timer.get('interval_seconds')
            if timer_interval is None:
                msg = f'Strategy {strategy_id!r} timer missing required field: interval_seconds'
                raise ValueError(msg)

            if isinstance(timer_interval, bool) or not isinstance(timer_interval, int):
                msg = f'Strategy {strategy_id!r} timer interval_seconds must be an int, got {timer_interval!r}'
                raise ValueError(msg)

            if not isinstance(timer_id, str):
                msg = f'Strategy {strategy_id!r} timer id must be a string, got {timer_id!r}'
                raise ValueError(msg)

            timer_specs.append(
                TimerSpec(timer_id=timer_id, interval_seconds=timer_interval)
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
                signal=signal_spec,
                capital_pct=capital_pct,
                timers=tuple(timer_specs),
            )
        )

    manifest = Manifest(
        account_id=account_id,
        allocated_capital=allocated_capital,
        capital_pool=capital_pool,
        strategies=tuple(specs),
    )

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
