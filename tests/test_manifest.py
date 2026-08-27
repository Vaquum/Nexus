'''Tests for Manifest dataclass and load_manifest function.'''

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from nexus.core.domain.bracket_protection_failure_response import (
    BracketProtectionFailureResponse,
)
from nexus.core.domain.reconciliation_mismatch_response import (
    ReconciliationMismatchResponse,
)
from nexus.infrastructure.manifest import (
    Manifest,
    SignalSpec,
    StrategySpec,
    load_manifest,
)


def _signal() -> SignalSpec:
    return SignalSpec(
        series='time_15m',
        interval_seconds=900,
    )


def _make_spec(
    strategy_id: str = 'test',
    capital_pct: Decimal = Decimal('50'),
) -> StrategySpec:
    return StrategySpec(
        strategy_id=strategy_id,
        file='test.py',
        signal=_signal(),
        capital_pct=capital_pct,
    )


def _write_yaml(path: Path, content: str) -> None:
    path.write_text(content, encoding='utf-8')


def _write_strategy_file(base: Path, rel_path: str, content: str = '') -> None:
    file_path = base / rel_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content or '# Strategy file\n', encoding='utf-8')


_YAML_SIGNAL = (
    '    signal:\n'
    '      series: time_15m\n'
    '      interval_seconds: 900\n'
)


