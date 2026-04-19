'''Verify InstanceConfig creation and validation.'''

from __future__ import annotations

from decimal import Decimal
from typing import cast

import pytest

from nexus.core.stp_mode import STPMode
from nexus.instance_config import InstanceConfig


def test_valid_creation() -> None:
    '''Verify a valid config is created without error.'''

    cfg = InstanceConfig(
        account_id='acc_001',
        venue='binance_spot',
    )
    assert cfg.account_id == 'acc_001'
    assert cfg.venue == 'binance_spot'
    assert cfg.duplicate_window_ms == 1000
    assert cfg.max_order_rate is None
    assert cfg.book_staleness_max_seconds is None
    assert cfg.max_spread_bps is None
    assert cfg.price_deviation_max_bps is None
    assert cfg.reference_price_source is None
    assert cfg.stp_mode == STPMode.CANCEL_TAKER
    assert cfg.capital_pct == {}


def test_valid_creation_with_duplicate_window_ms() -> None:
    '''Verify duplicate_window_ms override is accepted when positive int.'''

    cfg = InstanceConfig(
        account_id='acc_001',
        venue='binance_spot',
        duplicate_window_ms=250,
    )
    assert cfg.duplicate_window_ms == 250


def test_valid_creation_with_max_order_rate() -> None:
    cfg = InstanceConfig(
        account_id='acc_001',
        venue='binance_spot',
        max_order_rate=7,
    )
    assert cfg.max_order_rate == 7


def test_valid_creation_with_price_validation_fields() -> None:
    cfg = InstanceConfig(
        account_id='acc_001',
        venue='binance_spot',
        book_staleness_max_seconds=3,
        max_spread_bps=Decimal('7.5'),
        price_deviation_max_bps=Decimal('12.0'),
        reference_price_source='origo_mid',
    )

    assert cfg.book_staleness_max_seconds == 3
    assert cfg.max_spread_bps == Decimal('7.5')
    assert cfg.price_deviation_max_bps == Decimal('12.0')
    assert cfg.reference_price_source == 'origo_mid'


def test_reference_price_source_is_normalized() -> None:
    cfg = InstanceConfig(
        account_id='acc_001',
        venue='binance_spot',
        price_deviation_max_bps=Decimal('5'),
        reference_price_source='  ORIGO_MID ',
    )

    assert cfg.reference_price_source == 'origo_mid'


def test_valid_creation_with_capital_pct() -> None:
    '''Verify capital_pct map is accepted when values are valid.'''

    cfg = InstanceConfig(
        account_id='acc_001',
        venue='binance_spot',
        capital_pct={'momentum': Decimal('60'), 'mean_rev': Decimal('40')},
    )

    assert cfg.capital_pct['momentum'] == Decimal('60')
    assert cfg.capital_pct['mean_rev'] == Decimal('40')


def test_capital_pct_mapping_is_immutable() -> None:
    '''Verify capital_pct cannot be mutated after initialization.'''

    cfg = InstanceConfig(
        account_id='acc_001',
        venue='binance_spot',
        capital_pct={'momentum': Decimal('60')},
    )

    mutable_view = cast(dict[str, Decimal], cfg.capital_pct)
    with pytest.raises(TypeError):
        mutable_view['mean_rev'] = Decimal('40')


def test_frozen() -> None:
    '''Verify config is immutable after creation.'''

    cfg = InstanceConfig(
        account_id='acc_001',
        venue='binance_spot',
    )
    with pytest.raises(AttributeError):
        cfg.account_id = 'acc_002'  # type: ignore[misc]


def test_empty_account_id_rejected() -> None:
    '''Verify empty account_id raises ValueError.'''

    with pytest.raises(ValueError, match='account_id'):
        InstanceConfig(
            account_id='',
            venue='binance_spot',
        )


def test_whitespace_account_id_rejected() -> None:
    '''Verify whitespace-only account_id raises ValueError.'''

    with pytest.raises(ValueError, match='account_id'):
        InstanceConfig(
            account_id='   ',
            venue='binance_spot',
        )


def test_empty_venue_rejected() -> None:
    '''Verify empty venue raises ValueError.'''

    with pytest.raises(ValueError, match='venue'):
        InstanceConfig(
            account_id='acc_001',
            venue='',
        )


