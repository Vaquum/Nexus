'''Apply an account's configured response to a reported protection remediation.'''

from __future__ import annotations

import logging

from nexus.core.domain.bracket_protection_failure_response import (
    BracketProtectionFailureResponse,
)
from nexus.core.mode_controller import ModeController
from nexus.infrastructure.praxis_connector.protection_remediation import (
    ProtectionRemediation,
)

__all__ = ['ProtectionRemediationHandler']

_log = logging.getLogger(__name__)


class ProtectionRemediationHandler:
    '''Set the durable operational mode when a bracket's protection is lost.

    Praxis remediates the exposure locally (flatten or entries-blocked) and
    reports it; this handler makes the posture durable so it survives a
    restart. The account's response policy decides the mode: FLATTEN_THEN_HALT
    drives HALTED (Praxis has already flattened), REDUCE_ONLY forces
    REDUCE_ONLY. Every remediation is logged.

    Holds set here are sticky and only lifted by an operator
    (`ModeController.clear_protection_halt` / `clear_protection_reduce_only`):
    there is no "protection restored" event, so a subsequent clean cycle does
    NOT auto-resume the account. This is deliberately fail-safe. Re-applying
    the same response while a hold is already active is idempotent.

    Args:
        mode_controller: The account's ModeController.
        response: The account's configured bracket-protection failure policy.
    '''

    def __init__(
        self,
        mode_controller: ModeController,
        response: BracketProtectionFailureResponse,
    ) -> None:
        self._mode_controller = mode_controller
        self._response = response

    def process_protection_remediation(
        self,
        remediation: ProtectionRemediation,
    ) -> None:
        '''Apply the configured response to a reported protection remediation.

        FLATTEN_THEN_HALT drives the account to HALTED via a protection hold;
        REDUCE_ONLY forces REDUCE_ONLY via a reduce-only hold.

        Args:
            remediation: The reported bracket-protection loss.
        '''

        _log.warning(
            'bracket protection remediation reported',
            extra={
                'account_id': remediation.account_id,
                'protection_remediation_id': remediation.protection_remediation_id,
                'command_id': remediation.command_id,
                'protection_version': remediation.protection_version,
                'response': self._response.value,
            },
        )

        reason = (
            f'protection remediation: command_id={remediation.command_id} '
            f'protection_version={remediation.protection_version} '
            f'reason={remediation.reason}'
        )

        if self._response is BracketProtectionFailureResponse.FLATTEN_THEN_HALT:
            self._mode_controller.set_protection_halt(reason)
        elif self._response is BracketProtectionFailureResponse.REDUCE_ONLY:
            self._mode_controller.set_protection_reduce_only(reason)
