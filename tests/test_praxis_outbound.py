'''Tests for PraxisOutbound sync-to-async bridge.'''

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from nexus.core.domain.enums import OrderSide
from nexus.core.stp_mode import STPMode
from nexus.infrastructure.praxis_connector.praxis_outbound import PraxisOutbound
from nexus.infrastructure.praxis_connector.trade_command import TradeCommand
from nexus.infrastructure.praxis_connector.trade_command_type import TradeCommandType


@pytest.fixture()
def event_loop_thread() -> tuple[asyncio.AbstractEventLoop, threading.Thread]:
    '''Start an asyncio event loop in a background thread.'''

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    yield loop, thread
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=5)
    loop.close()


def _make_command(command_id: str = 'cmd_001') -> TradeCommand:
    return TradeCommand(
        command_id=command_id,
        command_type=TradeCommandType.NEW_ORDER,
        account_id='acc_001',
        venue='binance_spot',
        symbol='BTCUSDT',
        notional=Decimal('1000'),
        created_at=datetime.now(tz=timezone.utc),
        side=OrderSide.BUY,
        size=Decimal('0.01'),
        stp_mode=STPMode.CANCEL_TAKER,
        trade_id='trade_001',
    )


class TestPraxisOutbound:

    def test_sends_command_and_returns_id(
        self,
        event_loop_thread: tuple[asyncio.AbstractEventLoop, threading.Thread],
    ) -> None:
        '''send_command bridges to async and returns command_id.'''

        loop, _ = event_loop_thread

        async def mock_submit(**_kwargs: object) -> str:
            return 'praxis_cmd_42'

        outbound = PraxisOutbound(
            submit_fn=mock_submit,
            loop=loop,
        )

        command = _make_command()
        result = outbound.send_command(command)

        assert result == 'praxis_cmd_42'

    def test_passes_correct_fields(
        self,
        event_loop_thread: tuple[asyncio.AbstractEventLoop, threading.Thread],
    ) -> None:
        '''send_command passes TradeCommand fields to submit_fn.'''

        loop, _ = event_loop_thread
        received_kwargs: dict[str, object] = {}

        async def capture_submit(**kwargs: object) -> str:
            received_kwargs.update(kwargs)
            return 'cmd_id'

        outbound = PraxisOutbound(
            submit_fn=capture_submit,
            loop=loop,
        )

        command = _make_command()
        outbound.send_command(command)

        assert received_kwargs['account_id'] == 'acc_001'
        assert received_kwargs['symbol'] == 'BTCUSDT'
        assert received_kwargs['side'] == OrderSide.BUY
        assert received_kwargs['trade_id'] == 'trade_001'
        assert received_kwargs['stp_mode'] == STPMode.CANCEL_TAKER

    def test_timeout_raises(
        self,
        event_loop_thread: tuple[asyncio.AbstractEventLoop, threading.Thread],
    ) -> None:
        '''send_command raises TimeoutError if async call exceeds timeout.'''

        loop, _ = event_loop_thread

        async def slow_submit(**_kwargs: object) -> str:
            await asyncio.sleep(10)
            return 'never'

        outbound = PraxisOutbound(
            submit_fn=slow_submit,
            loop=loop,
            timeout=0.5,
        )

        command = _make_command()

        with pytest.raises((TimeoutError, concurrent.futures.TimeoutError)):
            outbound.send_command(command)

    def test_async_error_propagates(
        self,
        event_loop_thread: tuple[asyncio.AbstractEventLoop, threading.Thread],
    ) -> None:
        '''Async exception propagates to sync caller.'''

        loop, _ = event_loop_thread

        async def failing_submit(**_kwargs: object) -> str:
            msg = 'account not registered'
            raise ValueError(msg)

        outbound = PraxisOutbound(
            submit_fn=failing_submit,
            loop=loop,
        )

        command = _make_command()

        with pytest.raises(ValueError, match='account not registered'):
            outbound.send_command(command)
