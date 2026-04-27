from decimal import Decimal

import pytest

from nexus.core.domain.enums import OrderSide
from nexus.infrastructure.praxis_connector.order_context import OrderContext


def _entry_context() -> OrderContext:
    return OrderContext(
        command_id='cmd_001',
        strategy_id='strat_001',
        trade_id=None,
        side=OrderSide.BUY,
        order_size=Decimal('0.01'),
        order_notional=Decimal('500'),
        estimated_fees=Decimal('0.5'),
        is_entry=True,
    )


def _exit_context() -> OrderContext:
    return OrderContext(
        command_id='cmd_002',
        strategy_id='strat_001',
        trade_id='trade_001',
        side=OrderSide.SELL,
        order_size=Decimal('0.01'),
        order_notional=Decimal('510'),
        estimated_fees=Decimal('0.51'),
        is_entry=False,
    )


class TestOrderContextConstruction:
    def test_entry_context_valid(self) -> None:
        ctx = _entry_context()
        assert ctx.command_id == 'cmd_001'
        assert ctx.strategy_id == 'strat_001'
        assert ctx.trade_id is None
        assert ctx.side == OrderSide.BUY
        assert ctx.order_size == Decimal('0.01')
        assert ctx.order_notional == Decimal('500')
        assert ctx.estimated_fees == Decimal('0.5')
        assert ctx.is_entry is True

    def test_exit_context_valid(self) -> None:
        ctx = _exit_context()
        assert ctx.command_id == 'cmd_002'
        assert ctx.trade_id == 'trade_001'
        assert ctx.side == OrderSide.SELL
        assert ctx.is_entry is False

    def test_frozen(self) -> None:
        ctx = _entry_context()
        with pytest.raises(AttributeError):
            ctx.command_id = 'changed'  # type: ignore[misc]


class TestOrderContextIsEntryExit:
    def test_entry_flag_true(self) -> None:
        ctx = _entry_context()
        assert ctx.is_entry is True
        assert ctx.is_exit is False

    def test_entry_flag_false(self) -> None:
        ctx = _exit_context()
        assert ctx.is_entry is False
        assert ctx.is_exit is True

    def test_short_entry_sell_side_is_entry(self) -> None:
        ctx = OrderContext(
            command_id='cmd_short_open',
            strategy_id='strat_001',
            trade_id=None,
            side=OrderSide.SELL,
            order_size=Decimal('0.01'),
            order_notional=Decimal('500'),
            estimated_fees=Decimal('0.5'),
            is_entry=True,
        )
        assert ctx.is_entry is True
        assert ctx.is_exit is False

    def test_short_exit_buy_side_is_exit(self) -> None:
        ctx = OrderContext(
            command_id='cmd_short_close',
            strategy_id='strat_001',
            trade_id='trade_001',
            side=OrderSide.BUY,
            order_size=Decimal('0.01'),
            order_notional=Decimal('510'),
            estimated_fees=Decimal('0.51'),
            is_entry=False,
        )
        assert ctx.is_entry is False
        assert ctx.is_exit is True


class TestOrderContextIdValidation:
    def test_empty_command_id_rejected(self) -> None:
        with pytest.raises(ValueError, match='command_id must be a non-empty string'):
            OrderContext(
                command_id='',
                strategy_id='strat_001',
                trade_id=None,
                side=OrderSide.BUY,
                order_size=Decimal('0.01'),
                order_notional=Decimal('500'),
                estimated_fees=Decimal('0.5'),
                is_entry=True,
            )

    def test_whitespace_command_id_rejected(self) -> None:
        with pytest.raises(ValueError, match='command_id must be a non-empty string'):
            OrderContext(
                command_id='   ',
                strategy_id='strat_001',
                trade_id=None,
                side=OrderSide.BUY,
                order_size=Decimal('0.01'),
                order_notional=Decimal('500'),
                estimated_fees=Decimal('0.5'),
                is_entry=True,
            )

    def test_empty_strategy_id_rejected(self) -> None:
        with pytest.raises(ValueError, match='strategy_id must be a non-empty string'):
            OrderContext(
                command_id='cmd_001',
                strategy_id='',
                trade_id=None,
                side=OrderSide.BUY,
                order_size=Decimal('0.01'),
                order_notional=Decimal('500'),
                estimated_fees=Decimal('0.5'),
                is_entry=True,
            )

    def test_empty_trade_id_rejected(self) -> None:
        with pytest.raises(
            ValueError, match='trade_id must be a non-empty string if provided'
        ):
            OrderContext(
                command_id='cmd_001',
                strategy_id='strat_001',
                trade_id='',
                side=OrderSide.BUY,
                order_size=Decimal('0.01'),
                order_notional=Decimal('500'),
                estimated_fees=Decimal('0.5'),
                is_entry=True,
            )

    def test_whitespace_trade_id_rejected(self) -> None:
        with pytest.raises(
            ValueError, match='trade_id must be a non-empty string if provided'
        ):
            OrderContext(
                command_id='cmd_001',
                strategy_id='strat_001',
                trade_id='   ',
                side=OrderSide.BUY,
                order_size=Decimal('0.01'),
                order_notional=Decimal('500'),
                estimated_fees=Decimal('0.5'),
                is_entry=True,
            )


