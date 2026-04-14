'''Concrete outbound connector bridging sync Nexus to async Praxis.

Uses asyncio.run_coroutine_threadsafe to call Praxis async methods
from a sync Nexus thread.
'''

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from collections.abc import Awaitable, Callable

from nexus.infrastructure.praxis_connector.trade_command import TradeCommand

__all__ = ['PraxisOutbound']

_log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30.0


class PraxisOutbound:
    '''Sync-to-async bridge for Praxis Trading operations.

    Args:
        submit_fn: Async callable matching Praxis Trading.submit_command signature.
        register_fn: Sync callable matching Praxis Trading.register_account.
        unregister_fn: Async callable matching Praxis Trading.unregister_account.
        pull_positions_fn: Sync callable matching Praxis Trading.pull_positions.
        loop: Asyncio event loop running in the Praxis thread.
        timeout: Seconds to wait for async calls to complete.
    '''

    def __init__(
        self,
        submit_fn: Callable[..., Awaitable[str]],
        loop: asyncio.AbstractEventLoop,
        register_fn: Callable[[str], None] | None = None,
        unregister_fn: Callable[[str], Awaitable[None]] | None = None,
        pull_positions_fn: Callable[[str], dict[tuple[str, str], object]] | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._submit_fn = submit_fn
        self._loop = loop
        self._register_fn = register_fn
        self._unregister_fn = unregister_fn
        self._pull_positions_fn = pull_positions_fn
        self._timeout = timeout

    def send_command(self, command: TradeCommand) -> str:
        '''Submit TradeCommand to Praxis via async bridge.

        Args:
            command: Validated TradeCommand from Nexus.

        Returns:
            Command ID assigned by Praxis.

        Raises:
            TimeoutError: If Praxis does not respond within timeout.
            Exception: Propagates the original exception raised by submit_fn.
        '''

        # NOTE: execution_mode and execution_params require Action fields (TD-023).
        # Placeholder values used until full Action → TradeCommand translation is built.
        future = asyncio.run_coroutine_threadsafe(
            self._submit_fn(
                trade_id=command.trade_id or command.command_id,
                account_id=command.account_id,
                symbol=command.symbol,
                side=command.side,
                qty=command.size,
                order_type=command.command_type,
                execution_mode=None,
                execution_params=None,
                timeout=max(1, round(self._timeout)),
                reference_price=None,
                maker_preference=None,
                stp_mode=command.stp_mode,
                created_at=command.created_at,
            ),
            self._loop,
        )

        try:
            command_id = future.result(timeout=self._timeout)
        except (TimeoutError, concurrent.futures.TimeoutError):
            future.cancel()
            _log.error(
                'submit_command timed out: command_id=%s',
                command.command_id,
            )
            raise

        _log.info(
            'command submitted',
            extra={
                'nexus_command_id': command.command_id,
                'praxis_command_id': command_id,
            },
        )

        return command_id

    def register_account(self, account_id: str) -> None:
        '''Register account with Praxis Trading.

        Args:
            account_id: Account identifier to register.

        Raises:
            RuntimeError: If register_fn is not configured.
            ValueError: If Praxis rejects the registration.
        '''

        if self._register_fn is None:
            msg = 'register_fn not configured'
            raise RuntimeError(msg)

        self._register_fn(account_id)
        _log.info('account registered', extra={'account_id': account_id})

    def deregister_account(self, account_id: str) -> None:
        '''Deregister account from Praxis Trading via async bridge.

        Args:
            account_id: Account identifier to deregister.

        Raises:
            RuntimeError: If unregister_fn is not configured.
            TimeoutError: If Praxis does not respond within timeout.
        '''

        if self._unregister_fn is None:
            msg = 'unregister_fn not configured'
            raise RuntimeError(msg)

        future = asyncio.run_coroutine_threadsafe(
            self._unregister_fn(account_id),
            self._loop,
        )

        try:
            future.result(timeout=self._timeout)
        except (TimeoutError, concurrent.futures.TimeoutError):
            future.cancel()
            _log.error('deregister timed out: account_id=%s', account_id)
            raise

        _log.info('account deregistered', extra={'account_id': account_id})

    def pull_positions(self, account_id: str) -> dict[tuple[str, str], object]:
        '''Pull positions snapshot from Praxis Trading.

        Args:
            account_id: Account identifier to query.

        Returns:
            Positions keyed by (account_id, trade_id) tuples.

        Raises:
            RuntimeError: If pull_positions_fn is not configured.
        '''

        if self._pull_positions_fn is None:
            msg = 'pull_positions_fn not configured'
            raise RuntimeError(msg)

        return self._pull_positions_fn(account_id)