def test_non_int_duplicate_window_rejected() -> None:
    '''Verify non-int duplicate_window_ms raises ValueError.'''

    with pytest.raises(ValueError, match='duplicate_window_ms'):
        InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            duplicate_window_ms=cast(int, cast(object, '1000')),
        )


def test_bool_duplicate_window_rejected() -> None:
    with pytest.raises(ValueError, match='duplicate_window_ms'):
        InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            duplicate_window_ms=cast(int, cast(object, True)),
        )


def test_non_positive_duplicate_window_rejected() -> None:
    '''Verify non-positive duplicate_window_ms raises ValueError.'''

    with pytest.raises(ValueError, match='duplicate_window_ms'):
        InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            duplicate_window_ms=0,
        )


def test_non_int_max_order_rate_rejected() -> None:
    with pytest.raises(ValueError, match='max_order_rate'):
        InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            max_order_rate=cast(int, cast(object, '5')),
        )


def test_bool_max_order_rate_rejected() -> None:
    with pytest.raises(ValueError, match='max_order_rate'):
        InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            max_order_rate=cast(int, cast(object, True)),
        )


def test_non_positive_max_order_rate_rejected() -> None:
    with pytest.raises(ValueError, match='max_order_rate'):
        InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            max_order_rate=0,
        )


def test_non_int_book_staleness_max_seconds_rejected() -> None:
    with pytest.raises(ValueError, match='book_staleness_max_seconds'):
        InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            book_staleness_max_seconds=cast(int, cast(object, '3')),
        )


def test_bool_book_staleness_max_seconds_rejected() -> None:
    with pytest.raises(ValueError, match='book_staleness_max_seconds'):
        InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            book_staleness_max_seconds=cast(int, cast(object, True)),
        )


def test_non_positive_book_staleness_max_seconds_rejected() -> None:
    with pytest.raises(ValueError, match='book_staleness_max_seconds'):
        InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            book_staleness_max_seconds=0,
        )


def test_non_decimal_max_spread_bps_rejected() -> None:
    with pytest.raises(ValueError, match='max_spread_bps'):
        InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            max_spread_bps=cast(Decimal, cast(object, 5)),
        )


def test_negative_max_spread_bps_rejected() -> None:
    with pytest.raises(ValueError, match='max_spread_bps'):
        InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            max_spread_bps=Decimal('-0.1'),
        )


def test_nan_max_spread_bps_rejected() -> None:
    with pytest.raises(ValueError, match='max_spread_bps'):
        InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            max_spread_bps=Decimal('NaN'),
        )


def test_non_decimal_price_deviation_max_bps_rejected() -> None:
    with pytest.raises(ValueError, match='price_deviation_max_bps'):
        InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            price_deviation_max_bps=cast(Decimal, cast(object, 5)),
            reference_price_source='origo_mid',
        )


def test_negative_price_deviation_max_bps_rejected() -> None:
    with pytest.raises(ValueError, match='price_deviation_max_bps'):
        InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            price_deviation_max_bps=Decimal('-0.1'),
            reference_price_source='origo_mid',
        )


def test_nan_price_deviation_max_bps_rejected() -> None:
    with pytest.raises(ValueError, match='price_deviation_max_bps'):
        InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            price_deviation_max_bps=Decimal('NaN'),
            reference_price_source='origo_mid',
        )


def test_price_deviation_without_reference_source_rejected() -> None:
    with pytest.raises(ValueError, match='reference_price_source'):
        InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            price_deviation_max_bps=Decimal('5'),
        )


def test_empty_reference_price_source_rejected() -> None:
    with pytest.raises(ValueError, match='reference_price_source'):
        InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            price_deviation_max_bps=Decimal('5'),
            reference_price_source='   ',
        )


def test_invalid_reference_price_source_rejected() -> None:
    with pytest.raises(ValueError, match='reference_price_source'):
        InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            price_deviation_max_bps=Decimal('5'),
            reference_price_source='origo_last',
        )


