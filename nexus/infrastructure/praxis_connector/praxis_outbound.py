'''Concrete outbound connector bridging sync Nexus to async Praxis.

Uses asyncio.run_coroutine_threadsafe to call Praxis async methods
from a sync Nexus thread.
'''

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import logging
from collections.abc import Callable, Coroutine
from datetime import datetime, timezone
from typing import Any

from nexus.core.health_evaluator import HealthSnapshot
from nexus.infrastructure.praxis_connector.trade_command import TradeCommand

__all__ = ['CommandIdMismatchError', 'PraxisOutbound']

_log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30.0


class CommandIdMismatchError(RuntimeError):
    '''Raised when Praxis returns an id != the transmitted deterministic id.

    Only reachable when passthrough is active (the submit function
    accepts `command_id`) yet the receiver did not honour it. Treated
    as a submission failure so the caller rolls back the
    pre-registration rather than holding a registry keyed on an id the
    venue command does not carry.
    '''


def _command_id_support(
    submit_fn: Callable[..., Coroutine[Any, Any, str]],
) -> tuple[bool, bool]:
    '''Classify how `submit_fn` accepts a `command_id` keyword argument.

    The injected Praxis `submit_command` gained an optional
    `command_id` parameter; older Praxis builds did not. Detected once
    at construction so the deterministic id is passed through only when
    the receiver can take it.

    Returns:
        A `(passes, authoritative)` pair. `passes` is True when the id
        can be supplied at all — an explicit `command_id` parameter or
        a `**kwargs` catch-all. `authoritative` is True only for an
        explicit `command_id` parameter: such a receiver contracts to
        honour the id verbatim, so a divergent returned id is a real
        violation worth failing on. A `**kwargs`-only receiver might
        accept the kwarg and still mint its own id (a legacy Praxis
        wrapped in `**kwargs`), so passthrough there is best-effort and
        a divergent return is tolerated.
    '''

    try:
        parameters = inspect.signature(submit_fn).parameters
    except (ValueError, TypeError):
        return False, False

    if 'command_id' in parameters:
        return True, True

    has_var_keyword = any(
        param.kind is inspect.Parameter.VAR_KEYWORD
        for param in parameters.values()
    )

    return has_var_keyword, False


