'''Action output from strategy callbacks.'''

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ActionType(Enum):
    '''Type of action a strategy can request.

    Args:
        ENTER: Request to enter a new position.
        EXIT: Request to exit an existing position.
        MODIFY: Request to modify an existing position or order.
        ABORT: Request to abort a pending action.
    '''

    ENTER = 'enter'
    EXIT = 'exit'
    MODIFY = 'modify'
    ABORT = 'abort'


@dataclass(frozen=True)
class Action:
    '''Action output from a strategy callback.

    Args:
        action_type: Type of action requested.
    '''

    action_type: ActionType

    def __post_init__(self) -> None:
        if not isinstance(self.action_type, ActionType):
            msg = 'action_type must be an ActionType'
            raise ValueError(msg)