def test_valid_creation_with_stp_mode_cancel_maker() -> None:
    cfg = InstanceConfig(
        account_id='acc_001',
        venue='binance_spot',
        stp_mode=STPMode.CANCEL_MAKER,
    )
    assert cfg.stp_mode == STPMode.CANCEL_MAKER


def test_valid_creation_with_stp_mode_cancel_both() -> None:
    cfg = InstanceConfig(
        account_id='acc_001',
        venue='binance_spot',
        stp_mode=STPMode.CANCEL_BOTH,
    )
    assert cfg.stp_mode == STPMode.CANCEL_BOTH


def test_invalid_stp_mode_rejected() -> None:
    with pytest.raises(ValueError, match='stp_mode'):
        InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            stp_mode=cast(STPMode, cast(object, 'CANCEL_TAKER')),
        )


def test_empty_capital_pct_key_rejected() -> None:
    '''Verify empty capital_pct strategy key raises ValueError.'''

    with pytest.raises(ValueError, match='capital_pct keys'):
        InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            capital_pct={'': Decimal('10')},
        )


def test_non_string_capital_pct_key_rejected() -> None:
    '''Verify non-string capital_pct key raises ValueError.'''

    with pytest.raises(ValueError, match='capital_pct keys'):
        InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            capital_pct=cast(dict[str, Decimal], {1: Decimal('10')}),
        )


def test_duplicate_capital_pct_key_after_normalization_rejected() -> None:
    '''Verify duplicate keys after normalization raise ValueError.'''

    with pytest.raises(ValueError, match='duplicate keys after normalization'):
        InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            capital_pct={'momentum': Decimal('10'), ' momentum ': Decimal('20')},
        )


def test_non_mapping_capital_pct_rejected() -> None:
    '''Verify non-mapping capital_pct raises ValueError.'''

    with pytest.raises(ValueError, match='capital_pct must be a mapping'):
        InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            capital_pct=cast(dict[str, Decimal], cast(object, None)),
        )


def test_nan_capital_pct_rejected() -> None:
    '''Verify NaN capital_pct value raises ValueError.'''

    with pytest.raises(ValueError, match='capital_pct values'):
        InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            capital_pct={'momentum': Decimal('NaN')},
        )


def test_non_positive_capital_pct_rejected() -> None:
    '''Verify non-positive capital_pct value raises ValueError.'''

    with pytest.raises(ValueError, match='capital_pct values'):
        InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            capital_pct={'momentum': Decimal('0')},
        )


def test_capital_pct_above_100_rejected() -> None:
    '''Verify capital_pct value above 100 raises ValueError.'''

    with pytest.raises(ValueError, match='capital_pct values'):
        InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            capital_pct={'momentum': Decimal('120')},
        )


def test_capital_pct_total_above_100_rejected() -> None:
    '''Verify total capital_pct above 100 raises ValueError.'''

    with pytest.raises(ValueError, match='capital_pct total'):
        InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            capital_pct={'momentum': Decimal('70'), 'mean_rev': Decimal('40')},
        )


def test_shutdown_wait_timeout_bool_rejected() -> None:
    '''Verify bool shutdown_wait_timeout_seconds raises ValueError.'''

    with pytest.raises(ValueError, match='shutdown_wait_timeout_seconds must be a finite positive'):
        InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            shutdown_wait_timeout_seconds=True,
        )


def test_shutdown_abort_timeout_bool_rejected() -> None:
    '''Verify bool shutdown_abort_timeout_seconds raises ValueError.'''

    with pytest.raises(ValueError, match='shutdown_abort_timeout_seconds must be a finite positive'):
        InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            shutdown_abort_timeout_seconds=False,
        )


def test_shutdown_wait_timeout_zero_rejected() -> None:
    '''Verify zero shutdown_wait_timeout_seconds raises ValueError.'''

    with pytest.raises(ValueError, match='shutdown_wait_timeout_seconds must be a finite positive'):
        InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            shutdown_wait_timeout_seconds=0,
        )


def test_shutdown_abort_timeout_negative_rejected() -> None:
    '''Verify negative shutdown_abort_timeout_seconds raises ValueError.'''

    with pytest.raises(ValueError, match='shutdown_abort_timeout_seconds must be a finite positive'):
        InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            shutdown_abort_timeout_seconds=-5.0,
        )
