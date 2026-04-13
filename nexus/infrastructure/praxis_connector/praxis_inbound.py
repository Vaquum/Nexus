'''Concrete inbound connector consuming TradeOutcomes from a thread-safe queue.

Praxis pushes outcomes into the queue via on_trade_outcome callback,
routed by account_id. The Nexus instance thread calls receive_outcome
to consume them.
'''

from __future__ import annotations

import queue

from nexus.infrastructure.praxis_connector.trade_outcome import TradeOutcome

__all__ = ['PraxisInbound']

_DEFAULT_POLL_TIMEOUT = 0.1


class PraxisInbound:
    '''Queue-based inbound connector for receiving TradeOutcomes.

    Args:
        outcome_queue: Thread-safe queue fed by Praxis on_trade_outcome callback.
        poll_timeout: Seconds to wait on queue.get before returning None.
    '''

    def __init__(
        self,
        outcome_queue: queue.Queue[TradeOutcome],
        poll_timeout: float = _DEFAULT_POLL_TIMEOUT,
    ) -> None:
        self._queue = outcome_queue
        self._poll_timeout = poll_timeout

    def receive_outcome(self) -> TradeOutcome | None:
        '''Receive next TradeOutcome from the queue.

        Returns:
            TradeOutcome if available within poll_timeout, None otherwise.
        '''

        try:
            return self._queue.get(timeout=self._poll_timeout)
        except queue.Empty:
            return None

