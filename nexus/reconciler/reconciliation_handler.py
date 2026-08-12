'''Apply an account's configured response to a reported reconciliation mismatch.'''

from __future__ import annotations

import logging

from nexus.core.domain.reconciliation_mismatch_response import (
    ReconciliationMismatchResponse,
)
from nexus.core.mode_controller import ModeController
from nexus.infrastructure.praxis_connector.reconciliation_mismatch import (
    ReconciliationMismatch,
)

__all__ = ['ReconciliationHandler']

_log = logging.getLogger(__name__)


class ReconciliationHandler:
    '''Apply the account's configured response to a reconciliation mismatch.

    A mismatch is never silent: every one is logged. Beyond that the account's
    response policy decides the operational-mode reaction — HALT, REDUCE_ONLY,
    or ALERT_ONLY (log only).

    Holds set by HALT / REDUCE_ONLY are sticky and only lifted by an operator
    (`ModeController.clear_reconciliation_halt` /
    `clear_reconciliation_reduce_only`): there is no "mismatch cleared" event,
    so a subsequent clean reconciliation cycle does NOT auto-resume the
    account. This is deliberately fail-safe — a balance divergence stays held
    until a human confirms it is resolved. Re-applying the same response while
    a hold is already active is idempotent.

    Args:
        mode_controller: The account's ModeController.
        response: The account's configured mismatch-response policy.
    '''

    def __init__(
        self,
        mode_controller: ModeController,
        response: ReconciliationMismatchResponse,
    ) -> None:
        self._mode_controller = mode_controller
        self._response = response

    def process_reconciliation_mismatch(
        self,
        mismatch: ReconciliationMismatch,
    ) -> None:
        '''Apply the configured response to a reported mismatch.

        HALT drives the account to HALTED via a reconciliation hold;
        REDUCE_ONLY forces REDUCE_ONLY via a reduce-only hold; ALERT_ONLY
        logs only.

        Args:
            mismatch: The reported per-asset balance divergence.
        '''

        _log.warning(
            'reconciliation mismatch reported',
            extra={
                'account_id': mismatch.account_id,
                'reconciliation_mismatch_id': mismatch.reconciliation_mismatch_id,
                'asset': mismatch.asset,
                'expected': str(mismatch.expected),
                'actual': str(mismatch.actual),
                'response': self._response.value,
            },
        )

        reason = (
            f'reconciliation mismatch: asset={mismatch.asset} '
            f'expected={mismatch.expected} actual={mismatch.actual}'
        )

        if self._response is ReconciliationMismatchResponse.HALT:
            self._mode_controller.set_reconciliation_halt(reason)
        elif self._response is ReconciliationMismatchResponse.REDUCE_ONLY:
            self._mode_controller.set_reconciliation_reduce_only(reason)
