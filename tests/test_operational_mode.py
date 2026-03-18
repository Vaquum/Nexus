'''Verify ModeState and StrategyModeState creation and defaults.'''

from __future__ import annotations

from datetime import datetime

import pytest

from nexus.core.domain.enums import OperationalMode
from nexus.core.domain.operational_mode import ModeState, StrategyModeState


def test_mode_state_defaults() -> None:
    '''Verify ModeState defaults to ACTIVE with init trigger.'''

    ms = ModeState()
    assert ms.mode == OperationalMode.ACTIVE
    assert ms.trigger == 'init'
    assert ms.transitioned_at == datetime.min


def test_mode_state_custom() -> None:
    '''Verify ModeState accepts custom values.'''

    now = datetime(2026, 3, 17, 12, 0, 0)
    ms = ModeState(
        mode=OperationalMode.HALTED,
        trigger='reconciliation_mismatch',
        transitioned_at=now,
    )
    assert ms.mode == OperationalMode.HALTED
    assert ms.trigger == 'reconciliation_mismatch'
    assert ms.transitioned_at == now


def test_mode_state_mutable() -> None:
    '''Verify ModeState fields can be mutated for transitions.'''

    ms = ModeState()
    ms.mode = OperationalMode.REDUCE_ONLY
    ms.trigger = 'gateway_rejection'
    assert ms.mode == OperationalMode.REDUCE_ONLY


def test_strategy_mode_state_defaults() -> None:
    '''Verify StrategyModeState defaults to ACTIVE via ModeState.'''

    sms = StrategyModeState(strategy_id='momentum')
    assert sms.state.mode == OperationalMode.ACTIVE
    assert sms.state.trigger == 'init'


def test_strategy_mode_state_empty_id_rejected() -> None:
    '''Verify empty strategy_id raises ValueError.'''

    with pytest.raises(ValueError, match='strategy_id'):
        StrategyModeState(strategy_id='')
