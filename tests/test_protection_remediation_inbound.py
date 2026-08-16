'''Tests for the Praxis->Nexus protection-remediation inbound (WP-Praxis-0009).'''

from __future__ import annotations

import threading
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from nexus.core.domain.bracket_protection_failure_response import (
    BracketProtectionFailureResponse,
)
from nexus.core.domain.capital_state import CapitalState
from nexus.core.domain.enums import OperationalMode
from nexus.core.domain.instance_state import InstanceState
from nexus.core.mode_controller import ModeController
from nexus.infrastructure.praxis_connector.protection_remediation import (
    ProtectionRemediation,
)
from nexus.reconciler.protection_remediation_handler import (
    ProtectionRemediationHandler,
)

_TS = datetime(2026, 1, 1, tzinfo=timezone.utc)
_ACCT = 'acc-1'


def _remediation() -> ProtectionRemediation:
    return ProtectionRemediation(
        account_id=_ACCT, timestamp=_TS, protection_remediation_id='p1',
        command_id='cmd-1', protection_version=2, reason='replacement OCO rejected',
    )


class TestDomainType:

    def test_rejects_blank_command_id(self) -> None:
        with pytest.raises(ValueError, match='command_id'):
            ProtectionRemediation(
                account_id=_ACCT, timestamp=_TS, protection_remediation_id='p',
                command_id='  ', protection_version=1, reason='r',
            )

    def test_rejects_non_utc_timestamp(self) -> None:
        with pytest.raises(ValueError, match='UTC datetime'):
            ProtectionRemediation(
                account_id=_ACCT, timestamp=datetime(2026, 1, 1),
                protection_remediation_id='p', command_id='cmd-1',
                protection_version=1, reason='r',
            )

    def test_rejects_protection_version_below_one(self) -> None:
        with pytest.raises(ValueError, match='protection_version'):
            ProtectionRemediation(
                account_id=_ACCT, timestamp=_TS, protection_remediation_id='p',
                command_id='cmd-1', protection_version=0, reason='r',
            )

    def test_rejects_boolean_protection_version(self) -> None:
        with pytest.raises(ValueError, match='protection_version'):
            ProtectionRemediation(
                account_id=_ACCT, timestamp=_TS, protection_remediation_id='p',
                command_id='cmd-1', protection_version=True, reason='r',
            )


class TestProtectionRemediationHandler:

    def _make(self) -> tuple[ModeController, InstanceState]:
        state = InstanceState(capital=CapitalState(capital_pool=Decimal('10000')))
        return ModeController(state, threading.Lock(), clock=lambda: _TS), state

    def test_flatten_then_halt_response_halts(self) -> None:
        controller, state = self._make()
        handler = ProtectionRemediationHandler(
            controller, BracketProtectionFailureResponse.FLATTEN_THEN_HALT,
        )

        handler.process_protection_remediation(_remediation())

        assert state.mode.mode is OperationalMode.HALTED
        assert state.mode.trigger == 'protection'

    def test_reduce_only_response_reduces(self) -> None:
        controller, state = self._make()
        handler = ProtectionRemediationHandler(
            controller, BracketProtectionFailureResponse.REDUCE_ONLY,
        )

        handler.process_protection_remediation(_remediation())

        assert state.mode.mode is OperationalMode.REDUCE_ONLY
        assert state.mode.trigger == 'protection'

    def test_redelivery_while_held_is_idempotent(self) -> None:
        controller, state = self._make()
        handler = ProtectionRemediationHandler(
            controller, BracketProtectionFailureResponse.FLATTEN_THEN_HALT,
        )

        handler.process_protection_remediation(_remediation())
        handler.process_protection_remediation(_remediation())

        assert state.mode.mode is OperationalMode.HALTED
        assert state.mode_holds.protection_hold.active
