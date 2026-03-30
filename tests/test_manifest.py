'''Tests for manifest loading and validation.'''

from __future__ import annotations

import tempfile
from decimal import Decimal
from pathlib import Path

import pytest

from nexus.infrastructure.manifest import Manifest, StrategySpec, load_manifest


class TestStrategySpec:
    '''Tests for StrategySpec dataclass.'''

    def test_valid_strategy_spec(self) -> None:
        '''Valid StrategySpec creates successfully.'''

        spec = StrategySpec(
            strategy_id='momentum_v1',
            file='strategies/momentum.py',
            predictor_fn_ids=('cohort_alpha_v3', 'sensor_volatility'),
            capital_pct=Decimal('25'),
        )

        assert spec.strategy_id == 'momentum_v1'
        assert spec.file == 'strategies/momentum.py'
        assert spec.predictor_fn_ids == ('cohort_alpha_v3', 'sensor_volatility')
        assert spec.capital_pct == Decimal('25')

    def test_strategy_spec_is_frozen(self) -> None:
        '''StrategySpec is immutable.'''

        spec = StrategySpec(
            strategy_id='test',
            file='test.py',
            predictor_fn_ids=('pred1',),
            capital_pct=Decimal('10'),
        )

        with pytest.raises(AttributeError):
            spec.strategy_id = 'changed'  # type: ignore[misc]

    def test_empty_strategy_id_raises(self) -> None:
        '''Empty strategy_id raises ValueError.'''

        with pytest.raises(ValueError, match='strategy_id must be a non-empty string'):
            StrategySpec(
                strategy_id='',
                file='test.py',
                predictor_fn_ids=('pred1',),
                capital_pct=Decimal('10'),
            )

    def test_whitespace_strategy_id_raises(self) -> None:
        '''Whitespace-only strategy_id raises ValueError.'''

        with pytest.raises(ValueError, match='strategy_id must be a non-empty string'):
            StrategySpec(
                strategy_id='   ',
                file='test.py',
                predictor_fn_ids=('pred1',),
                capital_pct=Decimal('10'),
            )

    def test_empty_file_raises(self) -> None:
        '''Empty file raises ValueError.'''

        with pytest.raises(ValueError, match='file must be a non-empty string'):
            StrategySpec(
                strategy_id='test',
                file='',
                predictor_fn_ids=('pred1',),
                capital_pct=Decimal('10'),
            )

    def test_whitespace_file_raises(self) -> None:
        '''Whitespace-only file raises ValueError.'''

        with pytest.raises(ValueError, match='file must be a non-empty string'):
            StrategySpec(
                strategy_id='test',
                file='  ',
                predictor_fn_ids=('pred1',),
                capital_pct=Decimal('10'),
            )

    def test_empty_predictor_fn_ids_raises(self) -> None:
        '''Empty predictor_fn_ids raises ValueError.'''

        with pytest.raises(ValueError, match='predictor_fn_ids must be a non-empty tuple'):
            StrategySpec(
                strategy_id='test',
                file='test.py',
                predictor_fn_ids=(),
                capital_pct=Decimal('10'),
            )

    def test_predictor_fn_ids_not_tuple_raises(self) -> None:
        '''Non-tuple predictor_fn_ids raises ValueError.'''

        with pytest.raises(ValueError, match='predictor_fn_ids must be a non-empty tuple'):
            StrategySpec(
                strategy_id='test',
                file='test.py',
                predictor_fn_ids=['pred1'],  # type: ignore[arg-type]
                capital_pct=Decimal('10'),
            )

    def test_predictor_fn_ids_with_empty_string_raises(self) -> None:
        '''predictor_fn_ids containing empty string raises ValueError.'''

        with pytest.raises(ValueError, match='predictor_fn_ids must contain non-empty strings'):
            StrategySpec(
                strategy_id='test',
                file='test.py',
                predictor_fn_ids=('pred1', ''),
                capital_pct=Decimal('10'),
            )

    def test_predictor_fn_ids_with_whitespace_raises(self) -> None:
        '''predictor_fn_ids containing whitespace-only string raises ValueError.'''

        with pytest.raises(ValueError, match='predictor_fn_ids must contain non-empty strings'):
            StrategySpec(
                strategy_id='test',
                file='test.py',
                predictor_fn_ids=('pred1', '   '),
                capital_pct=Decimal('10'),
            )

    def test_predictor_fn_ids_with_non_string_raises(self) -> None:
        '''predictor_fn_ids containing non-string raises ValueError.'''

        with pytest.raises(ValueError, match='predictor_fn_ids must contain non-empty strings'):
            StrategySpec(
                strategy_id='test',
                file='test.py',
                predictor_fn_ids=('pred1', 123),  # type: ignore[arg-type]
                capital_pct=Decimal('10'),
            )

    def test_non_decimal_capital_pct_raises(self) -> None:
        '''Non-Decimal capital_pct raises ValueError.'''

        with pytest.raises(ValueError, match='capital_pct must be a finite Decimal'):
            StrategySpec(
                strategy_id='test',
                file='test.py',
                predictor_fn_ids=('pred1',),
                capital_pct=25,  # type: ignore[arg-type]
            )

    def test_infinite_capital_pct_raises(self) -> None:
        '''Infinite capital_pct raises ValueError.'''

        with pytest.raises(ValueError, match='capital_pct must be a finite Decimal'):
            StrategySpec(
                strategy_id='test',
                file='test.py',
                predictor_fn_ids=('pred1',),
                capital_pct=Decimal('inf'),
            )

    def test_nan_capital_pct_raises(self) -> None:
        '''NaN capital_pct raises ValueError.'''

        with pytest.raises(ValueError, match='capital_pct must be a finite Decimal'):
            StrategySpec(
                strategy_id='test',
                file='test.py',
                predictor_fn_ids=('pred1',),
                capital_pct=Decimal('nan'),
            )

    def test_zero_capital_pct_raises(self) -> None:
        '''Zero capital_pct raises ValueError.'''

        with pytest.raises(ValueError, match='capital_pct must be in'):
            StrategySpec(
                strategy_id='test',
                file='test.py',
                predictor_fn_ids=('pred1',),
                capital_pct=Decimal('0'),
            )

    def test_negative_capital_pct_raises(self) -> None:
        '''Negative capital_pct raises ValueError.'''

        with pytest.raises(ValueError, match='capital_pct must be in'):
            StrategySpec(
                strategy_id='test',
                file='test.py',
                predictor_fn_ids=('pred1',),
                capital_pct=Decimal('-5'),
            )

    def test_capital_pct_over_100_raises(self) -> None:
        '''capital_pct > 100 raises ValueError.'''

        with pytest.raises(ValueError, match='capital_pct must be in'):
            StrategySpec(
                strategy_id='test',
                file='test.py',
                predictor_fn_ids=('pred1',),
                capital_pct=Decimal('101'),
            )

    def test_capital_pct_exactly_100_allowed(self) -> None:
        '''capital_pct of exactly 100 is allowed.'''

        spec = StrategySpec(
            strategy_id='test',
            file='test.py',
            predictor_fn_ids=('pred1',),
            capital_pct=Decimal('100'),
        )

        assert spec.capital_pct == Decimal('100')

    def test_capital_pct_fractional_allowed(self) -> None:
        '''Fractional capital_pct is allowed.'''

        spec = StrategySpec(
            strategy_id='test',
            file='test.py',
            predictor_fn_ids=('pred1',),
            capital_pct=Decimal('12.5'),
        )

        assert spec.capital_pct == Decimal('12.5')

    def test_single_predictor_fn_allowed(self) -> None:
        '''Single predictor_fn in tuple is allowed.'''

        spec = StrategySpec(
            strategy_id='test',
            file='test.py',
            predictor_fn_ids=('single_pred',),
            capital_pct=Decimal('50'),
        )

        assert spec.predictor_fn_ids == ('single_pred',)

    def test_multiple_predictor_fn_ids_allowed(self) -> None:
        '''Multiple predictor_fn_ids are allowed.'''

        spec = StrategySpec(
            strategy_id='test',
            file='test.py',
            predictor_fn_ids=('pred1', 'pred2', 'pred3'),
            capital_pct=Decimal('50'),
        )

        assert spec.predictor_fn_ids == ('pred1', 'pred2', 'pred3')


