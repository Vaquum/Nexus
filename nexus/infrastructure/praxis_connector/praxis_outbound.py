'''Concrete outbound connector bridging sync Nexus to async Praxis.

Uses asyncio.run_coroutine_threadsafe to call Praxis Trading.submit_command()
from a sync Nexus thread.
'''

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from nexus.infrastructure.praxis_connector.trade_command import TradeCommand

__all__ = ['PraxisOutbound']

_log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30.0


class PraxisOutbound:
    '''Sync-to-async bridge for submitting commands to Praxis.

    Args:
        submit_fn: Async callable matching Praxis Trading.submit_command signature.
        loop: Asyncio event loop running in the Praxis thread.
        timeout: Seconds to wait for the async call to complete.
    '''

    def __init__(
        self,
        submit_fn: Callable[..., Awaitable[str]],
        loop: asyncio.AbstractEventLoop,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._submit_fn = submit_fn
        self._loop = loop
        self._timeout = timeout

    def send_command(self, command: TradeCommand) -> str:
        '''Submit TradeCommand to Praxis via async bridge.

        Args:
            command: Validated TradeCommand from Nexus.

        Returns:
            Command ID assigned by Praxis.

        Raises:
            TimeoutError: If Praxis does not respond within timeout.
            RuntimeError: If the async call fails.
        '''

        future = asyncio.run_coroutine_threadsafe(
            self._submit_fn(
                trade_id=command.trade_id or command.command_id,
                account_id=command.account_id,
                symbol=command.symbol,
                side=command.side,
                qty=command.size,
                order_type=command.command_type,
                execution_mode=command.command_type,
                execution_params=None,
                timeout=int(self._timeout),
                reference_price=None,
                maker_preference=None,
                stp_mode=command.stp_mode,
                created_at=command.created_at,
            ),
            self._loop,
        )

        try:
            command_id = future.result(timeout=self._timeout)
        except TimeoutError:
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