class TestManifest:

    def test_valid_manifest(self) -> None:
        '''Valid Manifest creates successfully.'''

        spec1 = _make_spec('strategy_a', Decimal('60'))
        spec2 = _make_spec('strategy_b', Decimal('40'))

        manifest = Manifest(
            account_id='test_acct',
            allocated_capital=Decimal('100000'),
            capital_pool=Decimal('10000'),
            strategies=(spec1, spec2),
        )

        assert manifest.capital_pool == Decimal('10000')
        assert manifest.strategies == (spec1, spec2)

    def test_manifest_is_frozen(self) -> None:
        '''Manifest is immutable.'''

        manifest = Manifest(
            account_id='test_acct',
            allocated_capital=Decimal('100000'),
            capital_pool=Decimal('10000'),
            strategies=(_make_spec(),),
        )

        with pytest.raises(AttributeError):
            manifest.capital_pool = Decimal('5000')  # type: ignore[misc]

    def test_non_decimal_capital_pool_raises(self) -> None:
        '''Non-Decimal capital_pool raises ValueError.'''

        with pytest.raises(ValueError, match='capital_pool must be a finite Decimal'):
            Manifest(
                account_id='test_acct',
                allocated_capital=Decimal('100000'),
                capital_pool=10000,  # type: ignore[arg-type]
                strategies=(_make_spec(),),
            )

    def test_infinite_capital_pool_raises(self) -> None:
        '''Infinite capital_pool raises ValueError.'''

        with pytest.raises(ValueError, match='capital_pool must be a finite Decimal'):
            Manifest(
                account_id='test_acct',
                allocated_capital=Decimal('100000'),
                capital_pool=Decimal('inf'),
                strategies=(_make_spec(),),
            )

    def test_nan_capital_pool_raises(self) -> None:
        '''NaN capital_pool raises ValueError.'''

        with pytest.raises(ValueError, match='capital_pool must be a finite Decimal'):
            Manifest(
                account_id='test_acct',
                allocated_capital=Decimal('100000'),
                capital_pool=Decimal('nan'),
                strategies=(_make_spec(),),
            )

    def test_zero_capital_pool_raises(self) -> None:
        '''Zero capital_pool raises ValueError.'''

        with pytest.raises(ValueError, match='capital_pool must be positive'):
            Manifest(
                account_id='test_acct',
                allocated_capital=Decimal('100000'),
                capital_pool=Decimal('0'),
                strategies=(_make_spec(),),
            )

    def test_negative_capital_pool_raises(self) -> None:
        '''Negative capital_pool raises ValueError.'''

        with pytest.raises(ValueError, match='capital_pool must be positive'):
            Manifest(
                account_id='test_acct',
                allocated_capital=Decimal('100000'),
                capital_pool=Decimal('-1000'),
                strategies=(_make_spec(),),
            )

    def test_empty_strategies_raises(self) -> None:
        '''Empty strategies raises ValueError.'''

        with pytest.raises(ValueError, match='strategies must be a non-empty tuple'):
            Manifest(
                account_id='test_acct',
                allocated_capital=Decimal('100000'),
                capital_pool=Decimal('10000'),
                strategies=(),
            )

    def test_strategies_not_tuple_raises(self) -> None:
        '''Non-tuple strategies raises ValueError.'''

        with pytest.raises(ValueError, match='strategies must be a non-empty tuple'):
            Manifest(
                account_id='test_acct',
                allocated_capital=Decimal('100000'),
                capital_pool=Decimal('10000'),
                strategies=[_make_spec()],  # type: ignore[arg-type]
            )

    def test_strategies_with_non_spec_raises(self) -> None:
        '''Strategies containing non-StrategySpec raises ValueError.'''

        with pytest.raises(ValueError, match='strategies must contain StrategySpec'):
            Manifest(
                account_id='test_acct',
                allocated_capital=Decimal('100000'),
                capital_pool=Decimal('10000'),
                strategies=(_make_spec(), 'not a spec'),  # type: ignore[arg-type]
            )

    def test_duplicate_strategy_id_raises(self) -> None:
        '''Duplicate strategy_id raises ValueError.'''

        with pytest.raises(ValueError, match='duplicate strategy_id'):
            Manifest(
                account_id='test_acct',
                allocated_capital=Decimal('100000'),
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
                account_id='test_acct',
                allocated_capital=Decimal('100000'),
                capital_pool=Decimal('10000'),
                strategies=(
                    _make_spec('a', Decimal('60')),
                    _make_spec('b', Decimal('50')),
                ),
            )

    def test_capital_pct_sum_exactly_100_allowed(self) -> None:
        '''capital_pct sum of exactly 100 is allowed.'''

        manifest = Manifest(
            account_id='test_acct',
            allocated_capital=Decimal('100000'),
            capital_pool=Decimal('10000'),
            strategies=(
                _make_spec('a', Decimal('60')),
                _make_spec('b', Decimal('40')),
            ),
        )

        assert manifest.capital_pool == Decimal('10000')

    def test_account_id_is_normalized(self) -> None:
        '''account_id is stripped of surrounding whitespace at construction.'''

        manifest = Manifest(
            account_id='  test_acct  ',
            allocated_capital=Decimal('100000'),
            capital_pool=Decimal('10000'),
            strategies=(_make_spec(),),
        )

        assert manifest.account_id == 'test_acct'

    def test_capital_pct_sum_under_100_allowed(self) -> None:
        '''capital_pct sum under 100 is allowed.'''

        manifest = Manifest(
            account_id='test_acct',
            allocated_capital=Decimal('100000'),
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
            account_id='test_acct',
            allocated_capital=Decimal('100000'),
            capital_pool=Decimal('10000'),
            strategies=(_make_spec('only_one', Decimal('100')),),
        )

        assert len(manifest.strategies) == 1