def _make_spec(
    strategy_id: str = 'test',
    capital_pct: Decimal = Decimal('50'),
) -> StrategySpec:
    '''Create a valid StrategySpec for testing.'''

    return StrategySpec(
        strategy_id=strategy_id,
        file='test.py',
        predictor_fn_ids=('pred1',),
        capital_pct=capital_pct,
    )


class TestManifest:
    '''Tests for Manifest dataclass.'''

    def test_valid_manifest(self) -> None:
        '''Valid Manifest creates successfully.'''

        spec1 = _make_spec('strategy_a', Decimal('60'))
        spec2 = _make_spec('strategy_b', Decimal('40'))

        manifest = Manifest(
            capital_pool=Decimal('10000'),
            strategies=(spec1, spec2),
        )

        assert manifest.capital_pool == Decimal('10000')
        assert manifest.strategies == (spec1, spec2)

    def test_manifest_is_frozen(self) -> None:
        '''Manifest is immutable.'''

        manifest = Manifest(
            capital_pool=Decimal('10000'),
            strategies=(_make_spec(),),
        )

        with pytest.raises(AttributeError):
            manifest.capital_pool = Decimal('5000')  # type: ignore[misc]

    def test_non_decimal_capital_pool_raises(self) -> None:
        '''Non-Decimal capital_pool raises ValueError.'''

        with pytest.raises(ValueError, match='capital_pool must be a finite Decimal'):
            Manifest(
                capital_pool=10000,  # type: ignore[arg-type]
                strategies=(_make_spec(),),
            )

    def test_infinite_capital_pool_raises(self) -> None:
        '''Infinite capital_pool raises ValueError.'''

        with pytest.raises(ValueError, match='capital_pool must be a finite Decimal'):
            Manifest(
                capital_pool=Decimal('inf'),
                strategies=(_make_spec(),),
            )

    def test_nan_capital_pool_raises(self) -> None:
        '''NaN capital_pool raises ValueError.'''

        with pytest.raises(ValueError, match='capital_pool must be a finite Decimal'):
            Manifest(
                capital_pool=Decimal('nan'),
                strategies=(_make_spec(),),
            )

    def test_zero_capital_pool_raises(self) -> None:
        '''Zero capital_pool raises ValueError.'''

        with pytest.raises(ValueError, match='capital_pool must be positive'):
            Manifest(
                capital_pool=Decimal('0'),
                strategies=(_make_spec(),),
            )

    def test_negative_capital_pool_raises(self) -> None:
        '''Negative capital_pool raises ValueError.'''

        with pytest.raises(ValueError, match='capital_pool must be positive'):
            Manifest(
                capital_pool=Decimal('-1000'),
                strategies=(_make_spec(),),
            )

    def test_empty_strategies_raises(self) -> None:
        '''Empty strategies raises ValueError.'''

        with pytest.raises(ValueError, match='strategies must be a non-empty tuple'):
            Manifest(
                capital_pool=Decimal('10000'),
                strategies=(),
            )

    def test_strategies_not_tuple_raises(self) -> None:
        '''Non-tuple strategies raises ValueError.'''

        with pytest.raises(ValueError, match='strategies must be a non-empty tuple'):
            Manifest(
                capital_pool=Decimal('10000'),
                strategies=[_make_spec()],  # type: ignore[arg-type]
            )

    def test_strategies_with_non_spec_raises(self) -> None:
        '''Strategies containing non-StrategySpec raises ValueError.'''

        with pytest.raises(ValueError, match='strategies must contain StrategySpec'):
            Manifest(
                capital_pool=Decimal('10000'),
                strategies=(_make_spec(), 'not a spec'),  # type: ignore[arg-type]
            )

    def test_duplicate_strategy_id_raises(self) -> None:
        '''Duplicate strategy_id raises ValueError.'''

        with pytest.raises(ValueError, match='duplicate strategy_id'):
            Manifest(
                capital_pool=Decimal('10000'),
                strategies=(
                    _make_spec('same_id', Decimal('50')),
                    _make_spec('same_id', Decimal('50')),
                ),
            )

    def test_capital_pct_sum_over_100_raises(self) -> None:
        '''capital_pct sum > 100 raises ValueError.'''

        with pytest.raises(ValueError, match=r'capital_pct sum .* exceeds 100'):
            Manifest(
                capital_pool=Decimal('10000'),
                strategies=(
                    _make_spec('a', Decimal('60')),
                    _make_spec('b', Decimal('50')),
                ),
            )

    def test_capital_pct_sum_exactly_100_allowed(self) -> None:
        '''capital_pct sum of exactly 100 is allowed.'''

        manifest = Manifest(
            capital_pool=Decimal('10000'),
            strategies=(
                _make_spec('a', Decimal('60')),
                _make_spec('b', Decimal('40')),
            ),
        )

        assert manifest.capital_pool == Decimal('10000')

    def test_capital_pct_sum_under_100_allowed(self) -> None:
        '''capital_pct sum under 100 is allowed.'''

        manifest = Manifest(
            capital_pool=Decimal('10000'),
            strategies=(
                _make_spec('a', Decimal('30')),
                _make_spec('b', Decimal('20')),
            ),
        )

        assert manifest.capital_pool == Decimal('10000')

    def test_single_strategy_allowed(self) -> None:
        '''Single strategy in manifest is allowed.'''

        manifest = Manifest(
            capital_pool=Decimal('10000'),
            strategies=(_make_spec('only_one', Decimal('100')),),
        )

        assert len(manifest.strategies) == 1


