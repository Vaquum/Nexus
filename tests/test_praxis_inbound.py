'''Tests for PraxisInbound queue-based outcome consumption.'''

from __future__ import annotations

import queue
from datetime import datetime, timezone

from nexus.infrastructure.praxis_connector.praxis_inbound import PraxisInbound
from nexus.infrastructure.praxis_connector.trade_outcome import TradeOutcome
from nexus.infrastructure.praxis_connector.trade_outcome_type import TradeOutcomeType


def _make_outcome(command_id: str = 'cmd_001') -> TradeOutcome:
    return TradeOutcome(
        outcome_id='out_001',
        command_id=command_id,
        outcome_type=TradeOutcomeType.ACK,
        timestamp=datetime.now(tz=timezone.utc),
    )


class TestPraxisInbound:

    def test_receive_returns_outcome(self) -> None:
        '''receive_outcome returns queued outcome.'''

        q: queue.Queue[TradeOutcome] = queue.Queue()
        outcome = _make_outcome()
        q.put(outcome)

        inbound = PraxisInbound(outcome_queue=q)
        result = inbound.receive_outcome()

        assert result is outcome

    def test_receive_returns_none_when_empty(self) -> None:
        '''receive_outcome returns None when queue is empty.'''

        q: queue.Queue[TradeOutcome] = queue.Queue()
        inbound = PraxisInbound(outcome_queue=q, poll_timeout=0.01)

        result = inbound.receive_outcome()

        assert result is None

    def test_receive_preserves_order(self) -> None:
        '''Outcomes are received in FIFO order.'''

        q: queue.Queue[TradeOutcome] = queue.Queue()
        o1 = _make_outcome('cmd_001')
        o2 = _make_outcome('cmd_002')
        q.put(o1)
        q.put(o2)

        inbound = PraxisInbound(outcome_queue=q)

        assert inbound.receive_outcome() is o1
        assert inbound.receive_outcome() is o2

