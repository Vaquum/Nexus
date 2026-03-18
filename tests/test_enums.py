'''Verify domain enum members and values.'''

from __future__ import annotations

from nexus.core.domain.enums import BreachLevel, OperationalMode, OrderSide


def test_operational_mode_members() -> None:
    '''Verify OperationalMode has exactly three members.'''

    assert set(OperationalMode) == {
        OperationalMode.ACTIVE,
        OperationalMode.REDUCE_ONLY,
        OperationalMode.HALTED,
    }


def test_operational_mode_values() -> None:
    '''Verify OperationalMode string values.'''

    assert OperationalMode.ACTIVE.value == 'ACTIVE'
    assert OperationalMode.REDUCE_ONLY.value == 'REDUCE_ONLY'
    assert OperationalMode.HALTED.value == 'HALTED'


def test_order_side_members() -> None:
    '''Verify OrderSide has exactly two members.'''

    assert set(OrderSide) == {OrderSide.BUY, OrderSide.SELL}


def test_breach_level_members() -> None:
    '''Verify BreachLevel has exactly four members.'''

    assert set(BreachLevel) == {
        BreachLevel.NONE,
        BreachLevel.WARN,
        BreachLevel.BREACH,
        BreachLevel.HALT,
    }
