'''Tests for PraxisOutbound sync-to-async bridge.'''

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from nexus.core.domain.enums import OrderSide
from nexus.core.domain.order_types import ExecutionMode, MakerPreference, OrderType
from nexus.core.health_evaluator import HealthSnapshot
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


def _make_command(command_id: str = 'cmd_001', strategy_id: str | None = 'momentum') -> TradeCommand:
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
        execution_mode=ExecutionMode.SINGLE_SHOT,
        order_type=OrderType.MARKET,
        execution_params={'slippage_bps': 10},
        deadline=300,
        maker_preference=MakerPreference.NO_PREFERENCE,
        reference_price=Decimal('100000'),
        strategy_id=strategy_id,
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
        assert received_kwargs['order_type'] == OrderType.MARKET
        assert received_kwargs['execution_mode'] == ExecutionMode.SINGLE_SHOT
        assert received_kwargs['execution_params'] == {'slippage_bps': 10}
        assert received_kwargs['maker_preference'] == MakerPreference.NO_PREFERENCE
        assert received_kwargs['reference_price'] == Decimal('100000')
        assert received_kwargs['timeout'] == 300
        assert received_kwargs['strategy_id'] == 'momentum'

    def test_strategy_id_passed_through_when_none(
        self,
        event_loop_thread: tuple[asyncio.AbstractEventLoop, threading.Thread],
    ) -> None:
        '''When `TradeCommand.strategy_id` is None (legacy path or
        non-strategy-attributed command), the kwarg is passed as None.
        Praxis-side `submit_command` accepts `strategy_id: str | None = None`
        and falls back to its own default behavior.
        '''

        loop, _ = event_loop_thread
        received_kwargs: dict[str, object] = {}

        async def capture_submit(**kwargs: object) -> str:
            received_kwargs.update(kwargs)
            return 'cmd_id'

        outbound = PraxisOutbound(submit_fn=capture_submit, loop=loop)
        outbound.send_command(_make_command(strategy_id=None))

        assert 'strategy_id' in received_kwargs
        assert received_kwargs['strategy_id'] is None

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


class TestPraxisOutboundSendAbort:

    def test_sends_abort_with_fields(
        self,
        event_loop_thread: tuple[asyncio.AbstractEventLoop, threading.Thread],
    ) -> None:
        '''send_abort bridges to async submit_abort_fn with named fields.'''

        loop, _ = event_loop_thread
        received: dict[str, object] = {}

        async def mock_submit_abort(**kwargs: object) -> None:
            received.update(kwargs)

        async def mock_submit(**_kwargs: object) -> str:
            return 'unused'

        outbound = PraxisOutbound(
            submit_fn=mock_submit,
            loop=loop,
            submit_abort_fn=mock_submit_abort,
        )

        created_at = datetime.now(tz=timezone.utc)
        outbound.send_abort(
            command_id='cmd_42',
            account_id='acc_001',
            reason='shutdown',
            created_at=created_at,
        )

        assert received['command_id'] == 'cmd_42'
        assert received['account_id'] == 'acc_001'
        assert received['reason'] == 'shutdown'
        assert received['created_at'] == created_at

    def test_raises_when_fn_not_configured(
        self,
        event_loop_thread: tuple[asyncio.AbstractEventLoop, threading.Thread],
    ) -> None:
        '''send_abort raises RuntimeError if submit_abort_fn is None.'''

        loop, _ = event_loop_thread

        async def mock_submit(**_kwargs: object) -> str:
            return 'unused'

        outbound = PraxisOutbound(submit_fn=mock_submit, loop=loop)

        with pytest.raises(RuntimeError, match='submit_abort_fn not configured'):
            outbound.send_abort(
                command_id='cmd_42',
                account_id='acc_001',
                reason='shutdown',
                created_at=datetime.now(tz=timezone.utc),
            )

    def test_async_error_propagates(
        self,
        event_loop_thread: tuple[asyncio.AbstractEventLoop, threading.Thread],
    ) -> None:
        '''Async submit_abort_fn exception propagates to sync caller.'''

        loop, _ = event_loop_thread

        async def failing_abort(**_kwargs: object) -> None:
            msg = 'unknown command'
            raise ValueError(msg)

        async def mock_submit(**_kwargs: object) -> str:
            return 'unused'

        outbound = PraxisOutbound(
            submit_fn=mock_submit,
            loop=loop,
            submit_abort_fn=failing_abort,
        )

        with pytest.raises(ValueError, match='unknown command'):
            outbound.send_abort(
                command_id='cmd_42',
                account_id='acc_001',
                reason='shutdown',
                created_at=datetime.now(tz=timezone.utc),
            )

    def test_naive_created_at_rejected(
        self,
        event_loop_thread: tuple[asyncio.AbstractEventLoop, threading.Thread],
    ) -> None:
        '''send_abort rejects naive datetime.'''

        loop, _ = event_loop_thread

        async def mock_submit_abort(**_kwargs: object) -> None:
            return

        async def mock_submit(**_kwargs: object) -> str:
            return 'unused'

        outbound = PraxisOutbound(
            submit_fn=mock_submit,
            loop=loop,
            submit_abort_fn=mock_submit_abort,
        )

        with pytest.raises(ValueError, match='must be timezone-aware UTC'):
            outbound.send_abort(
                command_id='cmd_42',
                account_id='acc_001',
                reason='shutdown',
                created_at=datetime(2026, 4, 18, 12, 0, 0),
            )

    def test_non_utc_created_at_rejected(
        self,
        event_loop_thread: tuple[asyncio.AbstractEventLoop, threading.Thread],
    ) -> None:
        '''send_abort rejects non-UTC timezone.'''

        loop, _ = event_loop_thread

        async def mock_submit_abort(**_kwargs: object) -> None:
            return

        async def mock_submit(**_kwargs: object) -> str:
            return 'unused'

        outbound = PraxisOutbound(
            submit_fn=mock_submit,
            loop=loop,
            submit_abort_fn=mock_submit_abort,
        )

        non_utc = datetime(2026, 4, 18, 12, 0, 0, tzinfo=timezone(timedelta(hours=5)))
        with pytest.raises(ValueError, match='must be timezone-aware UTC'):
            outbound.send_abort(
                command_id='cmd_42',
                account_id='acc_001',
                reason='shutdown',
                created_at=non_utc,
            )


