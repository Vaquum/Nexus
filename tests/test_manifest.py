'''Tests for Manifest dataclass and load_manifest function.'''

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from nexus.infrastructure.manifest import (
    Manifest,
    SensorSpec,
    StrategySpec,
    load_manifest,
)


def _pfn(tmp_path: Path) -> SensorSpec:
    exp_dir = tmp_path / 'experiment'
    exp_dir.mkdir(exist_ok=True)
    return SensorSpec(
        experiment_dir=exp_dir,
        permutation_ids=(1,),
        interval_seconds=60,
    )


def _make_spec(
    tmp_path: Path,
    strategy_id: str = 'test',
    capital_pct: Decimal = Decimal('50'),
) -> StrategySpec:
    return StrategySpec(
        strategy_id=strategy_id,
        file='test.py',
        sensors=(_pfn(tmp_path),),
        capital_pct=capital_pct,
    )


def _write_yaml(path: Path, content: str) -> None:
    path.write_text(content, encoding='utf-8')


def _write_strategy_file(base: Path, rel_path: str, content: str = '') -> None:
    file_path = base / rel_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content or '# Strategy file\n', encoding='utf-8')


def _yaml_sensors(tmp_path: Path) -> str:
    '''Return YAML sensors block pointing to a real experiment dir.'''

    exp_dir = tmp_path / 'experiment'
    exp_dir.mkdir(exist_ok=True)
    return (
        f'    sensors:\n'
        f'      - experiment: {exp_dir}\n'
        f'        permutation_ids: [1]\n'
        f'        interval_seconds: 60\n'
    )