class TestOrderContextNumericValidation:
    def test_order_size_zero_rejected(self) -> None:
        with pytest.raises(ValueError, match='order_size must be positive'):
            OrderContext(
                command_id='cmd_001',
                strategy_id='strat_001',
                trade_id=None,
                side=OrderSide.BUY,
                order_size=Decimal('0'),
                order_notional=Decimal('500'),
                estimated_fees=Decimal('0.5'),
                is_entry=True,
            )

    def test_order_size_negative_rejected(self) -> None:
        with pytest.raises(ValueError, match='order_size must be positive'):
            OrderContext(
                command_id='cmd_001',
                strategy_id='strat_001',
                trade_id=None,
                side=OrderSide.BUY,
                order_size=Decimal('-0.01'),
                order_notional=Decimal('500'),
                estimated_fees=Decimal('0.5'),
                is_entry=True,
            )

    def test_order_size_nan_rejected(self) -> None:
        with pytest.raises(ValueError, match='order_size must be a finite Decimal'):
            OrderContext(
                command_id='cmd_001',
                strategy_id='strat_001',
                trade_id=None,
                side=OrderSide.BUY,
                order_size=Decimal('NaN'),
                order_notional=Decimal('500'),
                estimated_fees=Decimal('0.5'),
                is_entry=True,
            )

    def test_order_notional_zero_rejected(self) -> None:
        with pytest.raises(ValueError, match='order_notional must be positive'):
            OrderContext(
                command_id='cmd_001',
                strategy_id='strat_001',
                trade_id=None,
                side=OrderSide.BUY,
                order_size=Decimal('0.01'),
                order_notional=Decimal('0'),
                estimated_fees=Decimal('0.5'),
                is_entry=True,
            )

    def test_order_notional_nan_rejected(self) -> None:
        with pytest.raises(ValueError, match='order_notional must be a finite Decimal'):
            OrderContext(
                command_id='cmd_001',
                strategy_id='strat_001',
                trade_id=None,
                side=OrderSide.BUY,
                order_size=Decimal('0.01'),
                order_notional=Decimal('NaN'),
                estimated_fees=Decimal('0.5'),
                is_entry=True,
            )

    def test_estimated_fees_negative_rejected(self) -> None:
        with pytest.raises(ValueError, match='estimated_fees must be non-negative'):
            OrderContext(
                command_id='cmd_001',
                strategy_id='strat_001',
                trade_id=None,
                side=OrderSide.BUY,
                order_size=Decimal('0.01'),
                order_notional=Decimal('500'),
                estimated_fees=Decimal('-0.5'),
                is_entry=True,
            )

    def test_estimated_fees_nan_rejected(self) -> None:
        with pytest.raises(ValueError, match='estimated_fees must be a finite Decimal'):
            OrderContext(
                command_id='cmd_001',
                strategy_id='strat_001',
                trade_id=None,
                side=OrderSide.BUY,
                order_size=Decimal('0.01'),
                order_notional=Decimal('500'),
                estimated_fees=Decimal('NaN'),
                is_entry=True,
            )

    def test_estimated_fees_zero_valid(self) -> None:
        ctx = OrderContext(
            command_id='cmd_001',
            strategy_id='strat_001',
            trade_id=None,
            side=OrderSide.BUY,
            order_size=Decimal('0.01'),
            order_notional=Decimal('500'),
            estimated_fees=Decimal('0'),
            is_entry=True,
        )
        assert ctx.estimated_fees == Decimal('0')

    def test_is_entry_non_bool_rejected(self) -> None:
        with pytest.raises(ValueError, match='is_entry must be a bool'):
            OrderContext(
                command_id='cmd_001',
                strategy_id='strat_001',
                trade_id=None,
                side=OrderSide.BUY,
                order_size=Decimal('0.01'),
                order_notional=Decimal('500'),
                estimated_fees=Decimal('0.5'),
                is_entry='yes',  # type: ignore[arg-type]
            )