class TestLoadManifest:

    def test_valid_manifest_loads(self, tmp_path: Path) -> None:
        '''Valid YAML manifest loads successfully.'''

        path = tmp_path / 'manifest.yaml'

        _write_strategy_file(tmp_path, 'strategies/momentum.py')
        _write_strategy_file(tmp_path, 'strategies/mean_rev.py')

        _write_yaml(
            path,
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - id: momentum\n'
            '    file: strategies/momentum.py\n'
            '    signal:\n'
            '      series: time_15m\n'
            '      interval_seconds: 900\n'
            '    capital_pct: 60\n'
            '  - id: mean_rev\n'
            '    file: strategies/mean_rev.py\n'
            '    signal:\n'
            '      series: time_15m\n'
            '      interval_seconds: 900\n'
            '    capital_pct: 40\n',
        )

        manifest = load_manifest(path)

        assert manifest.capital_pool == Decimal('10000')
        assert len(manifest.strategies) == 2
        assert manifest.strategies[0].strategy_id == 'momentum'
        assert manifest.strategies[1].strategy_id == 'mean_rev'
        assert manifest.strategies[0].signal.series == 'time_15m'
        assert manifest.strategies[0].signal.interval_seconds == 900

    def _write_minimal(self, tmp_path: Path, extra: str = '') -> Path:
        path = tmp_path / 'manifest.yaml'
        _write_strategy_file(tmp_path, 'strategies/momentum.py')
        _write_yaml(
            path,
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            + extra
            + 'strategies:\n'
            '  - id: momentum\n'
            '    file: strategies/momentum.py\n'
            '    signal:\n'
            '      series: time_15m\n'
            '      interval_seconds: 900\n'
            '    capital_pct: 100\n',
        )
        return path

    def test_max_price_deviation_bps_defaults_to_none(self, tmp_path: Path) -> None:
        manifest = load_manifest(self._write_minimal(tmp_path))

        assert manifest.strategies[0].max_price_deviation_bps is None

    def test_max_price_deviation_bps_parses_per_strategy(
        self, tmp_path: Path,
    ) -> None:
        path = tmp_path / 'manifest.yaml'
        _write_strategy_file(tmp_path, 'strategies/momentum.py')
        _write_yaml(
            path,
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - id: momentum\n'
            '    file: strategies/momentum.py\n'
            '    signal:\n'
            '      series: time_15m\n'
            '      interval_seconds: 900\n'
            '    capital_pct: 100\n'
            '    max_price_deviation_bps: 25\n',
        )

        manifest = load_manifest(path)

        assert manifest.strategies[0].max_price_deviation_bps == Decimal('25')

    def test_reconciliation_mismatch_response_defaults_to_halt(
        self, tmp_path: Path,
    ) -> None:
        manifest = load_manifest(self._write_minimal(tmp_path))

        assert (
            manifest.reconciliation_mismatch_response
            is ReconciliationMismatchResponse.HALT
        )

    def test_reconciliation_mismatch_response_parses_explicit(
        self, tmp_path: Path,
    ) -> None:
        manifest = load_manifest(
            self._write_minimal(
                tmp_path, extra='reconciliation_mismatch_response: REDUCE_ONLY\n',
            ),
        )

        assert (
            manifest.reconciliation_mismatch_response
            is ReconciliationMismatchResponse.REDUCE_ONLY
        )

    def test_reconciliation_mismatch_response_invalid_raises(
        self, tmp_path: Path,
    ) -> None:
        path = self._write_minimal(
            tmp_path, extra='reconciliation_mismatch_response: NOPE\n',
        )

        with pytest.raises(ValueError, match='reconciliation_mismatch_response'):
            load_manifest(path)

    def test_bracket_protection_failure_response_defaults_to_flatten_then_halt(
        self, tmp_path: Path,
    ) -> None:
        manifest = load_manifest(self._write_minimal(tmp_path))

        assert (
            manifest.bracket_protection_failure_response
            is BracketProtectionFailureResponse.FLATTEN_THEN_HALT
        )

    def test_bracket_protection_failure_response_parses_explicit(
        self, tmp_path: Path,
    ) -> None:
        manifest = load_manifest(
            self._write_minimal(
                tmp_path,
                extra='bracket_protection_failure_response: REDUCE_ONLY\n',
            ),
        )

        assert (
            manifest.bracket_protection_failure_response
            is BracketProtectionFailureResponse.REDUCE_ONLY
        )

    def test_bracket_protection_failure_response_invalid_raises(
        self, tmp_path: Path,
    ) -> None:
        path = self._write_minimal(
            tmp_path,
            extra='bracket_protection_failure_response: NOPE\n',
        )

        with pytest.raises(
            ValueError,
            match=(
                'bracket_protection_failure_response must be one of '
                'FLATTEN_THEN_HALT, REDUCE_ONLY'
            ),
        ):
            load_manifest(path)

    def test_file_not_found_raises(self) -> None:
        '''Missing manifest file raises FileNotFoundError.'''

        with pytest.raises(FileNotFoundError, match='Manifest file not found'):
            load_manifest(Path('/nonexistent/manifest.yaml'))

    def test_capital_pool_exceeds_allocated_raises(self, tmp_path: Path) -> None:
        '''capital_pool > allocated_capital raises ValueError.'''

        path = tmp_path / 'manifest.yaml'

        _write_yaml(
            path,
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 15000\n'
            'strategies:\n'
            '  - id: test\n'
            '    file: test.py\n'
            '    signal:\n'
            '      series: time_15m\n'
            '      interval_seconds: 900\n'
            '    capital_pct: 100\n',
        )

        with pytest.raises(ValueError, match='exceeds allocated_capital'):
            load_manifest(path)

    def test_missing_allocated_capital_raises(self, tmp_path: Path) -> None:
        '''Missing allocated_capital raises ValueError.'''

        path = tmp_path / 'manifest.yaml'
        _write_yaml(
            path,
            'account_id: test_acct\n'
            'capital_pool: 5000\n'
            'strategies: []\n',
        )

        with pytest.raises(ValueError, match='missing required field: allocated_capital'):
            load_manifest(path)

    def test_invalid_allocated_capital_raises(self, tmp_path: Path) -> None:
        '''Non-numeric allocated_capital raises ValueError.'''

        path = tmp_path / 'manifest.yaml'
        _write_yaml(
            path,
            'account_id: test_acct\n'
            'allocated_capital: not_a_number\n'
            'capital_pool: 5000\n'
            'strategies: []\n',
        )

        with pytest.raises(ValueError, match='allocated_capital is not a valid number'):
            load_manifest(path)

    def test_non_positive_allocated_capital_raises(self, tmp_path: Path) -> None:
        '''Zero or negative allocated_capital raises ValueError.'''

        path = tmp_path / 'manifest.yaml'
        _write_yaml(
            path,
            'account_id: test_acct\n'
            'allocated_capital: 0\n'
            'capital_pool: 5000\n'
            'strategies: []\n',
        )

        with pytest.raises(ValueError, match='allocated_capital must be a finite positive'):
            load_manifest(path)

    def test_missing_account_id_raises(self, tmp_path: Path) -> None:
        '''Missing account_id raises ValueError.'''

        path = tmp_path / 'manifest.yaml'
        _write_yaml(path, 'capital_pool: 10000\nstrategies: []\n')

        with pytest.raises(ValueError, match='missing or invalid required field: account_id'):
            load_manifest(path)

    def test_blank_account_id_raises(self, tmp_path: Path) -> None:
        '''Blank account_id raises ValueError.'''

        path = tmp_path / 'manifest.yaml'
        _write_yaml(path, 'account_id: \'   \'\ncapital_pool: 10000\nstrategies: []\n')

        with pytest.raises(ValueError, match='missing or invalid required field: account_id'):
            load_manifest(path)

    def test_missing_capital_pool_raises(self, tmp_path: Path) -> None:
        '''Missing capital_pool raises ValueError.'''

        path = tmp_path / 'manifest.yaml'

        _write_yaml(
            path,
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'strategies:\n'
            '  - id: test\n'
            '    file: test.py\n'
            '    signal:\n'
            '      series: time_15m\n'
            '      interval_seconds: 900\n'
            '    capital_pct: 100\n',
        )

        with pytest.raises(ValueError, match='missing required field: capital_pool'):
            load_manifest(path)

    def test_missing_strategies_raises(self, tmp_path: Path) -> None:
        '''Missing strategies raises ValueError.'''

        path = tmp_path / 'manifest.yaml'
        _write_yaml(
            path,
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n',
        )

        with pytest.raises(ValueError, match='missing or empty strategies'):
            load_manifest(path)

    def test_empty_strategies_raises(self, tmp_path: Path) -> None:
        '''Empty strategies list raises ValueError.'''

        path = tmp_path / 'manifest.yaml'
        _write_yaml(
            path,
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies: []\n',
        )

        with pytest.raises(ValueError, match='missing or empty strategies'):
            load_manifest(path)

    def test_strategy_missing_id_raises(self, tmp_path: Path) -> None:
        '''Strategy missing id raises ValueError.'''

        path = tmp_path / 'manifest.yaml'

        _write_yaml(
            path,
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - file: test.py\n'
            '    signal:\n'
            '      series: time_15m\n'
            '      interval_seconds: 900\n'
            '    capital_pct: 100\n',
        )

        with pytest.raises(ValueError, match='missing required field: id'):
            load_manifest(path)

    def test_strategy_missing_file_raises(self, tmp_path: Path) -> None:
        '''Strategy missing file raises ValueError.'''

        path = tmp_path / 'manifest.yaml'

        _write_yaml(
            path,
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - id: test\n'
            '    signal:\n'
            '      series: time_15m\n'
            '      interval_seconds: 900\n'
            '    capital_pct: 100\n',
        )

        with pytest.raises(ValueError, match='missing required field: file'):
            load_manifest(path)

    def test_strategy_missing_signal_raises(self, tmp_path: Path) -> None:
        '''Strategy missing signal raises ValueError.'''

        path = tmp_path / 'manifest.yaml'

        _write_yaml(
            path,
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - id: test\n'
            '    file: test.py\n'
            '    capital_pct: 100\n',
        )

        with pytest.raises(ValueError, match='missing or invalid signal mapping'):
            load_manifest(path)

    def test_strategy_signal_missing_series_raises(self, tmp_path: Path) -> None:
        '''Strategy signal missing series raises ValueError.'''

        path = tmp_path / 'manifest.yaml'

        _write_yaml(
            path,
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - id: test\n'
            '    file: test.py\n'
            '    signal:\n'
            '      interval_seconds: 900\n'
            '    capital_pct: 100\n',
        )

        with pytest.raises(ValueError, match='signal missing required field: series'):
            load_manifest(path)

    def test_strategy_signal_missing_interval_raises(self, tmp_path: Path) -> None:
        '''Strategy signal missing interval_seconds raises ValueError.'''

        path = tmp_path / 'manifest.yaml'

        _write_yaml(
            path,
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - id: test\n'
            '    file: test.py\n'
            '    signal:\n'
            '      series: time_15m\n'
            '    capital_pct: 100\n',
        )

        with pytest.raises(ValueError, match='signal missing required field: interval_seconds'):
            load_manifest(path)

    def test_strategy_missing_capital_pct_raises(self, tmp_path: Path) -> None:
        '''Strategy missing capital_pct raises ValueError.'''

        path = tmp_path / 'manifest.yaml'

        _write_yaml(
            path,
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - id: test\n'
            '    file: test.py\n'
            '    signal:\n'
            '      series: time_15m\n'
            '      interval_seconds: 900\n',
        )

        with pytest.raises(ValueError, match='missing required field: capital_pct'):
            load_manifest(path)

    def test_malformed_yaml_raises(self, tmp_path: Path) -> None:
        '''Malformed YAML raises yaml.YAMLError.'''

        path = tmp_path / 'manifest.yaml'
        _write_yaml(path, 'capital_pool: [unclosed')

        with pytest.raises(yaml.YAMLError):
            load_manifest(path)

    def test_non_mapping_yaml_raises(self, tmp_path: Path) -> None:
        '''Non-mapping YAML raises ValueError.'''

        path = tmp_path / 'manifest.yaml'
        _write_yaml(path, '- item1\n- item2\n')

        with pytest.raises(ValueError, match='must be a YAML mapping'):
            load_manifest(path)

    def test_duplicate_strategy_id_raises(self, tmp_path: Path) -> None:
        '''Duplicate strategy_id raises ValueError.'''

        path = tmp_path / 'manifest.yaml'

        _write_yaml(
            path,
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - id: same\n'
            '    file: test1.py\n'
            '    signal:\n'
            '      series: time_15m\n'
            '      interval_seconds: 900\n'
            '    capital_pct: 50\n'
            '  - id: same\n'
            '    file: test2.py\n'
            '    signal:\n'
            '      series: time_15m\n'
            '      interval_seconds: 900\n'
            '    capital_pct: 50\n',
        )

        with pytest.raises(ValueError, match='duplicate strategy_id'):
            load_manifest(path)

    def test_capital_pct_sum_over_100_raises(self, tmp_path: Path) -> None:
        '''capital_pct sum > 100 raises ValueError.'''

        path = tmp_path / 'manifest.yaml'
        _write_strategy_file(tmp_path, 'a.py')
        _write_strategy_file(tmp_path, 'b.py')

        _write_yaml(
            path,
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - id: a\n'
            '    file: a.py\n'
            '    signal:\n'
            '      series: time_15m\n'
            '      interval_seconds: 900\n'
            '    capital_pct: 60\n'
            '  - id: b\n'
            '    file: b.py\n'
            '    signal:\n'
            '      series: time_15m\n'
            '      interval_seconds: 900\n'
            '    capital_pct: 50\n',
        )

        with pytest.raises(ValueError, match='exceeds 100'):
            load_manifest(path)

    def test_strategy_file_not_found_raises(self, tmp_path: Path) -> None:
        '''Missing strategy .py file raises ValueError with path.'''

        path = tmp_path / 'manifest.yaml'

        _write_yaml(
            path,
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - id: missing_file\n'
            '    file: nonexistent/strategy.py\n'
            '    signal:\n'
            '      series: time_15m\n'
            '      interval_seconds: 900\n'
            '    capital_pct: 100\n',
        )

        with pytest.raises(ValueError, match=r"file not found.*nonexistent/strategy\.py"):
            load_manifest(path)

    def test_strategy_file_syntax_error_raises(self, tmp_path: Path) -> None:
        '''Invalid Python syntax in strategy file raises ValueError.'''

        path = tmp_path / 'manifest.yaml'

        strategy_file = tmp_path / 'bad_syntax.py'
        strategy_file.write_text('def broken(\n')

        _write_yaml(
            path,
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - id: bad_syntax\n'
            '    file: bad_syntax.py\n'
            '    signal:\n'
            '      series: time_15m\n'
            '      interval_seconds: 900\n'
            '    capital_pct: 100\n',
        )

        with pytest.raises(ValueError, match=r'syntax error.*bad_syntax\.py'):
            load_manifest(path)

    def test_invalid_capital_pool_decimal_raises(self, tmp_path: Path) -> None:
        '''Non-numeric capital_pool raises ValueError.'''

        path = tmp_path / 'manifest.yaml'
        _write_strategy_file(tmp_path, 'test.py')

        _write_yaml(
            path,
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: not_a_number\n'
            'strategies:\n'
            '  - id: test\n'
            '    file: test.py\n'
            '    signal:\n'
            '      series: time_15m\n'
            '      interval_seconds: 900\n'
            '    capital_pct: 100\n',
        )

        with pytest.raises(ValueError, match='capital_pool is not a valid number'):
            load_manifest(path)

    def test_invalid_capital_pct_decimal_raises(self, tmp_path: Path) -> None:
        '''Non-numeric capital_pct raises ValueError.'''

        path = tmp_path / 'manifest.yaml'
        _write_strategy_file(tmp_path, 'test.py')

        _write_yaml(
            path,
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - id: test\n'
            '    file: test.py\n'
            '    signal:\n'
            '      series: time_15m\n'
            '      interval_seconds: 900\n'
            '    capital_pct: invalid\n',
        )

        with pytest.raises(ValueError, match='capital_pct is not a valid number'):
            load_manifest(path)

    def test_absolute_file_path_raises(self, tmp_path: Path) -> None:
        '''Absolute strategy file path raises ValueError.'''

        path = tmp_path / 'manifest.yaml'

        _write_yaml(
            path,
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - id: absolute\n'
            '    file: /etc/passwd\n'
            '    signal:\n'
            '      series: time_15m\n'
            '      interval_seconds: 900\n'
            '    capital_pct: 100\n',
        )

        with pytest.raises(ValueError, match='file must be relative'):
            load_manifest(path)

    def test_path_traversal_raises(self, tmp_path: Path) -> None:
        '''Strategy file path escaping base raises ValueError.'''

        path = tmp_path / 'manifest.yaml'

        _write_yaml(
            path,
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - id: escape\n'
            '    file: ../../../etc/passwd\n'
            '    signal:\n'
            '      series: time_15m\n'
            '      interval_seconds: 900\n'
            '    capital_pct: 100\n',
        )

        with pytest.raises(ValueError, match='escapes base path'):
            load_manifest(path)

    def test_directory_instead_of_file_raises(self, tmp_path: Path) -> None:
        '''Directory path instead of file raises ValueError.'''

        path = tmp_path / 'manifest.yaml'
        (tmp_path / 'a_directory').mkdir()

        _write_yaml(
            path,
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - id: dir_not_file\n'
            '    file: a_directory\n'
            '    signal:\n'
            '      series: time_15m\n'
            '      interval_seconds: 900\n'
            '    capital_pct: 100\n',
        )

        with pytest.raises(ValueError, match='file not found'):
            load_manifest(path)


