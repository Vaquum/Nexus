'''Abstract base class for trading strategies.

Defines the contract that all concrete strategies must implement,
including state persistence lifecycle hooks.
'''

from __future__ import annotations

from abc import ABC, abstractmethod


class Strategy(ABC):
    '''Abstract base class for trading strategies.

    Concrete strategies inherit from this class and implement
    the required lifecycle methods for state persistence.

    Args:
        strategy_id: Unique identifier for this strategy instance.
    '''

    _strategy_id: str

    def __init__(self, strategy_id: str) -> None:
        if not isinstance(strategy_id, str) or not strategy_id.strip():
            msg = 'strategy_id must be a non-empty string'
            raise ValueError(msg)

        self._strategy_id = strategy_id

    @property
    def strategy_id(self) -> str:
        '''Unique identifier for this strategy instance.'''

        return self._strategy_id

    @abstractmethod
    def on_save(self) -> bytes:
        '''Serialize strategy state for persistence.

        Called by Strategy Runner for periodic checkpoints and shutdown.
        The strategy author decides what state to persist.

        Returns:
            Serialized state as bytes.
        '''

    @abstractmethod
    def on_load(self, data: bytes) -> None:
        '''Restore strategy state from persisted bytes.

        Called by Strategy Runner on startup and crash recovery.
        The data parameter contains bytes previously returned by on_save().

        Args:
            data: Serialized state from a prior on_save() call.
        '''