class PraxisOutbound:
    '''Sync-to-async bridge for Praxis Trading operations.

    Args:
        submit_fn: Async callable matching Praxis Trading.submit_command signature.
        register_fn: Sync callable matching Praxis Trading.register_account.
        unregister_fn: Async callable matching Praxis Trading.unregister_account.
        pull_positions_fn: Sync callable matching Praxis Trading.pull_positions.
        submit_abort_fn: Async callable wrapping Praxis Trading.submit_abort.
        get_health_snapshot_fn: Async callable wrapping Praxis Trading.get_health_snapshot.
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
        get_health_snapshot_fn: Callable[[str], Coroutine[Any, Any, HealthSnapshot]] | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._submit_fn = submit_fn
        (
            self._submit_supports_command_id,
            self._submit_command_id_authoritative,
        ) = _command_id_support(submit_fn)
        self._loop = loop
        self._register_fn = register_fn
        self._unregister_fn = unregister_fn
        self._pull_positions_fn = pull_positions_fn
        self._submit_abort_fn = submit_abort_fn
        self._get_health_snapshot_fn = get_health_snapshot_fn
        self._timeout = timeout

    @property
    def supports_command_id(self) -> bool:
        '''Whether the submit function authoritatively honours the id.

        True only for a receiver that declares `command_id` as an
        explicit parameter — one that contracts to use the transmitted
        id verbatim. The launcher gates pre-registration (and the
        `SUBMISSION_UNKNOWN` timeout semantics) on this: pre-registering
        against `command.command_id` is only safe when the venue command
        is guaranteed to carry that same id. A `**kwargs`-only receiver
        is deliberately excluded — it might accept the kwarg yet mint
        its own id, so the launcher falls back to the legacy
        submit-then-register path for it, as it does for an old Praxis
        that lacks the parameter entirely.
        '''

        return self._submit_command_id_authoritative

    def send_command(self, command: TradeCommand) -> str:
        '''Submit TradeCommand to Praxis via async bridge.

        Args:
            command: Validated TradeCommand from Nexus.

        Returns:
            Command ID assigned by Praxis.

        Raises:
            TimeoutError: If Praxis does not respond within timeout.
            CommandIdMismatchError: If passthrough is active and Praxis
                returns a command id other than the transmitted one.
            Exception: Propagates the original exception raised by submit_fn.
        '''

        order_timeout = (
            command.deadline
            if command.deadline is not None
            else max(1, round(self._timeout))
        )
        submit_kwargs: dict[str, Any] = {
            'trade_id': command.trade_id or command.command_id,
            'account_id': command.account_id,
            'symbol': command.symbol,
            'side': command.side,
            'qty': command.size,
            'order_type': command.order_type,
            'execution_mode': command.execution_mode,
            'execution_params': command.execution_params,
            'timeout': order_timeout,
            'reference_price': command.reference_price,
            'maker_preference': command.maker_preference,
            'stp_mode': command.stp_mode,
            'created_at': command.created_at,
            'strategy_id': command.strategy_id,
        }

        if command.quote_qty is not None:
            submit_kwargs['quote_qty'] = command.quote_qty

        if self._submit_supports_command_id:
            submit_kwargs['command_id'] = command.command_id

        future: concurrent.futures.Future[str] = asyncio.run_coroutine_threadsafe(
            self._submit_fn(**submit_kwargs),
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

        if (
            self._submit_command_id_authoritative
            and str(command_id) != command.command_id
        ):
            _log.error(
                'praxis returned a command_id differing from the '
                'transmitted deterministic id; identity passthrough '
                'was not honoured — failing the submission so the '
                'caller rolls back rather than holding a registration '
                'keyed on an id the venue command does not carry',
                extra={
                    'nexus_command_id': command.command_id,
                    'praxis_command_id': command_id,
                },
            )
            msg = (
                f'praxis command_id {command_id!r} does not match the '
                f'transmitted deterministic id {command.command_id!r}'
            )
            raise CommandIdMismatchError(msg)

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

        if created_at.tzinfo is None or created_at.utcoffset() != timezone.utc.utcoffset(None):
            msg = 'send_abort.created_at must be timezone-aware UTC'
            raise ValueError(msg)

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

    def get_health_snapshot(self, account_id: str) -> HealthSnapshot:
        '''Pull a HealthSnapshot from Praxis via async bridge.

        Unlike send_command and send_abort, this does not require the
        account to be ready: Praxis intentionally serves a default zeroed
        snapshot for unknown accounts so a Manager can poll across the
        whole lifecycle.

        Args:
            account_id: Account whose snapshot is requested.

        Returns:
            HealthSnapshot composed by the Trading sub-system. Returns
            default-valued snapshot when Praxis has no samples yet.

        Raises:
            RuntimeError: If get_health_snapshot_fn is not configured.
            TimeoutError: If Praxis does not respond within timeout.
            Exception: Propagates the original exception raised by the fn.
        '''

        if self._get_health_snapshot_fn is None:
            msg = 'get_health_snapshot_fn not configured'
            raise RuntimeError(msg)

        future: concurrent.futures.Future[HealthSnapshot] = (
            asyncio.run_coroutine_threadsafe(
                self._get_health_snapshot_fn(account_id),
                self._loop,
            )
        )

        try:
            return future.result(timeout=self._timeout)
        except (TimeoutError, concurrent.futures.TimeoutError):
            future.cancel()
            _log.error('get_health_snapshot timed out: account_id=%s', account_id)
            raise

    def pull_positions(self, account_id: str) -> dict[tuple[str, str], Any]:
        '''Pull positions snapshot from Praxis Trading.

        Args:
            account_id: Account identifier to query.

        Returns:
            Positions keyed by (trade_id, account_id) tuples. Consumers
            should match on `pos.trade_id` rather than tuple position —
            the key ordering is a Praxis-internal detail, and reading it
            positionally is what previously mis-bucketed every position.

        Raises:
            RuntimeError: If pull_positions_fn is not configured.
        '''

        if self._pull_positions_fn is None:
            msg = 'pull_positions_fn not configured'
            raise RuntimeError(msg)

        return self._pull_positions_fn(account_id)
