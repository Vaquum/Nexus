'''Verify intake stage validation skeleton and hook behavior.'''

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from typing import Any, cast
from datetime import datetime, timezone

import pytest

from nexus.core.validator import (
    IntakeValidationHook,
    ValidationAction,
    ValidationDecision,
    ValidationRequestContext,
    ValidationStage,
    build_default_intake_hooks,
    make_duplicate_order_hook,
    make_order_rate_hook,
    make_reference_integrity_hook,
    validate_intake_stage,
)
from nexus.core.domain.enums import OrderSide
from nexus.core.domain.instance_state import InstanceState
from nexus.core.domain.position import Position
from nexus.instance_config import InstanceConfig


def _make_context(**overrides: Any) -> ValidationRequestContext:
    config = InstanceConfig(
        account_id='acc_001',
        venue='binance_spot',
        allocated_capital=Decimal('10000'),
    )
    defaults: dict[str, Any] = {
        'strategy_id': 'strat_a',
        'action': ValidationAction.ENTER,
        'command_id': 'cmd_default',
        'symbol': 'BTCUSDT',
        'order_side': OrderSide.BUY,
        'order_size': Decimal('1'),
        'order_notional': Decimal('100'),
        'estimated_fees': Decimal('1'),
        'strategy_budget': Decimal('5000'),
        'state': InstanceState.from_config(config),
        'config': config,
    }
    defaults.update(overrides)
    return ValidationRequestContext(**defaults)


class TestIntakeBuiltins:
    def test_allows_valid_request_shape(self) -> None:
        decision = validate_intake_stage(_make_context())
        assert decision.allowed is True

    def test_rejects_whitespace_strategy_id(self) -> None:
        decision = validate_intake_stage(_make_context(strategy_id=' strat_a '))
        assert decision.allowed is False
        assert decision.failed_stage == ValidationStage.INTAKE
        assert decision.reason_code == 'INTAKE_STRATEGY_ID_WHITESPACE'

    def test_rejects_zero_order_notional(self) -> None:
        decision = validate_intake_stage(_make_context(order_notional=Decimal('0')))
        assert decision.allowed is False
        assert decision.reason_code == 'INTAKE_ORDER_NOTIONAL_ZERO'

    def test_rejects_zero_strategy_budget(self) -> None:
        decision = validate_intake_stage(_make_context(strategy_budget=Decimal('0')))
        assert decision.allowed is False
        assert decision.reason_code == 'INTAKE_STRATEGY_BUDGET_ZERO'

    @pytest.mark.parametrize(
        ('action', 'side'),
        [
            (ValidationAction.EXIT, OrderSide.SELL),
            (ValidationAction.ABORT, OrderSide.BUY),
            (ValidationAction.CANCEL, OrderSide.BUY),
        ],
    )
    def test_zero_strategy_budget_allowed_for_safety_actions(
        self,
        action: ValidationAction,
        side: OrderSide,
    ) -> None:
        decision = validate_intake_stage(
            _make_context(
                action=action,
                order_side=side,
                strategy_budget=Decimal('0'),
            )
        )
        assert decision.allowed is True

    def test_rejects_missing_command_id_for_enter(self) -> None:
        with pytest.raises(
            ValueError,
            match=r'required for ValidationAction\.ENTER',
        ):
            _make_context(command_id=None)


class TestIntakeHooks:
    def test_hook_denial_short_circuits(self) -> None:
        def deny_hook(_: ValidationRequestContext) -> ValidationDecision:
            return ValidationDecision(
                allowed=False,
                failed_stage=ValidationStage.INTAKE,
                reason_code='INTAKE_CUSTOM_RULE',
                message='custom intake policy denied request',
            )

        decision = validate_intake_stage(_make_context(), hooks=(deny_hook,))
        assert decision.allowed is False
        assert decision.reason_code == 'INTAKE_CUSTOM_RULE'

    def test_hook_can_return_none_to_continue(self) -> None:
        called = False

        def no_op_hook(_: ValidationRequestContext) -> None:
            nonlocal called
            called = True

        decision = validate_intake_stage(_make_context(), hooks=(no_op_hook,))
        assert called is True
        assert decision.allowed is True

    def test_hook_must_return_decision_or_none(self) -> None:
        def bad_hook(_: ValidationRequestContext) -> object:
            return object()

        with pytest.raises(ValueError, match='must return ValidationDecision or None'):
            validate_intake_stage(
                _make_context(),
                hooks=(cast(IntakeValidationHook, bad_hook),),
            )

    def test_hook_denial_must_be_intake_stage(self) -> None:
        def bad_deny_hook(_: ValidationRequestContext) -> ValidationDecision:
            return ValidationDecision(
                allowed=False,
                failed_stage=ValidationStage.RISK,
                reason_code='BAD_STAGE',
                message='wrong stage',
            )

        with pytest.raises(ValueError, match='failed_stage=INTAKE'):
            validate_intake_stage(_make_context(), hooks=(bad_deny_hook,))