class TestPraxisOutboundGetHealthSnapshot:

    def test_pulls_snapshot(
        self,
        event_loop_thread: tuple[asyncio.AbstractEventLoop, threading.Thread],
    ) -> None:
        '''get_health_snapshot bridges to async fn and returns the snapshot.'''

        loop, _ = event_loop_thread
        target = HealthSnapshot(latency_p99_ms=42.0, consecutive_failures=1)
        received: dict[str, object] = {}

        async def fake_get_snapshot(account_id: str) -> HealthSnapshot:
            received['account_id'] = account_id
            return target

        async def mock_submit(**_kwargs: object) -> str:
            return 'unused'

        outbound = PraxisOutbound(
            submit_fn=mock_submit,
            loop=loop,
            get_health_snapshot_fn=fake_get_snapshot,
        )

        result = outbound.get_health_snapshot('acc-1')

        assert result is target
        assert received['account_id'] == 'acc-1'

    def test_raises_when_fn_not_configured(
        self,
        event_loop_thread: tuple[asyncio.AbstractEventLoop, threading.Thread],
    ) -> None:
        '''get_health_snapshot raises RuntimeError when fn is None.'''

        loop, _ = event_loop_thread

        async def mock_submit(**_kwargs: object) -> str:
            return 'unused'

        outbound = PraxisOutbound(submit_fn=mock_submit, loop=loop)

        with pytest.raises(RuntimeError, match='get_health_snapshot_fn not configured'):
            outbound.get_health_snapshot('acc-1')

    def test_async_error_propagates(
        self,
        event_loop_thread: tuple[asyncio.AbstractEventLoop, threading.Thread],
    ) -> None:
        '''Exception from get_health_snapshot_fn propagates.'''

        loop, _ = event_loop_thread

        async def failing_get_snapshot(_account_id: str) -> object:
            msg = 'praxis not started'
            raise RuntimeError(msg)

        async def mock_submit(**_kwargs: object) -> str:
            return 'unused'

        outbound = PraxisOutbound(
            submit_fn=mock_submit,
            loop=loop,
            get_health_snapshot_fn=failing_get_snapshot,
        )

        with pytest.raises(RuntimeError, match='praxis not started'):
            outbound.get_health_snapshot('acc-1')
