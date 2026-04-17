'''Concrete outbound connector bridging sync Nexus to async Praxis.

Uses asyncio.run_coroutine_threadsafe to call Praxis async methods
from a sync Nexus thread.
'''

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Any

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
        submit_abort_fn: Async callable wrapping Praxis Trading.submit_abort.
        loop: Asyncio event loop running in the Praxis thread.
        timeout: Seconds to wait for async calls to complete.
    '''

    def __init__(
        self,
        submit_fn: Callable[..., Coroutine[Any, Any, str]],
        loop: asyncio.AbstractEventLoop,
        register_fn: Callable[[str], None] | None = None,
        unregister_fn: Callable[[str], Coroutine[Any, Any, None]] | None = None,
        pull_positions_fn: Callable[[str], dict[tuple[str, str], Any]] | None = None,
        submit_abort_fn: Callable[..., Coroutine[Any, Any, None]] | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._submit_fn = submit_fn
        self._loop = loop
        self._register_fn = register_fn
        self._unregister_fn = unregister_fn
        self._pull_positions_fn = pull_positions_fn
        self._submit_abort_fn = submit_abort_fn
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

        future: concurrent.futures.Future[str] = asyncio.run_coroutine_threadsafe(
            self._submit_fn(
                trade_id=command.trade_id or command.command_id,
                account_id=command.account_id,
                symbol=command.symbol,
                side=command.side,
                qty=command.size,
                order_type=command.order_type,
                execution_mode=command.execution_mode,
                execution_params=command.execution_params,
                timeout=max(1, round(self._timeout)),
                reference_price=command.reference_price,
                maker_preference=command.maker_preference,
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

        return str(command_id)

    def send_abort(
        self,
        *,
        command_id: str,
        account_id: str,
        reason: str,
        created_at: datetime,
    ) -> None:
        '''Submit trade abort to Praxis via async bridge.

        Args:
            command_id: Command to abort.
            account_id: Owning account for the command.
            reason: Reason for the abort (e.g. 'shutdown').
            created_at: Abort creation timestamp (UTC, timezone-aware).

        Raises:
            RuntimeError: If submit_abort_fn is not configured.
            TimeoutError: If Praxis does not respond within timeout.
            Exception: Propagates the original exception raised by submit_abort_fn.
        '''

        if self._submit_abort_fn is None:
            msg = 'submit_abort_fn not configured'
            raise RuntimeError(msg)

        future: concurrent.futures.Future[None] = asyncio.run_coroutine_threadsafe(
            self._submit_abort_fn(
                command_id=command_id,
                account_id=account_id,
                reason=reason,
                created_at=created_at,
            ),
            self._loop,
        )

        try:
            future.result(timeout=self._timeout)
        except (TimeoutError, concurrent.futures.TimeoutError):
            future.cancel()
            _log.error('submit_abort timed out: command_id=%s', command_id)
            raise

        _log.info('abort submitted', extra={'command_id': command_id, 'reason': reason})

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

        future: concurrent.futures.Future[None] = asyncio.run_coroutine_threadsafe(
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

    def pull_positions(self, account_id: str) -> dict[tuple[str, str], Any]:
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