class TestManifest:

    def test_valid_manifest(self, tmp_path: Path) -> None:
        '''Valid Manifest creates successfully.'''

        spec1 = _make_spec(tmp_path, 'strategy_a', Decimal('60'))
        spec2 = _make_spec(tmp_path, 'strategy_b', Decimal('40'))

        manifest = Manifest(
            capital_pool=Decimal('10000'),
            strategies=(spec1, spec2),
        )

        assert manifest.capital_pool == Decimal('10000')
        assert manifest.strategies == (spec1, spec2)

    def test_manifest_is_frozen(self, tmp_path: Path) -> None:
        '''Manifest is immutable.'''

        manifest = Manifest(
            capital_pool=Decimal('10000'),
            strategies=(_make_spec(tmp_path),),
        )

        with pytest.raises(AttributeError):
            manifest.capital_pool = Decimal('5000')  # type: ignore[misc]

    def test_non_decimal_capital_pool_raises(self, tmp_path: Path) -> None:
        '''Non-Decimal capital_pool raises ValueError.'''

        with pytest.raises(ValueError, match='capital_pool must be a finite Decimal'):
            Manifest(
                capital_pool=10000,  # type: ignore[arg-type]
                strategies=(_make_spec(tmp_path),),
            )

    def test_infinite_capital_pool_raises(self, tmp_path: Path) -> None:
        '''Infinite capital_pool raises ValueError.'''

        with pytest.raises(ValueError, match='capital_pool must be a finite Decimal'):
            Manifest(
                capital_pool=Decimal('inf'),
                strategies=(_make_spec(tmp_path),),
            )

    def test_nan_capital_pool_raises(self, tmp_path: Path) -> None:
        '''NaN capital_pool raises ValueError.'''

        with pytest.raises(ValueError, match='capital_pool must be a finite Decimal'):
            Manifest(
                capital_pool=Decimal('nan'),
                strategies=(_make_spec(tmp_path),),
            )

    def test_zero_capital_pool_raises(self, tmp_path: Path) -> None:
        '''Zero capital_pool raises ValueError.'''

        with pytest.raises(ValueError, match='capital_pool must be positive'):
            Manifest(
                capital_pool=Decimal('0'),
                strategies=(_make_spec(tmp_path),),
            )

    def test_negative_capital_pool_raises(self, tmp_path: Path) -> None:
        '''Negative capital_pool raises ValueError.'''

        with pytest.raises(ValueError, match='capital_pool must be positive'):
            Manifest(
                capital_pool=Decimal('-1000'),
                strategies=(_make_spec(tmp_path),),
            )

    def test_empty_strategies_raises(self) -> None:
        '''Empty strategies raises ValueError.'''

        with pytest.raises(ValueError, match='strategies must be a non-empty tuple'):
            Manifest(
                capital_pool=Decimal('10000'),
                strategies=(),
            )

    def test_strategies_not_tuple_raises(self, tmp_path: Path) -> None:
        '''Non-tuple strategies raises ValueError.'''

        with pytest.raises(ValueError, match='strategies must be a non-empty tuple'):
            Manifest(
                capital_pool=Decimal('10000'),
                strategies=[_make_spec(tmp_path)],  # type: ignore[arg-type]
            )

    def test_strategies_with_non_spec_raises(self, tmp_path: Path) -> None:
        '''Strategies containing non-StrategySpec raises ValueError.'''

        with pytest.raises(ValueError, match='strategies must contain StrategySpec'):
            Manifest(
                capital_pool=Decimal('10000'),
                strategies=(_make_spec(tmp_path), 'not a spec'),  # type: ignore[arg-type]
            )

    def test_duplicate_strategy_id_raises(self, tmp_path: Path) -> None:
        '''Duplicate strategy_id raises ValueError.'''

        with pytest.raises(ValueError, match='duplicate strategy_id'):
            Manifest(
                capital_pool=Decimal('10000'),
                strategies=(
                    _make_spec(tmp_path, 'same_id', Decimal('50')),
                    _make_spec(tmp_path, 'same_id', Decimal('50')),
                ),
            )

    def test_capital_pct_sum_over_100_raises(self, tmp_path: Path) -> None:
        '''capital_pct sum > 100 raises ValueError.'''

        with pytest.raises(ValueError, match=r'capital_pct sum .* exceeds 100'):
            Manifest(
                capital_pool=Decimal('10000'),
                strategies=(
                    _make_spec(tmp_path, 'a', Decimal('60')),
                    _make_spec(tmp_path, 'b', Decimal('50')),
                ),
            )

    def test_capital_pct_sum_exactly_100_allowed(self, tmp_path: Path) -> None:
        '''capital_pct sum of exactly 100 is allowed.'''

        manifest = Manifest(
            capital_pool=Decimal('10000'),
            strategies=(
                _make_spec(tmp_path, 'a', Decimal('60')),
                _make_spec(tmp_path, 'b', Decimal('40')),
            ),
        )

        assert manifest.capital_pool == Decimal('10000')

    def test_capital_pct_sum_under_100_allowed(self, tmp_path: Path) -> None:
        '''capital_pct sum under 100 is allowed.'''

        manifest = Manifest(
            capital_pool=Decimal('10000'),
            strategies=(
                _make_spec(tmp_path, 'a', Decimal('30')),
                _make_spec(tmp_path, 'b', Decimal('20')),
            ),
        )

        assert manifest.capital_pool == Decimal('10000')

    def test_single_strategy_allowed(self, tmp_path: Path) -> None:
        '''Single strategy in manifest is allowed.'''

        manifest = Manifest(
            capital_pool=Decimal('10000'),
            strategies=(_make_spec(tmp_path, 'only_one', Decimal('100')),),
        )

        assert len(manifest.strategies) == 1


