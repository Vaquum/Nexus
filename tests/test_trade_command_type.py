'''Verify TradeCommandType enum members and values.'''

from __future__ import annotations

from nexus.infrastructure.praxis_connector.trade_command_type import TradeCommandType


def test_trade_command_type_members() -> None:
    '''Verify TradeCommandType has exactly three members.'''

    assert set(TradeCommandType) == {
        TradeCommandType.NEW_ORDER,
        TradeCommandType.AMEND_ORDER,
        TradeCommandType.CANCEL_ORDER,
    }


def test_trade_command_type_values() -> None:
    '''Verify TradeCommandType string values.'''

    assert TradeCommandType.NEW_ORDER.value == 'NEW_ORDER'
    assert TradeCommandType.AMEND_ORDER.value == 'AMEND_ORDER'
    assert TradeCommandType.CANCEL_ORDER.value == 'CANCEL_ORDER'
