'''ProtectionRemediation dataclass for Praxis Connector inbound processing.

Represents a bracket whose protective OCO could not be restored, leaving the
position naked, that the Praxis execution engine has remediated locally
(flatten or entries-blocked) per the account's policy. Pushed to Nexus,
which sets the durable operational mode so the posture survives a restart.
'''

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

__all__ = ['ProtectionRemediation']


@dataclass(frozen=True)
class ProtectionRemediation:
    '''Immutable signal that a bracket's protection was lost and remediated.

    Args:
        account_id: Owning account.
        timestamp: When the exposure was remediated (must be UTC).
        protection_remediation_id: Stable unique identifier for the event.
        command_id: Entry command whose protective OCO could not be restored.
        protection_version: Amend attempt after which protection was declared
            lost (>= 1).
        reason: Short description of the remediation.
    '''

    account_id: str
    timestamp: datetime
    protection_remediation_id: str
    command_id: str
    protection_version: int
    reason: str

    def __post_init__(self) -> None:
        '''Validate invariants at construction time.'''

        for field_name in (
            'account_id', 'protection_remediation_id', 'command_id', 'reason',
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                msg = f'ProtectionRemediation.{field_name} must be a non-empty string'
                raise ValueError(msg)

        if (
            not isinstance(self.timestamp, datetime)
            or self.timestamp.tzinfo is not timezone.utc
        ):
            msg = 'ProtectionRemediation.timestamp must be a UTC datetime'
            raise ValueError(msg)

        if (
            isinstance(self.protection_version, bool)
            or not isinstance(self.protection_version, int)
            or self.protection_version < 1
        ):
            msg = 'ProtectionRemediation.protection_version must be an int >= 1'
            raise ValueError(msg)