class TestLoadManifest:

    def test_valid_manifest_loads(self, tmp_path: Path) -> None:
        '''Valid YAML manifest loads successfully.'''

        path = tmp_path / 'manifest.yaml'
        exp_dir = tmp_path / 'experiment'
        exp_dir.mkdir()

        _write_strategy_file(tmp_path, 'strategies/momentum.py')
        _write_strategy_file(tmp_path, 'strategies/mean_rev.py')

        _write_yaml(
            path,
            f'capital_pool: 10000\n'
            f'strategies:\n'
            f'  - id: momentum\n'
            f'    file: strategies/momentum.py\n'
            f'    sensors:\n'
            f'      - experiment: {exp_dir}\n'
            f'        permutation_ids: [1]\n'
            f'        interval_seconds: 60\n'
            f'    capital_pct: 60\n'
            f'  - id: mean_rev\n'
            f'    file: strategies/mean_rev.py\n'
            f'    sensors:\n'
            f'      - experiment: {exp_dir}\n'
            f'        permutation_ids: [2]\n'
            f'        interval_seconds: 300\n'
            f'    capital_pct: 40\n',
        )

        manifest = load_manifest(path, Decimal('20000'))

        assert manifest.capital_pool == Decimal('10000')
        assert len(manifest.strategies) == 2
        assert manifest.strategies[0].strategy_id == 'momentum'
        assert manifest.strategies[1].strategy_id == 'mean_rev'
        assert manifest.strategies[0].sensors[0].permutation_ids == (1,)
        assert manifest.strategies[0].sensors[0].interval_seconds == 60

    def test_file_not_found_raises(self) -> None:
        '''Missing manifest file raises FileNotFoundError.'''

        with pytest.raises(FileNotFoundError, match='Manifest file not found'):
            load_manifest(Path('/nonexistent/manifest.yaml'), Decimal('10000'))

    def test_capital_pool_exceeds_allocated_raises(self, tmp_path: Path) -> None:
        '''capital_pool > allocated_capital raises ValueError.'''

        path = tmp_path / 'manifest.yaml'
        exp_dir = tmp_path / 'experiment'
        exp_dir.mkdir()

        _write_yaml(
            path,
            f'capital_pool: 15000\n'
            f'strategies:\n'
            f'  - id: test\n'
            f'    file: test.py\n'
            f'    sensors:\n'
            f'      - experiment: {exp_dir}\n'
            f'        permutation_ids: [1]\n'
            f'        interval_seconds: 60\n'
            f'    capital_pct: 100\n',
        )

        with pytest.raises(ValueError, match='exceeds allocated_capital'):
            load_manifest(path, Decimal('10000'))

    def test_non_finite_allocated_capital_raises(self, tmp_path: Path) -> None:
        '''Non-finite allocated_capital raises ValueError.'''

        path = tmp_path / 'manifest.yaml'
        exp_dir = tmp_path / 'experiment'
        exp_dir.mkdir()
        strategy = tmp_path / 'test.py'
        strategy.write_text('pass')

        _write_yaml(
            path,
            f'capital_pool: 5000\n'
            f'strategies:\n'
            f'  - id: test\n'
            f'    file: test.py\n'
            f'    sensors:\n'
            f'      - experiment: {exp_dir}\n'
            f'        permutation_ids: [1]\n'
            f'        interval_seconds: 60\n'
            f'    capital_pct: 100\n',
        )

        with pytest.raises(ValueError, match='allocated_capital must be a finite'):
            load_manifest(path, Decimal('NaN'))

        with pytest.raises(ValueError, match='allocated_capital must be a finite'):
            load_manifest(path, Decimal('Infinity'))

    def test_missing_capital_pool_raises(self, tmp_path: Path) -> None:
        '''Missing capital_pool raises ValueError.'''

        path = tmp_path / 'manifest.yaml'
        exp_dir = tmp_path / 'experiment'
        exp_dir.mkdir()

        _write_yaml(
            path,
            f'strategies:\n'
            f'  - id: test\n'
            f'    file: test.py\n'
            f'    sensors:\n'
            f'      - experiment: {exp_dir}\n'
            f'        permutation_ids: [1]\n'
            f'        interval_seconds: 60\n'
            f'    capital_pct: 100\n',
        )

        with pytest.raises(ValueError, match='missing required field: capital_pool'):
            load_manifest(path, Decimal('10000'))

    def test_missing_strategies_raises(self, tmp_path: Path) -> None:
        '''Missing strategies raises ValueError.'''

        path = tmp_path / 'manifest.yaml'
        _write_yaml(path, 'capital_pool: 10000\n')

        with pytest.raises(ValueError, match='missing or empty strategies'):
            load_manifest(path, Decimal('20000'))

    def test_empty_strategies_raises(self, tmp_path: Path) -> None:
        '''Empty strategies list raises ValueError.'''

        path = tmp_path / 'manifest.yaml'
        _write_yaml(path, 'capital_pool: 10000\nstrategies: []\n')

        with pytest.raises(ValueError, match='missing or empty strategies'):
            load_manifest(path, Decimal('20000'))

    def test_strategy_missing_id_raises(self, tmp_path: Path) -> None:
        '''Strategy missing id raises ValueError.'''

        path = tmp_path / 'manifest.yaml'
        exp_dir = tmp_path / 'experiment'
        exp_dir.mkdir()

        _write_yaml(
            path,
            f'capital_pool: 10000\n'
            f'strategies:\n'
            f'  - file: test.py\n'
            f'    sensors:\n'
            f'      - experiment: {exp_dir}\n'
            f'        permutation_ids: [1]\n'
            f'        interval_seconds: 60\n'
            f'    capital_pct: 100\n',
        )

        with pytest.raises(ValueError, match='missing required field: id'):
            load_manifest(path, Decimal('20000'))

    def test_strategy_missing_file_raises(self, tmp_path: Path) -> None:
        '''Strategy missing file raises ValueError.'''

        path = tmp_path / 'manifest.yaml'
        exp_dir = tmp_path / 'experiment'
        exp_dir.mkdir()

        _write_yaml(
            path,
            f'capital_pool: 10000\n'
            f'strategies:\n'
            f'  - id: test\n'
            f'    sensors:\n'
            f'      - experiment: {exp_dir}\n'
            f'        permutation_ids: [1]\n'
            f'        interval_seconds: 60\n'
            f'    capital_pct: 100\n',
        )

        with pytest.raises(ValueError, match='missing required field: file'):
            load_manifest(path, Decimal('20000'))

    def test_strategy_missing_sensors_raises(self, tmp_path: Path) -> None:
        '''Strategy missing sensors raises ValueError.'''

        path = tmp_path / 'manifest.yaml'

        _write_yaml(
            path,
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - id: test\n'
            '    file: test.py\n'
            '    capital_pct: 100\n',
        )

        with pytest.raises(ValueError, match='missing or empty sensors'):
            load_manifest(path, Decimal('20000'))

    def test_strategy_missing_capital_pct_raises(self, tmp_path: Path) -> None:
        '''Strategy missing capital_pct raises ValueError.'''

        path = tmp_path / 'manifest.yaml'
        exp_dir = tmp_path / 'experiment'
        exp_dir.mkdir()

        _write_yaml(
            path,
            f'capital_pool: 10000\n'
            f'strategies:\n'
            f'  - id: test\n'
            f'    file: test.py\n'
            f'    sensors:\n'
            f'      - experiment: {exp_dir}\n'
            f'        permutation_ids: [1]\n'
            f'        interval_seconds: 60\n',
        )

        with pytest.raises(ValueError, match='missing required field: capital_pct'):
            load_manifest(path, Decimal('20000'))

    def test_malformed_yaml_raises(self, tmp_path: Path) -> None:
        '''Malformed YAML raises yaml.YAMLError.'''

        path = tmp_path / 'manifest.yaml'
        _write_yaml(path, 'capital_pool: [unclosed')

        with pytest.raises(yaml.YAMLError):
            load_manifest(path, Decimal('20000'))

    def test_non_mapping_yaml_raises(self, tmp_path: Path) -> None:
        '''Non-mapping YAML raises ValueError.'''

        path = tmp_path / 'manifest.yaml'
        _write_yaml(path, '- item1\n- item2\n')

        with pytest.raises(ValueError, match='must be a YAML mapping'):
            load_manifest(path, Decimal('20000'))

    def test_duplicate_strategy_id_raises(self, tmp_path: Path) -> None:
        '''Duplicate strategy_id raises ValueError.'''

        path = tmp_path / 'manifest.yaml'
        exp_dir = tmp_path / 'experiment'
        exp_dir.mkdir()

        _write_yaml(
            path,
            f'capital_pool: 10000\n'
            f'strategies:\n'
            f'  - id: same\n'
            f'    file: test1.py\n'
            f'    sensors:\n'
            f'      - experiment: {exp_dir}\n'
            f'        permutation_ids: [1]\n'
            f'        interval_seconds: 60\n'
            f'    capital_pct: 50\n'
            f'  - id: same\n'
            f'    file: test2.py\n'
            f'    sensors:\n'
            f'      - experiment: {exp_dir}\n'
            f'        permutation_ids: [2]\n'
            f'        interval_seconds: 60\n'
            f'    capital_pct: 50\n',
        )

        with pytest.raises(ValueError, match='duplicate strategy_id'):
            load_manifest(path, Decimal('20000'))

    def test_capital_pct_sum_over_100_raises(self, tmp_path: Path) -> None:
        '''capital_pct sum > 100 raises ValueError.'''

        path = tmp_path / 'manifest.yaml'
        exp_dir = tmp_path / 'experiment'
        exp_dir.mkdir()
        _write_strategy_file(tmp_path, 'a.py')
        _write_strategy_file(tmp_path, 'b.py')

        _write_yaml(
            path,
            f'capital_pool: 10000\n'
            f'strategies:\n'
            f'  - id: a\n'
            f'    file: a.py\n'
            f'    sensors:\n'
            f'      - experiment: {exp_dir}\n'
            f'        permutation_ids: [1]\n'
            f'        interval_seconds: 60\n'
            f'    capital_pct: 60\n'
            f'  - id: b\n'
            f'    file: b.py\n'
            f'    sensors:\n'
            f'      - experiment: {exp_dir}\n'
            f'        permutation_ids: [2]\n'
            f'        interval_seconds: 60\n'
            f'    capital_pct: 50\n',
        )

        with pytest.raises(ValueError, match='exceeds 100'):
            load_manifest(path, Decimal('20000'))

    def test_strategy_file_not_found_raises(self, tmp_path: Path) -> None:
        '''Missing strategy .py file raises ValueError with path.'''

        path = tmp_path / 'manifest.yaml'
        exp_dir = tmp_path / 'experiment'
        exp_dir.mkdir()

        _write_yaml(
            path,
            f'capital_pool: 10000\n'
            f'strategies:\n'
            f'  - id: missing_file\n'
            f'    file: nonexistent/strategy.py\n'
            f'    sensors:\n'
            f'      - experiment: {exp_dir}\n'
            f'        permutation_ids: [1]\n'
            f'        interval_seconds: 60\n'
            f'    capital_pct: 100\n',
        )

        with pytest.raises(ValueError, match=r"file not found.*nonexistent/strategy\.py"):
            load_manifest(path, Decimal('20000'))

    def test_strategy_file_syntax_error_raises(self, tmp_path: Path) -> None:
        '''Invalid Python syntax in strategy file raises ValueError.'''

        path = tmp_path / 'manifest.yaml'
        exp_dir = tmp_path / 'experiment'
        exp_dir.mkdir()

        strategy_file = tmp_path / 'bad_syntax.py'
        strategy_file.write_text('def broken(\n')

        _write_yaml(
            path,
            f'capital_pool: 10000\n'
            f'strategies:\n'
            f'  - id: bad_syntax\n'
            f'    file: bad_syntax.py\n'
            f'    sensors:\n'
            f'      - experiment: {exp_dir}\n'
            f'        permutation_ids: [1]\n'
            f'        interval_seconds: 60\n'
            f'    capital_pct: 100\n',
        )

        with pytest.raises(ValueError, match=r'syntax error.*bad_syntax\.py'):
            load_manifest(path, Decimal('20000'))

    def test_invalid_capital_pool_decimal_raises(self, tmp_path: Path) -> None:
        '''Non-numeric capital_pool raises ValueError.'''

        path = tmp_path / 'manifest.yaml'
        exp_dir = tmp_path / 'experiment'
        exp_dir.mkdir()
        _write_strategy_file(tmp_path, 'test.py')

        _write_yaml(
            path,
            f'capital_pool: not_a_number\n'
            f'strategies:\n'
            f'  - id: test\n'
            f'    file: test.py\n'
            f'    sensors:\n'
            f'      - experiment: {exp_dir}\n'
            f'        permutation_ids: [1]\n'
            f'        interval_seconds: 60\n'
            f'    capital_pct: 100\n',
        )

        with pytest.raises(ValueError, match='capital_pool is not a valid number'):
            load_manifest(path, Decimal('20000'))

    def test_invalid_capital_pct_decimal_raises(self, tmp_path: Path) -> None:
        '''Non-numeric capital_pct raises ValueError.'''

        path = tmp_path / 'manifest.yaml'
        exp_dir = tmp_path / 'experiment'
        exp_dir.mkdir()
        _write_strategy_file(tmp_path, 'test.py')

        _write_yaml(
            path,
            f'capital_pool: 10000\n'
            f'strategies:\n'
            f'  - id: test\n'
            f'    file: test.py\n'
            f'    sensors:\n'
            f'      - experiment: {exp_dir}\n'
            f'        permutation_ids: [1]\n'
            f'        interval_seconds: 60\n'
            f'    capital_pct: invalid\n',
        )

        with pytest.raises(ValueError, match='capital_pct is not a valid number'):
            load_manifest(path, Decimal('20000'))

    def test_absolute_file_path_raises(self, tmp_path: Path) -> None:
        '''Absolute strategy file path raises ValueError.'''

        path = tmp_path / 'manifest.yaml'
        exp_dir = tmp_path / 'experiment'
        exp_dir.mkdir()

        _write_yaml(
            path,
            f'capital_pool: 10000\n'
            f'strategies:\n'
            f'  - id: absolute\n'
            f'    file: /etc/passwd\n'
            f'    sensors:\n'
            f'      - experiment: {exp_dir}\n'
            f'        permutation_ids: [1]\n'
            f'        interval_seconds: 60\n'
            f'    capital_pct: 100\n',
        )

        with pytest.raises(ValueError, match='file must be relative'):
            load_manifest(path, Decimal('20000'))

    def test_path_traversal_raises(self, tmp_path: Path) -> None:
        '''Strategy file path escaping base raises ValueError.'''

        path = tmp_path / 'manifest.yaml'
        exp_dir = tmp_path / 'experiment'
        exp_dir.mkdir()

        _write_yaml(
            path,
            f'capital_pool: 10000\n'
            f'strategies:\n'
            f'  - id: escape\n'
            f'    file: ../../../etc/passwd\n'
            f'    sensors:\n'
            f'      - experiment: {exp_dir}\n'
            f'        permutation_ids: [1]\n'
            f'        interval_seconds: 60\n'
            f'    capital_pct: 100\n',
        )

        with pytest.raises(ValueError, match='escapes base path'):
            load_manifest(path, Decimal('20000'))

    def test_directory_instead_of_file_raises(self, tmp_path: Path) -> None:
        '''Directory path instead of file raises ValueError.'''

        path = tmp_path / 'manifest.yaml'
        exp_dir = tmp_path / 'experiment'
        exp_dir.mkdir()
        (tmp_path / 'a_directory').mkdir()

        _write_yaml(
            path,
            f'capital_pool: 10000\n'
            f'strategies:\n'
            f'  - id: dir_not_file\n'
            f'    file: a_directory\n'
            f'    sensors:\n'
            f'      - experiment: {exp_dir}\n'
            f'        permutation_ids: [1]\n'
            f'        interval_seconds: 60\n'
            f'    capital_pct: 100\n',
        )

        with pytest.raises(ValueError, match='file not found'):
            load_manifest(path, Decimal('20000'))


