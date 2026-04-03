'''Startup and shutdown error types.'''

from __future__ import annotations

__all__ = ['ShutdownError', 'StartupError']


class StartupError(Exception):
    '''Raised when startup sequence fails.

    Args:
        step: Name of the step that failed.
        reason: Description of the failure.
    '''

    def __init__(self, step: str, reason: str) -> None:
        self.step = step
        self.reason = reason
        super().__init__(f'Startup failed at {step}: {reason}')


class ShutdownError(Exception):
    '''Raised when shutdown sequence fails critically.

    Args:
        step: Name of the step that failed.
        reason: Description of the failure.
    '''

    def __init__(self, step: str, reason: str) -> None:
        self.step = step
        self.reason = reason
        super().__init__(f'Shutdown failed at {step}: {reason}')
