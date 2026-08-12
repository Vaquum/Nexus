'''Tests for the Praxis->Nexus reconciliation inbound (WP-Praxis-0009 7.3/8.7).'''

from __future__ import annotations

import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from nexus.core.capital_controller.capital_controller import CapitalController
from nexus.core.domain.capital_state import CapitalState
from nexus.core.domain.enums import OperationalMode
from nexus.core.domain.instance_state import InstanceState
from nexus.core.domain.reconciliation_mismatch_response import (
    ReconciliationMismatchResponse,
)
from nexus.core.mode_controller import ModeController
from nexus.infrastructure.praxis_connector.fund_transaction import FundTransaction
from nexus.infrastructure.praxis_connector.outcome_processor import OutcomeProcessor
from nexus.infrastructure.praxis_connector.reconciliation_mismatch import (
    ReconciliationMismatch,
)
from nexus.infrastructure.state_store import StateStore
from nexus.reconciler.reconciliation_handler import ReconciliationHandler

_TS = datetime(2026, 1, 1, tzinfo=timezone.utc)
_ACCT = 'acc-1'


def _fund(fid: str = 'dep-1', direction: str = 'DEPOSIT') -> FundTransaction:
    return FundTransaction(
        account_id=_ACCT, timestamp=_TS, fund_transaction_id=fid,
        amount=Decimal('100'), direction=direction,
    )


def _mismatch() -> ReconciliationMismatch:
    return ReconciliationMismatch(
        account_id=_ACCT, timestamp=_TS, reconciliation_mismatch_id='r1',
        asset='USDT', expected=Decimal('0'), actual=Decimal('100'),
    )


class TestDomainTypes:

    def test_fund_transaction_rejects_bad_direction(self) -> None:
        with pytest.raises(ValueError, match='direction'):
            _fund(direction='TRANSFER')

    def test_fund_transaction_rejects_non_positive_amount(self) -> None:
        with pytest.raises(ValueError, match='positive finite'):
            FundTransaction(
                account_id=_ACCT, timestamp=_TS, fund_transaction_id='d',
                amount=Decimal('0'), direction='DEPOSIT',
            )

    def test_mismatch_delta(self) -> None:
        assert _mismatch().delta == Decimal('100')

    def test_mismatch_rejects_zero_delta(self) -> None:
        with pytest.raises(ValueError, match='expected != actual'):
            ReconciliationMismatch(
                account_id=_ACCT, timestamp=_TS, reconciliation_mismatch_id='r',
                asset='USDT', expected=Decimal('5'), actual=Decimal('5'),
            )


class TestProcessFundTransaction:

    def _make(self, tmp: str) -> tuple[OutcomeProcessor, CapitalState]:
        capital_state = CapitalState(capital_pool=Decimal('10000'))
        processor = OutcomeProcessor(
            CapitalController(capital_state),
            InstanceState(capital=capital_state),
            StateStore(Path(tmp)),
        )
        return processor, capital_state

    def test_records_and_leaves_capital_pool_unchanged(self) -> None:
        with TemporaryDirectory() as tmp:
            processor, capital_state = self._make(tmp)
            before_pool = capital_state.capital_pool
            before_available = capital_state.available

            assert processor.process_fund_transaction(_fund()) is True

            assert capital_state.capital_pool == before_pool
            assert capital_state.available == before_available

    def test_duplicate_is_dropped(self) -> None:
        with TemporaryDirectory() as tmp:
            processor, _ = self._make(tmp)

            assert processor.process_fund_transaction(_fund('dep-1')) is True
            assert processor.process_fund_transaction(_fund('dep-1')) is False


class TestReconciliationHandler:

    def _make(self) -> tuple[ModeController, InstanceState]:
        state = InstanceState(capital=CapitalState(capital_pool=Decimal('10000')))
        return ModeController(state, threading.Lock(), clock=lambda: _TS), state

    def test_halt_response_halts(self) -> None:
        controller, state = self._make()
        handler = ReconciliationHandler(controller, ReconciliationMismatchResponse.HALT)

        handler.process_reconciliation_mismatch(_mismatch())

        assert state.mode.mode is OperationalMode.HALTED

    def test_reduce_only_response_reduces(self) -> None:
        controller, state = self._make()
        handler = ReconciliationHandler(
            controller, ReconciliationMismatchResponse.REDUCE_ONLY,
        )

        handler.process_reconciliation_mismatch(_mismatch())

        assert state.mode.mode is OperationalMode.REDUCE_ONLY

    def test_alert_only_response_does_not_change_mode(self) -> None:
        controller, state = self._make()
        handler = ReconciliationHandler(
            controller, ReconciliationMismatchResponse.ALERT_ONLY,
        )

        handler.process_reconciliation_mismatch(_mismatch())

        assert state.mode.mode is OperationalMode.ACTIVE

    def test_redelivery_while_held_is_idempotent(self) -> None:
        controller, state = self._make()
        handler = ReconciliationHandler(controller, ReconciliationMismatchResponse.HALT)

        handler.process_reconciliation_mismatch(_mismatch())
        handler.process_reconciliation_mismatch(_mismatch())

        assert state.mode.mode is OperationalMode.HALTED
        assert state.mode_holds.reconciliation_hold.active