class TestLoadManifestTimers:

    def test_manifest_with_timers_loads(self, tmp_path: Path) -> None:
        '''Manifest with valid timers parses correctly.'''

        path = tmp_path / 'manifest.yaml'
        exp_dir = tmp_path / 'experiment'
        exp_dir.mkdir()
        _write_strategy_file(tmp_path, 'strat.py')

        _write_yaml(
            path,
            f'capital_pool: 10000\n'
            f'strategies:\n'
            f'  - id: strat1\n'
            f'    file: strat.py\n'
            f'    sensors:\n'
            f'      - experiment: {exp_dir}\n'
            f'        permutation_ids: [1]\n'
            f'        interval_seconds: 60\n'
            f'    capital_pct: 100\n'
            f'    timers:\n'
            f'      - id: trailing_stop\n'
            f'        interval_seconds: 30\n'
            f'      - id: position_review\n'
            f'        interval_seconds: 300\n',
        )

        manifest = load_manifest(path, Decimal('20000'))

        assert len(manifest.strategies[0].timers) == 2
        assert manifest.strategies[0].timers[0].timer_id == 'trailing_stop'
        assert manifest.strategies[0].timers[1].interval_seconds == 300

    def test_manifest_without_timers_loads(self, tmp_path: Path) -> None:
        '''Manifest without timers field defaults to empty tuple.'''

        path = tmp_path / 'manifest.yaml'
        exp_dir = tmp_path / 'experiment'
        exp_dir.mkdir()
        _write_strategy_file(tmp_path, 'strat.py')

        _write_yaml(
            path,
            f'capital_pool: 10000\n'
            f'strategies:\n'
            f'  - id: strat1\n'
            f'    file: strat.py\n'
            f'    sensors:\n'
            f'      - experiment: {exp_dir}\n'
            f'        permutation_ids: [1]\n'
            f'        interval_seconds: 60\n'
            f'    capital_pct: 100\n',
        )

        manifest = load_manifest(path, Decimal('20000'))

        assert manifest.strategies[0].timers == ()

    def test_timer_missing_id_raises(self, tmp_path: Path) -> None:
        '''Timer missing id raises ValueError.'''

        path = tmp_path / 'manifest.yaml'
        exp_dir = tmp_path / 'experiment'
        exp_dir.mkdir()
        _write_strategy_file(tmp_path, 'strat.py')

        _write_yaml(
            path,
            f'capital_pool: 10000\n'
            f'strategies:\n'
            f'  - id: strat1\n'
            f'    file: strat.py\n'
            f'    sensors:\n'
            f'      - experiment: {exp_dir}\n'
            f'        permutation_ids: [1]\n'
            f'        interval_seconds: 60\n'
            f'    capital_pct: 100\n'
            f'    timers:\n'
            f'      - interval_seconds: 30\n',
        )

        with pytest.raises(ValueError, match='timer missing required field: id'):
            load_manifest(path, Decimal('20000'))

    def test_timer_bool_interval_raises(self, tmp_path: Path) -> None:
        '''Timer with bool interval_seconds raises ValueError.'''

        path = tmp_path / 'manifest.yaml'
        exp_dir = tmp_path / 'experiment'
        exp_dir.mkdir()
        _write_strategy_file(tmp_path, 'strat.py')

        _write_yaml(
            path,
            f'capital_pool: 10000\n'
            f'strategies:\n'
            f'  - id: strat1\n'
            f'    file: strat.py\n'
            f'    sensors:\n'
            f'      - experiment: {exp_dir}\n'
            f'        permutation_ids: [1]\n'
            f'        interval_seconds: 60\n'
            f'    capital_pct: 100\n'
            f'    timers:\n'
            f'      - id: check\n'
            f'        interval_seconds: true\n',
        )

        with pytest.raises(ValueError, match='interval_seconds must be an int'):
            load_manifest(path, Decimal('20000'))

    def test_duplicate_timer_id_raises(self, tmp_path: Path) -> None:
        '''Duplicate timer_id raises ValueError.'''

        path = tmp_path / 'manifest.yaml'
        exp_dir = tmp_path / 'experiment'
        exp_dir.mkdir()
        _write_strategy_file(tmp_path, 'strat.py')

        _write_yaml(
            path,
            f'capital_pool: 10000\n'
            f'strategies:\n'
            f'  - id: strat1\n'
            f'    file: strat.py\n'
            f'    sensors:\n'
            f'      - experiment: {exp_dir}\n'
            f'        permutation_ids: [1]\n'
            f'        interval_seconds: 60\n'
            f'    capital_pct: 100\n'
            f'    timers:\n'
            f'      - id: check\n'
            f'        interval_seconds: 30\n'
            f'      - id: check\n'
            f'        interval_seconds: 60\n',
        )

        with pytest.raises(ValueError, match='duplicate timer_id'):
            load_manifest(path, Decimal('20000'))
