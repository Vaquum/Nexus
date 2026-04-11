'''Tests for StrategySpec dataclass.'''

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from nexus.infrastructure.manifest import SensorSpec, StrategySpec


def _pfn(tmp_path: Path) -> SensorSpec:
    '''Create a valid SensorSpec for testing.'''

    exp_dir = tmp_path / 'experiment'
    exp_dir.mkdir(exist_ok=True)
    return SensorSpec(
        experiment_dir=exp_dir,
        permutation_ids=(1,),
        interval_seconds=60,
    )


class TestStrategySpec:

    def test_valid_strategy_spec(self, tmp_path: Path) -> None:
        '''Valid StrategySpec creates successfully.'''

        pfn = _pfn(tmp_path)
        spec = StrategySpec(
            strategy_id='momentum_v1',
            file='strategies/momentum.py',
            sensors=(pfn,),
            capital_pct=Decimal('25'),
        )

        assert spec.strategy_id == 'momentum_v1'
        assert spec.file == 'strategies/momentum.py'
        assert spec.sensors == (pfn,)
        assert spec.capital_pct == Decimal('25')

    def test_strategy_spec_is_frozen(self, tmp_path: Path) -> None:
        '''StrategySpec is immutable.'''

        spec = StrategySpec(
            strategy_id='test',
            file='test.py',
            sensors=(_pfn(tmp_path),),
            capital_pct=Decimal('10'),
        )

        with pytest.raises(AttributeError):
            spec.strategy_id = 'changed'  # type: ignore[misc]

    def test_empty_strategy_id_raises(self, tmp_path: Path) -> None:
        '''Empty strategy_id raises ValueError.'''

        with pytest.raises(ValueError, match='strategy_id must be a non-empty string'):
            StrategySpec(
                strategy_id='',
                file='test.py',
                sensors=(_pfn(tmp_path),),
                capital_pct=Decimal('10'),
            )

    def test_whitespace_strategy_id_raises(self, tmp_path: Path) -> None:
        '''Whitespace-only strategy_id raises ValueError.'''

        with pytest.raises(ValueError, match='strategy_id must be a non-empty string'):
            StrategySpec(
                strategy_id='   ',
                file='test.py',
                sensors=(_pfn(tmp_path),),
                capital_pct=Decimal('10'),
            )

    def test_padded_whitespace_strategy_id_raises(self, tmp_path: Path) -> None:
        '''Strategy_id with surrounding whitespace raises ValueError.'''

        with pytest.raises(
            ValueError, match='strategy_id must be a non-empty string without surrounding whitespace'
        ):
            StrategySpec(
                strategy_id=' s1 ',
                file='test.py',
                sensors=(_pfn(tmp_path),),
                capital_pct=Decimal('10'),
            )

    def test_leading_whitespace_strategy_id_raises(self, tmp_path: Path) -> None:
        '''Strategy_id with leading whitespace raises ValueError.'''

        with pytest.raises(
            ValueError, match='strategy_id must be a non-empty string without surrounding whitespace'
        ):
            StrategySpec(
                strategy_id=' s1',
                file='test.py',
                sensors=(_pfn(tmp_path),),
                capital_pct=Decimal('10'),
            )

    def test_trailing_whitespace_strategy_id_raises(self, tmp_path: Path) -> None:
        '''Strategy_id with trailing whitespace raises ValueError.'''

        with pytest.raises(
            ValueError, match='strategy_id must be a non-empty string without surrounding whitespace'
        ):
            StrategySpec(
                strategy_id='s1 ',
                file='test.py',
                sensors=(_pfn(tmp_path),),
                capital_pct=Decimal('10'),
            )

    def test_empty_file_raises(self, tmp_path: Path) -> None:
        '''Empty file raises ValueError.'''

        with pytest.raises(ValueError, match='file must be a non-empty string'):
            StrategySpec(
                strategy_id='test',
                file='',
                sensors=(_pfn(tmp_path),),
                capital_pct=Decimal('10'),
            )

    def test_whitespace_file_raises(self, tmp_path: Path) -> None:
        '''Whitespace-only file raises ValueError.'''

        with pytest.raises(ValueError, match='file must be a non-empty string'):
            StrategySpec(
                strategy_id='test',
                file='  ',
                sensors=(_pfn(tmp_path),),
                capital_pct=Decimal('10'),
            )

    def test_padded_whitespace_file_raises(self, tmp_path: Path) -> None:
        '''File with surrounding whitespace raises ValueError.'''

        with pytest.raises(ValueError, match='file must be a non-empty string without surrounding whitespace'):
            StrategySpec(
                strategy_id='test',
                file=' strategies/foo.py ',
                sensors=(_pfn(tmp_path),),
                capital_pct=Decimal('10'),
            )

    def test_empty_sensors_raises(self) -> None:
        '''Empty sensors raises ValueError.'''

        with pytest.raises(ValueError, match='sensors must be a non-empty tuple'):
            StrategySpec(
                strategy_id='test',
                file='test.py',
                sensors=(),
                capital_pct=Decimal('10'),
            )

    def test_sensors_not_tuple_raises(self, tmp_path: Path) -> None:
        '''Non-tuple sensors raises ValueError.'''

        with pytest.raises(ValueError, match='sensors must be a non-empty tuple'):
            StrategySpec(
                strategy_id='test',
                file='test.py',
                sensors=[_pfn(tmp_path)],  # type: ignore[arg-type]
                capital_pct=Decimal('10'),
            )

    def test_sensors_with_non_spec_raises(self, tmp_path: Path) -> None:
        '''sensors containing non-SensorSpec raises ValueError.'''

        with pytest.raises(ValueError, match='sensors must contain SensorSpec'):
            StrategySpec(
                strategy_id='test',
                file='test.py',
                sensors=(_pfn(tmp_path), 'not a spec'),  # type: ignore[arg-type]
                capital_pct=Decimal('10'),
            )

    def test_non_decimal_capital_pct_raises(self, tmp_path: Path) -> None:
        '''Non-Decimal capital_pct raises ValueError.'''

        with pytest.raises(ValueError, match='capital_pct must be a finite Decimal'):
            StrategySpec(
                strategy_id='test',
                file='test.py',
                sensors=(_pfn(tmp_path),),
                capital_pct=25,  # type: ignore[arg-type]
            )

    def test_infinite_capital_pct_raises(self, tmp_path: Path) -> None:
        '''Infinite capital_pct raises ValueError.'''

        with pytest.raises(ValueError, match='capital_pct must be a finite Decimal'):
            StrategySpec(
                strategy_id='test',
                file='test.py',
                sensors=(_pfn(tmp_path),),
                capital_pct=Decimal('inf'),
            )

    def test_nan_capital_pct_raises(self, tmp_path: Path) -> None:
        '''NaN capital_pct raises ValueError.'''

        with pytest.raises(ValueError, match='capital_pct must be a finite Decimal'):
            StrategySpec(
                strategy_id='test',
                file='test.py',
                sensors=(_pfn(tmp_path),),
                capital_pct=Decimal('nan'),
            )

    def test_zero_capital_pct_raises(self, tmp_path: Path) -> None:
        '''Zero capital_pct raises ValueError.'''

        with pytest.raises(ValueError, match='capital_pct must be in'):
            StrategySpec(
                strategy_id='test',
                file='test.py',
                sensors=(_pfn(tmp_path),),
                capital_pct=Decimal('0'),
            )

    def test_negative_capital_pct_raises(self, tmp_path: Path) -> None:
        '''Negative capital_pct raises ValueError.'''

        with pytest.raises(ValueError, match='capital_pct must be in'):
            StrategySpec(
                strategy_id='test',
                file='test.py',
                sensors=(_pfn(tmp_path),),
                capital_pct=Decimal('-5'),
            )

    def test_capital_pct_over_100_raises(self, tmp_path: Path) -> None:
        '''capital_pct > 100 raises ValueError.'''

        with pytest.raises(ValueError, match='capital_pct must be in'):
            StrategySpec(
                strategy_id='test',
                file='test.py',
                sensors=(_pfn(tmp_path),),
                capital_pct=Decimal('101'),
            )

    def test_capital_pct_exactly_100_allowed(self, tmp_path: Path) -> None:
        '''capital_pct of exactly 100 is allowed.'''

        spec = StrategySpec(
            strategy_id='test',
            file='test.py',
            sensors=(_pfn(tmp_path),),
            capital_pct=Decimal('100'),
        )

        assert spec.capital_pct == Decimal('100')

    def test_capital_pct_fractional_allowed(self, tmp_path: Path) -> None:
        '''Fractional capital_pct is allowed.'''

        spec = StrategySpec(
            strategy_id='test',
            file='test.py',
            sensors=(_pfn(tmp_path),),
            capital_pct=Decimal('12.5'),
        )

        assert spec.capital_pct == Decimal('12.5')

    def test_multiple_sensors_allowed(self, tmp_path: Path) -> None:
        '''Multiple sensors are allowed.'''

        exp1 = tmp_path / 'exp1'
        exp1.mkdir()
        exp2 = tmp_path / 'exp2'
        exp2.mkdir()

        pfn1 = SensorSpec(experiment_dir=exp1, permutation_ids=(1,), interval_seconds=60)
        pfn2 = SensorSpec(experiment_dir=exp2, permutation_ids=(2, 3), interval_seconds=300)

        spec = StrategySpec(
            strategy_id='test',
            file='test.py',
            sensors=(pfn1, pfn2),
            capital_pct=Decimal('50'),
        )

        assert len(spec.sensors) == 2