def _write_yaml(path: Path, content: str) -> None:
    '''Write YAML content to a file.'''

    path.write_text(content)


def _write_strategy_file(base: Path, rel_path: str, content: str = '') -> None:
    '''Create a strategy .py file.'''

    file_path = base / rel_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content or '# Strategy file\n')


class TestLoadManifest:
    '''Tests for load_manifest function.'''

    def test_valid_manifest_loads(self) -> None:
        '''Valid YAML manifest loads successfully.'''

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            path = tmp_path / 'manifest.yaml'

            _write_strategy_file(tmp_path, 'strategies/momentum.py')
            _write_strategy_file(tmp_path, 'strategies/mean_rev.py')

            _write_yaml(
                path,
                '''
capital_pool: 10000
strategies:
  - id: momentum
    file: strategies/momentum.py
    predictor_fn_ids:
      - alpha_v1
    capital_pct: 60
  - id: mean_rev
    file: strategies/mean_rev.py
    predictor_fn_ids:
      - sensor_vol
    capital_pct: 40
''',
            )

            manifest = load_manifest(path, Decimal('20000'))

            assert manifest.capital_pool == Decimal('10000')
            assert len(manifest.strategies) == 2
            assert manifest.strategies[0].strategy_id == 'momentum'
            assert manifest.strategies[1].strategy_id == 'mean_rev'

    def test_file_not_found_raises(self) -> None:
        '''Missing manifest file raises FileNotFoundError.'''

        with pytest.raises(FileNotFoundError, match='Manifest file not found'):
            load_manifest(Path('/nonexistent/manifest.yaml'), Decimal('10000'))

    def test_capital_pool_exceeds_allocated_raises(self) -> None:
        '''capital_pool > allocated_capital raises ValueError.'''

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'manifest.yaml'
            _write_yaml(
                path,
                '''
capital_pool: 15000
strategies:
  - id: test
    file: test.py
    predictor_fn_ids: [pred1]
    capital_pct: 100
''',
            )

            with pytest.raises(ValueError, match='exceeds allocated_capital'):
                load_manifest(path, Decimal('10000'))

    def test_missing_capital_pool_raises(self) -> None:
        '''Missing capital_pool raises ValueError.'''

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'manifest.yaml'
            _write_yaml(
                path,
                '''
strategies:
  - id: test
    file: test.py
    predictor_fn_ids: [pred1]
    capital_pct: 100
''',
            )

            with pytest.raises(ValueError, match='missing required field: capital_pool'):
                load_manifest(path, Decimal('10000'))

    def test_missing_strategies_raises(self) -> None:
        '''Missing strategies raises ValueError.'''

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'manifest.yaml'
            _write_yaml(path, 'capital_pool: 10000\n')

            with pytest.raises(ValueError, match='missing or empty strategies'):
                load_manifest(path, Decimal('20000'))

    def test_empty_strategies_raises(self) -> None:
        '''Empty strategies list raises ValueError.'''

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'manifest.yaml'
            _write_yaml(
                path,
                '''
capital_pool: 10000
strategies: []
''',
            )

            with pytest.raises(ValueError, match='missing or empty strategies'):
                load_manifest(path, Decimal('20000'))

    def test_strategy_missing_id_raises(self) -> None:
        '''Strategy missing id raises ValueError.'''

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'manifest.yaml'
            _write_yaml(
                path,
                '''
capital_pool: 10000
strategies:
  - file: test.py
    predictor_fn_ids: [pred1]
    capital_pct: 100
''',
            )

            with pytest.raises(ValueError, match='missing required field: id'):
                load_manifest(path, Decimal('20000'))

    def test_strategy_missing_file_raises(self) -> None:
        '''Strategy missing file raises ValueError.'''

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'manifest.yaml'
            _write_yaml(
                path,
                '''
capital_pool: 10000
strategies:
  - id: test
    predictor_fn_ids: [pred1]
    capital_pct: 100
''',
            )

            with pytest.raises(ValueError, match='missing required field: file'):
                load_manifest(path, Decimal('20000'))

    def test_strategy_missing_predictor_fn_ids_raises(self) -> None:
        '''Strategy missing predictor_fn_ids raises ValueError.'''

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'manifest.yaml'
            _write_yaml(
                path,
                '''
capital_pool: 10000
strategies:
  - id: test
    file: test.py
    capital_pct: 100
''',
            )

            with pytest.raises(ValueError, match='missing or empty predictor_fn_ids'):
                load_manifest(path, Decimal('20000'))

    def test_strategy_missing_capital_pct_raises(self) -> None:
        '''Strategy missing capital_pct raises ValueError.'''

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'manifest.yaml'
            _write_yaml(
                path,
                '''
capital_pool: 10000
strategies:
  - id: test
    file: test.py
    predictor_fn_ids: [pred1]
''',
            )

            with pytest.raises(ValueError, match='missing required field: capital_pct'):
                load_manifest(path, Decimal('20000'))

    def test_malformed_yaml_raises(self) -> None:
        '''Malformed YAML raises yaml.YAMLError.'''

        import yaml

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'manifest.yaml'
            _write_yaml(path, 'capital_pool: [unclosed')

            with pytest.raises(yaml.YAMLError):
                load_manifest(path, Decimal('20000'))

    def test_non_mapping_yaml_raises(self) -> None:
        '''Non-mapping YAML raises ValueError.'''

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'manifest.yaml'
            _write_yaml(path, '- item1\n- item2\n')

            with pytest.raises(ValueError, match='must be a YAML mapping'):
                load_manifest(path, Decimal('20000'))

    def test_duplicate_strategy_id_raises(self) -> None:
        '''Duplicate strategy_id raises ValueError.'''

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'manifest.yaml'
            _write_yaml(
                path,
                '''
capital_pool: 10000
strategies:
  - id: same
    file: test1.py
    predictor_fn_ids: [pred1]
    capital_pct: 50
  - id: same
    file: test2.py
    predictor_fn_ids: [pred2]
    capital_pct: 50
''',
            )

            with pytest.raises(ValueError, match='duplicate strategy_id'):
                load_manifest(path, Decimal('20000'))

    def test_capital_pct_sum_over_100_raises(self) -> None:
        '''capital_pct sum > 100 raises ValueError.'''

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'manifest.yaml'
            _write_yaml(
                path,
                '''
capital_pool: 10000
strategies:
  - id: a
    file: a.py
    predictor_fn_ids: [pred1]
    capital_pct: 60
  - id: b
    file: b.py
    predictor_fn_ids: [pred2]
    capital_pct: 50
''',
            )

            with pytest.raises(ValueError, match='exceeds 100'):
                load_manifest(path, Decimal('20000'))

    def test_strategy_file_not_found_raises(self) -> None:
        '''Missing strategy .py file raises ValueError with path.'''

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            path = tmp_path / 'manifest.yaml'
            _write_yaml(
                path,
                '''
capital_pool: 10000
strategies:
  - id: missing_file
    file: nonexistent/strategy.py
    predictor_fn_ids: [pred1]
    capital_pct: 100
''',
            )

            with pytest.raises(ValueError, match=r"file not found.*nonexistent/strategy\.py"):
                load_manifest(path, Decimal('20000'))

    def test_strategy_file_syntax_error_raises(self) -> None:
        '''Invalid Python syntax in strategy file raises ValueError.'''

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            path = tmp_path / 'manifest.yaml'

            strategy_file = tmp_path / 'bad_syntax.py'
            strategy_file.write_text('def broken(\n')

            _write_yaml(
                path,
                '''
capital_pool: 10000
strategies:
  - id: bad_syntax
    file: bad_syntax.py
    predictor_fn_ids: [pred1]
    capital_pct: 100
''',
            )

            with pytest.raises(ValueError, match=r'syntax error.*bad_syntax\.py'):
                load_manifest(path, Decimal('20000'))
