'''Verify InstanceConfig creation and validation.'''

from __future__ import annotations

from decimal import Decimal

import pytest

from nexus.instance_config import InstanceConfig


def test_valid_creation() -> None:
    '''Verify a valid config is created without error.'''

    cfg = InstanceConfig(
        account_id='acc_001',
        venue='binance_spot',
        allocated_capital=Decimal('10000'),
    )
    assert cfg.account_id == 'acc_001'
    assert cfg.venue == 'binance_spot'
    assert cfg.allocated_capital == Decimal('10000')
    assert cfg.capital_pct == {}


def test_valid_creation_with_capital_pct() -> None:
    '''Verify capital_pct map is accepted when values are valid.'''

    cfg = InstanceConfig(
        account_id='acc_001',
        venue='binance_spot',
        allocated_capital=Decimal('10000'),
        capital_pct={'momentum': Decimal('60'), 'mean_rev': Decimal('40')},
    )

    assert cfg.capital_pct['momentum'] == Decimal('60')
    assert cfg.capital_pct['mean_rev'] == Decimal('40')


def test_frozen() -> None:
    '''Verify config is immutable after creation.'''

    cfg = InstanceConfig(
        account_id='acc_001',
        venue='binance_spot',
        allocated_capital=Decimal('10000'),
    )
    with pytest.raises(AttributeError):
        cfg.account_id = 'acc_002'  # type: ignore[misc]


def test_empty_account_id_rejected() -> None:
    '''Verify empty account_id raises ValueError.'''

    with pytest.raises(ValueError, match='account_id'):
        InstanceConfig(
            account_id='',
            venue='binance_spot',
            allocated_capital=Decimal('10000'),
        )


def test_whitespace_account_id_rejected() -> None:
    '''Verify whitespace-only account_id raises ValueError.'''

    with pytest.raises(ValueError, match='account_id'):
        InstanceConfig(
            account_id='   ',
            venue='binance_spot',
            allocated_capital=Decimal('10000'),
        )


def test_empty_venue_rejected() -> None:
    '''Verify empty venue raises ValueError.'''

    with pytest.raises(ValueError, match='venue'):
        InstanceConfig(
            account_id='acc_001',
            venue='',
            allocated_capital=Decimal('10000'),
        )


def test_zero_capital_rejected() -> None:
    '''Verify zero allocated_capital raises ValueError.'''

    with pytest.raises(ValueError, match='allocated_capital'):
        InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            allocated_capital=Decimal('0'),
        )


def test_negative_capital_rejected() -> None:
    '''Verify negative allocated_capital raises ValueError.'''

    with pytest.raises(ValueError, match='allocated_capital'):
        InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            allocated_capital=Decimal('-100'),
        )


def test_nan_capital_rejected() -> None:
    '''Verify NaN allocated_capital raises ValueError.'''

    with pytest.raises(ValueError, match='allocated_capital'):
        InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            allocated_capital=Decimal('NaN'),
        )


def test_infinity_capital_rejected() -> None:
    '''Verify Infinity allocated_capital raises ValueError.'''

    with pytest.raises(ValueError, match='allocated_capital'):
        InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            allocated_capital=Decimal('Infinity'),
        )


def test_empty_capital_pct_key_rejected() -> None:
    '''Verify empty capital_pct strategy key raises ValueError.'''

    with pytest.raises(ValueError, match='capital_pct keys'):
        InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            allocated_capital=Decimal('10000'),
            capital_pct={'': Decimal('10')},
        )


def test_nan_capital_pct_rejected() -> None:
    '''Verify NaN capital_pct value raises ValueError.'''

    with pytest.raises(ValueError, match='capital_pct values'):
        InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            allocated_capital=Decimal('10000'),
            capital_pct={'momentum': Decimal('NaN')},
        )


def test_non_positive_capital_pct_rejected() -> None:
    '''Verify non-positive capital_pct value raises ValueError.'''

    with pytest.raises(ValueError, match='capital_pct values'):
        InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            allocated_capital=Decimal('10000'),
            capital_pct={'momentum': Decimal('0')},
        )


def test_capital_pct_above_100_rejected() -> None:
    '''Verify capital_pct value above 100 raises ValueError.'''

    with pytest.raises(ValueError, match='capital_pct values'):
        InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            allocated_capital=Decimal('10000'),
            capital_pct={'momentum': Decimal('120')},
        )


def test_capital_pct_total_above_100_rejected() -> None:
    '''Verify total capital_pct above 100 raises ValueError.'''

    with pytest.raises(ValueError, match='capital_pct total'):
        InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            allocated_capital=Decimal('10000'),
            capital_pct={'momentum': Decimal('70'), 'mean_rev': Decimal('40')},
        )