class TestLoadManifestTimers:

    def test_manifest_with_timers_loads(self, tmp_path: Path) -> None:
        '''Manifest with valid timers parses correctly.'''

        path = tmp_path / 'manifest.yaml'
        _write_strategy_file(tmp_path, 'strat.py')

        _write_yaml(
            path,
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - id: strat1\n'
            '    file: strat.py\n'
            '    signal:\n'
            '      series: time_15m\n'
            '      interval_seconds: 900\n'
            '    capital_pct: 100\n'
            '    timers:\n'
            '      - id: trailing_stop\n'
            '        interval_seconds: 30\n'
            '      - id: position_review\n'
            '        interval_seconds: 300\n',
        )

        manifest = load_manifest(path)

        assert len(manifest.strategies[0].timers) == 2
        assert manifest.strategies[0].timers[0].timer_id == 'trailing_stop'
        assert manifest.strategies[0].timers[1].interval_seconds == 300

    def test_manifest_without_timers_loads(self, tmp_path: Path) -> None:
        '''Manifest without timers field defaults to empty tuple.'''

        path = tmp_path / 'manifest.yaml'
        _write_strategy_file(tmp_path, 'strat.py')

        _write_yaml(
            path,
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - id: strat1\n'
            '    file: strat.py\n'
            '    signal:\n'
            '      series: time_15m\n'
            '      interval_seconds: 900\n'
            '    capital_pct: 100\n',
        )

        manifest = load_manifest(path)

        assert manifest.strategies[0].timers == ()

    def test_timer_missing_id_raises(self, tmp_path: Path) -> None:
        '''Timer missing id raises ValueError.'''

        path = tmp_path / 'manifest.yaml'
        _write_strategy_file(tmp_path, 'strat.py')

        _write_yaml(
            path,
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - id: strat1\n'
            '    file: strat.py\n'
            '    signal:\n'
            '      series: time_15m\n'
            '      interval_seconds: 900\n'
            '    capital_pct: 100\n'
            '    timers:\n'
            '      - interval_seconds: 30\n',
        )

        with pytest.raises(ValueError, match='timer missing required field: id'):
            load_manifest(path)

    def test_timer_bool_interval_raises(self, tmp_path: Path) -> None:
        '''Timer with bool interval_seconds raises ValueError.'''

        path = tmp_path / 'manifest.yaml'
        _write_strategy_file(tmp_path, 'strat.py')

        _write_yaml(
            path,
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - id: strat1\n'
            '    file: strat.py\n'
            '    signal:\n'
            '      series: time_15m\n'
            '      interval_seconds: 900\n'
            '    capital_pct: 100\n'
            '    timers:\n'
            '      - id: check\n'
            '        interval_seconds: true\n',
        )

        with pytest.raises(ValueError, match='interval_seconds must be an int'):
            load_manifest(path)

    def test_duplicate_timer_id_raises(self, tmp_path: Path) -> None:
        '''Duplicate timer_id raises ValueError.'''

        path = tmp_path / 'manifest.yaml'
        _write_strategy_file(tmp_path, 'strat.py')

        _write_yaml(
            path,
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - id: strat1\n'
            '    file: strat.py\n'
            '    signal:\n'
            '      series: time_15m\n'
            '      interval_seconds: 900\n'
            '    capital_pct: 100\n'
            '    timers:\n'
            '      - id: check\n'
            '        interval_seconds: 30\n'
            '      - id: check\n'
            '        interval_seconds: 60\n',
        )

        with pytest.raises(ValueError, match='duplicate timer_id'):
            load_manifest(path)


