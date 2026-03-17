'''Verify CapitalState creation, validation, and available property.'''

from __future__ import annotations

from decimal import Decimal

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
