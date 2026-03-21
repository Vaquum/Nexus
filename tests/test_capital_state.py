'''Verify CapitalState creation, validation, and available property.'''

from __future__ import annotations

from decimal import Decimal
from typing import Any, cast

import pytest

from nexus.core.domain.capital_state import CapitalState


def test_valid_creation() -> None:
    '''Verify a valid capital state is created with defaults.'''

    cs = CapitalState(capital_pool=Decimal('10000'))
    assert cs.capital_pool == Decimal('10000')
    assert cs.position_notional == Decimal(0)
    assert cs.working_order_notional == Decimal(0)
    assert cs.in_flight_order_notional == Decimal(0)
    assert cs.fee_reserve == Decimal(0)
    assert cs.reservation_notional == Decimal(0)
    assert cs.per_strategy_deployed == {}


def test_available_all_zero() -> None:
    '''Verify available equals capital_pool when nothing is deployed.'''

    cs = CapitalState(capital_pool=Decimal('10000'))
    assert cs.available == Decimal('10000')


def test_available_with_deductions() -> None:
    '''Verify available subtracts all commitments.'''

    cs = CapitalState(
        capital_pool=Decimal('10000'),
        position_notional=Decimal('3000'),
        working_order_notional=Decimal('1000'),
        in_flight_order_notional=Decimal('500'),
        fee_reserve=Decimal('100'),
        reservation_notional=Decimal('400'),
    )
    assert cs.available == Decimal('5000')


def test_available_negative_after_pool_reduction() -> None:
    '''Verify available can go negative when pool shrinks below deployment via hot-reload.'''

    cs = CapitalState(
        capital_pool=Decimal('1000'),
        position_notional=Decimal('1500'),
    )
    assert cs.available == Decimal('-500')


def test_zero_capital_pool_rejected() -> None:
    '''Verify zero capital_pool raises ValueError.'''

    with pytest.raises(ValueError, match='capital_pool'):
        CapitalState(capital_pool=Decimal(0))


def test_negative_capital_pool_rejected() -> None:
    '''Verify negative capital_pool raises ValueError.'''

    with pytest.raises(ValueError, match='capital_pool'):
        CapitalState(capital_pool=Decimal('-1000'))


def test_negative_position_notional_rejected() -> None:
    '''Verify negative position_notional raises ValueError.'''

    with pytest.raises(ValueError, match='position_notional'):
        CapitalState(
            capital_pool=Decimal('10000'),
            position_notional=Decimal('-1'),
        )


def test_negative_fee_reserve_rejected() -> None:
    '''Verify negative fee_reserve raises ValueError.'''

    with pytest.raises(ValueError, match='fee_reserve'):
        CapitalState(
            capital_pool=Decimal('10000'),
            fee_reserve=Decimal('-1'),
        )


def test_nan_capital_pool_rejected() -> None:
    '''Verify NaN capital_pool raises ValueError.'''

    with pytest.raises(ValueError, match='capital_pool'):
        CapitalState(capital_pool=Decimal('NaN'))


def test_infinity_capital_pool_rejected() -> None:
    '''Verify Infinity capital_pool raises ValueError.'''

    with pytest.raises(ValueError, match='capital_pool'):
        CapitalState(capital_pool=Decimal('Infinity'))


def test_nan_notional_rejected() -> None:
    '''Verify NaN position_notional raises ValueError.'''

    with pytest.raises(ValueError, match='position_notional'):
        CapitalState(
            capital_pool=Decimal('10000'),
            position_notional=Decimal('NaN'),
        )


def test_valid_per_strategy_deployed_creation() -> None:
    '''Verify valid per_strategy_deployed map is accepted.'''

    cs = CapitalState(
        capital_pool=Decimal('10000'),
        per_strategy_deployed={
            'momentum': Decimal('1200'),
            'mean_rev': Decimal('300.5'),
        },
    )

    assert cs.per_strategy_deployed['momentum'] == Decimal('1200')
    assert cs.per_strategy_deployed['mean_rev'] == Decimal('300.5')


def test_empty_strategy_key_rejected() -> None:
    '''Verify empty strategy key in per_strategy_deployed raises ValueError.'''

    with pytest.raises(ValueError, match='per_strategy_deployed keys'):
        CapitalState(
            capital_pool=Decimal('10000'),
            per_strategy_deployed={'': Decimal('1')},
        )


def test_non_string_strategy_key_rejected() -> None:
    '''Verify non-string key in per_strategy_deployed raises ValueError.'''

    with pytest.raises(ValueError, match='per_strategy_deployed keys'):
        CapitalState(
            capital_pool=Decimal('10000'),
            per_strategy_deployed=cast(
                dict[str, Decimal],
                {Decimal('1'): Decimal('1')},
            ),
        )


def test_whitespace_strategy_key_rejected() -> None:
    '''Verify whitespace-surrounded key in per_strategy_deployed raises ValueError.'''

    with pytest.raises(ValueError, match='must not contain leading or trailing'):
        CapitalState(
            capital_pool=Decimal('10000'),
            per_strategy_deployed={' strat_a ': Decimal('1')},
        )


def test_negative_strategy_deployed_rejected() -> None:
    '''Verify negative deployed value in per_strategy_deployed raises ValueError.'''

    with pytest.raises(ValueError, match='per_strategy_deployed values'):
        CapitalState(
            capital_pool=Decimal('10000'),
            per_strategy_deployed={'momentum': Decimal('-1')},
        )


def test_nan_strategy_deployed_rejected() -> None:
    '''Verify NaN deployed value in per_strategy_deployed raises ValueError.'''

    with pytest.raises(ValueError, match='per_strategy_deployed values'):
        CapitalState(
            capital_pool=Decimal('10000'),
            per_strategy_deployed={'momentum': Decimal('NaN')},
        )


def test_non_decimal_strategy_deployed_rejected() -> None:
    '''Verify non-Decimal deployed value in per_strategy_deployed raises ValueError.'''

    with pytest.raises(ValueError, match='per_strategy_deployed values'):
        CapitalState(
            capital_pool=Decimal('10000'),
            per_strategy_deployed=cast(dict[str, Decimal], {'momentum': cast(Any, 1)}),
        )