class TestRfcStageOneHooks:
    def test_order_rate_hook_rejects_bool_and_non_int(self) -> None:
        with pytest.raises(ValueError, match='must be an integer'):
            make_order_rate_hook(True)

        with pytest.raises(ValueError, match='must be an integer'):
            make_order_rate_hook(cast(int, cast(object, 1.5)))

    def test_duplicate_order_hook_rejects_bool_and_non_int(self) -> None:
        with pytest.raises(ValueError, match='must be an integer'):
            make_duplicate_order_hook(True)

        with pytest.raises(ValueError, match='must be an integer'):
            make_duplicate_order_hook(cast(int, cast(object, 1000.0)))

    def test_duplicate_hook_is_thread_safe_for_same_command_id(self) -> None:
        dupe_hook = make_duplicate_order_hook(1000)

        def run_once() -> bool:
            decision = validate_intake_stage(
                _make_context(command_id='cmd_shared'),
                hooks=(dupe_hook,),
            )
            return decision.allowed

        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(lambda _: run_once(), range(8)))

        assert outcomes.count(True) == 1
        assert outcomes.count(False) == 7

    def test_max_order_rate_enforced_for_enter(self) -> None:
        times = [
            datetime(2026, 3, 23, 10, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 3, 23, 10, 0, 0, 100000, tzinfo=timezone.utc),
            datetime(2026, 3, 23, 10, 0, 0, 200000, tzinfo=timezone.utc),
        ]
        idx = {'n': 0}

        def now_fn() -> datetime:
            t = times[idx['n']]
            idx['n'] += 1
            return t

        rate_hook = make_order_rate_hook(2, now_fn=now_fn)

        d1 = validate_intake_stage(_make_context(), hooks=(rate_hook,))
        d2 = validate_intake_stage(_make_context(), hooks=(rate_hook,))
        d3 = validate_intake_stage(_make_context(), hooks=(rate_hook,))

        assert d1.allowed is True
        assert d2.allowed is True
        assert d3.allowed is False
        assert d3.reason_code == 'INTAKE_MAX_ORDER_RATE_EXCEEDED'

    def test_max_order_rate_ignores_non_enter(self) -> None:
        rate_hook = make_order_rate_hook(1)
        decision = validate_intake_stage(
            _make_context(action=ValidationAction.EXIT, order_side=OrderSide.SELL),
            hooks=(rate_hook,),
        )
        assert decision.allowed is True

    def test_duplicate_window_detects_repeat_shape(self) -> None:
        times = [
            datetime(2026, 3, 23, 10, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 3, 23, 10, 0, 0, 300000, tzinfo=timezone.utc),
        ]
        idx = {'n': 0}

        def now_fn() -> datetime:
            t = times[idx['n']]
            idx['n'] += 1
            return t

        dupe_hook = make_duplicate_order_hook(1000, now_fn=now_fn)

        d1 = validate_intake_stage(_make_context(), hooks=(dupe_hook,))
        d2 = validate_intake_stage(_make_context(), hooks=(dupe_hook,))

        assert d1.allowed is True
        assert d2.allowed is False
        assert d2.reason_code == 'INTAKE_DUPLICATE_ORDER_WINDOW'

    def test_duplicate_window_keyed_by_strategy_id(self) -> None:
        dupe_hook = make_duplicate_order_hook(1000)
        d1 = validate_intake_stage(
            _make_context(strategy_id='strat_a'),
            hooks=(dupe_hook,),
        )
        d2 = validate_intake_stage(
            _make_context(strategy_id='strat_b'),
            hooks=(dupe_hook,),
        )
        assert d1.allowed is True
        assert d2.allowed is True

    def test_duplicate_window_allows_pyramiding_with_unique_command_ids(self) -> None:
        dupe_hook = make_duplicate_order_hook(1000)
        d1 = validate_intake_stage(
            _make_context(command_id='cmd_1'),
            hooks=(dupe_hook,),
        )
        d2 = validate_intake_stage(
            _make_context(command_id='cmd_2'),
            hooks=(dupe_hook,),
        )

        assert d1.allowed is True
        assert d2.allowed is True

    def test_reference_integrity_exit_requires_open_trade(self) -> None:
        ref_hook = make_reference_integrity_hook(active_command_ids=set())
        decision = validate_intake_stage(
            _make_context(
                action=ValidationAction.EXIT,
                order_side=OrderSide.SELL,
                trade_id='missing_trade',
            ),
            hooks=(ref_hook,),
        )
        assert decision.allowed is False
        assert decision.reason_code == 'INTAKE_TRADE_REFERENCE_INVALID'

    def test_reference_integrity_exit_size_bound(self) -> None:
        state = InstanceState.from_config(_make_context().config)
        state.positions['t1'] = Position(
            trade_id='t1',
            strategy_id='strat_a',
            symbol='BTCUSDT',
            side=OrderSide.BUY,
            size=Decimal('1.0'),
            entry_price=Decimal('50000'),
            pending_exit=Decimal('0.2'),
        )
        ref_hook = make_reference_integrity_hook(active_command_ids=set())

        decision = validate_intake_stage(
            _make_context(
                action=ValidationAction.EXIT,
                order_side=OrderSide.SELL,
                trade_id='t1',
                order_size=Decimal('0.9'),
                state=state,
            ),
            hooks=(ref_hook,),
        )

        assert decision.allowed is False
        assert decision.reason_code == 'INTAKE_EXIT_SIZE_EXCEEDS_REMAINING'

    def test_reference_integrity_modify_requires_active_command(self) -> None:
        ref_hook = make_reference_integrity_hook(active_command_ids={'cmd_ok'})

        decision = validate_intake_stage(
            _make_context(
                action=ValidationAction.MODIFY,
                command_id='cmd_missing',
                order_side=None,
            ),
            hooks=(ref_hook,),
        )
        assert decision.allowed is False
        assert decision.reason_code == 'INTAKE_COMMAND_REFERENCE_INVALID'

    def test_reference_integrity_modify_requires_positive_size(self) -> None:
        ref_hook = make_reference_integrity_hook(active_command_ids={'cmd_ok'})

        decision = validate_intake_stage(
            _make_context(
                action=ValidationAction.MODIFY,
                command_id='cmd_ok',
                order_size=Decimal('0'),
                order_side=None,
            ),
            hooks=(ref_hook,),
        )
        assert decision.allowed is False
        assert decision.reason_code == 'INTAKE_MODIFY_SIZE_INVALID'

    def test_spot_direction_rules_enforced(self) -> None:
        ref_hook = make_reference_integrity_hook(active_command_ids=set())
        decision = validate_intake_stage(
            _make_context(action=ValidationAction.ENTER, order_side=OrderSide.SELL),
            hooks=(ref_hook,),
        )
        assert decision.allowed is False
        assert decision.reason_code == 'INTAKE_SPOT_DIRECTION_INVALID'

    def test_enter_requires_positive_size(self) -> None:
        ref_hook = make_reference_integrity_hook(active_command_ids=set())
        decision = validate_intake_stage(
            _make_context(action=ValidationAction.ENTER, order_size=Decimal('0')),
            hooks=(ref_hook,),
        )
        assert decision.allowed is False
        assert decision.reason_code == 'INTAKE_ENTER_SIZE_INVALID'

    def test_default_hooks_use_config_duplicate_window_ms(self) -> None:
        config = InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            allocated_capital=Decimal('10000'),
            duplicate_window_ms=1000,
        )
        state = InstanceState.from_config(config)

        times = [
            datetime(2026, 3, 23, 10, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 3, 23, 10, 0, 0, 300000, tzinfo=timezone.utc),
        ]
        idx = {'n': 0}

        def now_fn() -> datetime:
            t = times[idx['n']]
            idx['n'] += 1
            return t

        hooks = build_default_intake_hooks(config, now_fn=now_fn)
        d1 = validate_intake_stage(
            _make_context(config=config, state=state), hooks=hooks
        )
        d2 = validate_intake_stage(
            _make_context(config=config, state=state), hooks=hooks
        )

        assert d1.allowed is True
        assert d2.allowed is False
        assert d2.reason_code == 'INTAKE_DUPLICATE_ORDER_WINDOW'

    def test_default_hooks_preserve_provided_empty_active_command_ids_set(self) -> None:
        config = InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            allocated_capital=Decimal('10000'),
        )
        active_command_ids: set[str] = set()
        hooks = build_default_intake_hooks(
            config,
            active_command_ids=active_command_ids,
        )

        active_command_ids.add('cmd_shared')
        decision = validate_intake_stage(
            _make_context(
                action=ValidationAction.MODIFY,
                command_id='cmd_shared',
                order_side=None,
            ),
            hooks=hooks,
        )

        assert decision.allowed is True
