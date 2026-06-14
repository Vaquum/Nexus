'''Tests for StrategySpec dataclass.'''

from __future__ import annotations

from decimal import Decimal

import pytest

from nexus.infrastructure.manifest import SignalSpec, StrategySpec


def _signal() -> SignalSpec:
    '''Create a valid SignalSpec for testing.'''

    return SignalSpec(
        series='time_15m',
        interval_seconds=900,
    )


class TestStrategySpec:

    def test_valid_strategy_spec(self) -> None:
        '''Valid StrategySpec creates successfully.'''

        signal = _signal()
        spec = StrategySpec(
            strategy_id='momentum_v1',
            file='strategies/momentum.py',
            signal=signal,
            capital_pct=Decimal('25'),
        )

        assert spec.strategy_id == 'momentum_v1'
        assert spec.file == 'strategies/momentum.py'
        assert spec.signal == signal
        assert spec.capital_pct == Decimal('25')

    def test_strategy_spec_is_frozen(self) -> None:
        '''StrategySpec is immutable.'''

        spec = StrategySpec(
            strategy_id='test',
            file='test.py',
            signal=_signal(),
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
                signal=_signal(),
                capital_pct=Decimal('10'),
            )

    def test_whitespace_strategy_id_raises(self) -> None:
        '''Whitespace-only strategy_id raises ValueError.'''

        with pytest.raises(ValueError, match='strategy_id must be a non-empty string'):
            StrategySpec(
                strategy_id='   ',
                file='test.py',
                signal=_signal(),
                capital_pct=Decimal('10'),
            )

    def test_padded_whitespace_strategy_id_raises(self) -> None:
        '''Strategy_id with surrounding whitespace raises ValueError.'''

        with pytest.raises(
            ValueError, match='strategy_id must be a non-empty string without surrounding whitespace'
        ):
            StrategySpec(
                strategy_id=' s1 ',
                file='test.py',
                signal=_signal(),
                capital_pct=Decimal('10'),
            )

    def test_leading_whitespace_strategy_id_raises(self) -> None:
        '''Strategy_id with leading whitespace raises ValueError.'''

        with pytest.raises(
            ValueError, match='strategy_id must be a non-empty string without surrounding whitespace'
        ):
            StrategySpec(
                strategy_id=' s1',
                file='test.py',
                signal=_signal(),
                capital_pct=Decimal('10'),
            )

    def test_trailing_whitespace_strategy_id_raises(self) -> None:
        '''Strategy_id with trailing whitespace raises ValueError.'''

        with pytest.raises(
            ValueError, match='strategy_id must be a non-empty string without surrounding whitespace'
        ):
            StrategySpec(
                strategy_id='s1 ',
                file='test.py',
                signal=_signal(),
                capital_pct=Decimal('10'),
            )

    def test_empty_file_raises(self) -> None:
        '''Empty file raises ValueError.'''

        with pytest.raises(ValueError, match='file must be a non-empty string'):
            StrategySpec(
                strategy_id='test',
                file='',
                signal=_signal(),
                capital_pct=Decimal('10'),
            )

    def test_whitespace_file_raises(self) -> None:
        '''Whitespace-only file raises ValueError.'''

        with pytest.raises(ValueError, match='file must be a non-empty string'):
            StrategySpec(
                strategy_id='test',
                file='  ',
                signal=_signal(),
                capital_pct=Decimal('10'),
            )

    def test_padded_whitespace_file_raises(self) -> None:
        '''File with surrounding whitespace raises ValueError.'''

        with pytest.raises(ValueError, match='file must be a non-empty string without surrounding whitespace'):
            StrategySpec(
                strategy_id='test',
                file=' strategies/foo.py ',
                signal=_signal(),
                capital_pct=Decimal('10'),
            )

    def test_non_signal_spec_raises(self) -> None:
        '''Non-SignalSpec signal raises ValueError.'''

        with pytest.raises(ValueError, match='signal must be a SignalSpec instance'):
            StrategySpec(
                strategy_id='test',
                file='test.py',
                signal='not a signal',  # type: ignore[arg-type]
                capital_pct=Decimal('10'),
            )

    def test_non_decimal_capital_pct_raises(self) -> None:
        '''Non-Decimal capital_pct raises ValueError.'''

        with pytest.raises(ValueError, match='capital_pct must be a finite Decimal'):
            StrategySpec(
                strategy_id='test',
                file='test.py',
                signal=_signal(),
                capital_pct=25,  # type: ignore[arg-type]
            )

    def test_infinite_capital_pct_raises(self) -> None:
        '''Infinite capital_pct raises ValueError.'''

        with pytest.raises(ValueError, match='capital_pct must be a finite Decimal'):
            StrategySpec(
                strategy_id='test',
                file='test.py',
                signal=_signal(),
                capital_pct=Decimal('inf'),
            )

    def test_nan_capital_pct_raises(self) -> None:
        '''NaN capital_pct raises ValueError.'''

        with pytest.raises(ValueError, match='capital_pct must be a finite Decimal'):
            StrategySpec(
                strategy_id='test',
                file='test.py',
                signal=_signal(),
                capital_pct=Decimal('nan'),
            )

    def test_zero_capital_pct_raises(self) -> None:
        '''Zero capital_pct raises ValueError.'''

        with pytest.raises(ValueError, match='capital_pct must be in'):
            StrategySpec(
                strategy_id='test',
                file='test.py',
                signal=_signal(),
                capital_pct=Decimal('0'),
            )

    def test_negative_capital_pct_raises(self) -> None:
        '''Negative capital_pct raises ValueError.'''

        with pytest.raises(ValueError, match='capital_pct must be in'):
            StrategySpec(
                strategy_id='test',
                file='test.py',
                signal=_signal(),
                capital_pct=Decimal('-5'),
            )

    def test_capital_pct_over_100_raises(self) -> None:
        '''capital_pct > 100 raises ValueError.'''

        with pytest.raises(ValueError, match='capital_pct must be in'):
            StrategySpec(
                strategy_id='test',
                file='test.py',
                signal=_signal(),
                capital_pct=Decimal('101'),
            )

    def test_capital_pct_exactly_100_allowed(self) -> None:
        '''capital_pct of exactly 100 is allowed.'''

        spec = StrategySpec(
            strategy_id='test',
            file='test.py',
            signal=_signal(),
            capital_pct=Decimal('100'),
        )

        assert spec.capital_pct == Decimal('100')

    def test_capital_pct_fractional_allowed(self) -> None:
        '''Fractional capital_pct is allowed.'''

        spec = StrategySpec(
            strategy_id='test',
            file='test.py',
            signal=_signal(),
            capital_pct=Decimal('12.5'),
        )

        assert spec.capital_pct == Decimal('12.5')