def _write_min_manifest(tmp_path: Path, risk_block: str = '') -> Path:
    path = tmp_path / 'manifest.yaml'
    _write_strategy_file(tmp_path, 'strategies/momentum.py')
    _write_yaml(
        path,
        'account_id: test_acct\n'
        'allocated_capital: 10000\n'
        'capital_pool: 10000\n'
        + risk_block +
        'strategies:\n'
        '  - id: momentum\n'
        '    file: strategies/momentum.py\n'
        '    signal:\n'
        '      series: time_15m\n'
        '      interval_seconds: 900\n'
        '    capital_pct: 100\n',
    )

    return path


def test_risk_controls_parsed(tmp_path: Path) -> None:
    path = _write_min_manifest(
        tmp_path,
        'risk_controls:\n'
        '  max_daily_loss: 250\n'
        '  max_drawdown_pct: 0.05\n',
    )

    manifest = load_manifest(path)

    assert manifest.risk_controls.max_daily_loss == Decimal('250')
    assert manifest.risk_controls.max_drawdown_pct == Decimal('0.05')
    assert manifest.risk_controls.max_drawdown is None


def test_risk_controls_absent_defaults_to_empty(tmp_path: Path) -> None:
    manifest = load_manifest(_write_min_manifest(tmp_path))

    assert manifest.risk_controls.max_daily_loss is None
    assert manifest.risk_controls.max_drawdown_pct is None
    assert manifest.risk_controls.max_drawdown is None


def test_negative_risk_control_rejected(tmp_path: Path) -> None:
    path = _write_min_manifest(
        tmp_path,
        'risk_controls:\n'
        '  max_daily_loss: -1\n',
    )

    with pytest.raises(ValueError, match='positive'):
        load_manifest(path)


def test_risk_controls_non_mapping_rejected(tmp_path: Path) -> None:
    path = _write_min_manifest(
        tmp_path,
        'risk_controls: []\n',
    )

    with pytest.raises(ValueError, match='must be a mapping'):
        load_manifest(path)
