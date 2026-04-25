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
from nexus.core.domain.enums import OperationalMode, OrderSide
from nexus.core.domain.operational_mode import ModeState
from nexus.core.domain.instance_state import InstanceState
from nexus.core.domain.position import Position
from nexus.instance_config import InstanceConfig


def _make_context(**overrides: Any) -> ValidationRequestContext:
    config = InstanceConfig(
        account_id='acc_001',
        venue='binance_spot',
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
        'state': InstanceState.fresh(Decimal('10000')),
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

    def test_zero_strategy_budget_allowed_for_modify_builtin_gate(self) -> None:
        decision = validate_intake_stage(
            _make_context(
                action=ValidationAction.MODIFY,
                strategy_budget=Decimal('0'),
                order_side=None,
            )
        )
        assert decision.allowed is True

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
        assert d3.message == 'max_order_rate exceeded: limit=2/s'

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
        assert d2.message == 'duplicate command_id detected within 1000ms window'

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

    def test_duplicate_window_keys_on_command_id_not_order_shape(self) -> None:
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

        d1 = validate_intake_stage(
            _make_context(command_id='cmd_same', order_size=Decimal('1.0')),
            hooks=(dupe_hook,),
        )
        d2 = validate_intake_stage(
            _make_context(command_id='cmd_same', order_size=Decimal('2.0')),
            hooks=(dupe_hook,),
        )

        assert d1.allowed is True
        assert d2.allowed is False
        assert d2.reason_code == 'INTAKE_DUPLICATE_ORDER_WINDOW'

    def test_max_order_rate_recovers_after_window_cutoff(self) -> None:
        times = [
            datetime(2026, 3, 23, 10, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 3, 23, 10, 0, 0, 100000, tzinfo=timezone.utc),
            datetime(2026, 3, 23, 10, 0, 1, 200000, tzinfo=timezone.utc),
        ]
        idx = {'n': 0}

        def now_fn() -> datetime:
            t = times[idx['n']]
            idx['n'] += 1
            return t

        rate_hook = make_order_rate_hook(1, now_fn=now_fn)

        d1 = validate_intake_stage(_make_context(), hooks=(rate_hook,))
        d2 = validate_intake_stage(_make_context(), hooks=(rate_hook,))
        d3 = validate_intake_stage(_make_context(), hooks=(rate_hook,))

        assert d1.allowed is True
        assert d2.allowed is False
        assert d2.reason_code == 'INTAKE_MAX_ORDER_RATE_EXCEEDED'
        assert d3.allowed is True

    def test_duplicate_window_replay_block_expires_after_window(self) -> None:
        times = [
            datetime(2026, 3, 23, 10, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 3, 23, 10, 0, 0, 300000, tzinfo=timezone.utc),
            datetime(2026, 3, 23, 10, 0, 1, 300000, tzinfo=timezone.utc),
        ]
        idx = {'n': 0}

        def now_fn() -> datetime:
            t = times[idx['n']]
            idx['n'] += 1
            return t

        dupe_hook = make_duplicate_order_hook(1000, now_fn=now_fn)

        d1 = validate_intake_stage(
            _make_context(command_id='cmd_replay'),
            hooks=(dupe_hook,),
        )
        d2 = validate_intake_stage(
            _make_context(command_id='cmd_replay'),
            hooks=(dupe_hook,),
        )
        d3 = validate_intake_stage(
            _make_context(command_id='cmd_replay'),
            hooks=(dupe_hook,),
        )

        assert d1.allowed is True
        assert d2.allowed is False
        assert d2.reason_code == 'INTAKE_DUPLICATE_ORDER_WINDOW'
        assert d3.allowed is True

    def test_duplicate_window_ignores_non_enter_without_clock_reads(self) -> None:
        def now_fn() -> datetime:
            msg = 'duplicate hook clock should not be used for non-ENTER actions'
            raise AssertionError(msg)

        dupe_hook = make_duplicate_order_hook(1000, now_fn=now_fn)

        d1 = validate_intake_stage(
            _make_context(
                action=ValidationAction.EXIT,
                order_side=OrderSide.SELL,
                command_id='cmd_same',
            ),
            hooks=(dupe_hook,),
        )
        d2 = validate_intake_stage(
            _make_context(
                action=ValidationAction.EXIT,
                order_side=OrderSide.SELL,
                command_id='cmd_same',
            ),
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
        state = InstanceState.fresh(Decimal('10000'))
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

    def test_reference_integrity_modify_requires_modifiable_set(self) -> None:
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
        assert decision.reason_code == 'INTAKE_MODIFIABLE_COMMANDS_UNAVAILABLE'

    def test_reference_integrity_modify_requires_modifiable_command_when_provided(
        self,
    ) -> None:
        ref_hook = make_reference_integrity_hook(
            active_command_ids={'cmd_ok', 'cmd_finalized'},
            modifiable_command_ids={'cmd_ok'},
        )

        decision = validate_intake_stage(
            _make_context(
                action=ValidationAction.MODIFY,
                command_id='cmd_finalized',
                order_side=None,
            ),
            hooks=(ref_hook,),
        )

        assert decision.allowed is False
        assert decision.reason_code == 'INTAKE_COMMAND_REFERENCE_INVALID'

    def test_reference_integrity_modify_accepts_modifiable_command_when_provided(
        self,
    ) -> None:
        ref_hook = make_reference_integrity_hook(
            active_command_ids={'cmd_ok', 'cmd_finalized'},
            modifiable_command_ids={'cmd_ok'},
        )

        decision = validate_intake_stage(
            _make_context(
                action=ValidationAction.MODIFY,
                command_id='cmd_ok',
                order_side=None,
            ),
            hooks=(ref_hook,),
        )

        assert decision.allowed is True

    def test_reference_integrity_modify_requires_positive_size(self) -> None:
        ref_hook = make_reference_integrity_hook(
            active_command_ids={'cmd_ok'},
            modifiable_command_ids={'cmd_ok'},
        )

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
            duplicate_window_ms=1000,
        )
        state = InstanceState.fresh(Decimal('10000'))

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

    def test_default_hooks_modify_requires_modifiable_command_ids(self) -> None:
        config = InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
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

        assert decision.allowed is False
        assert decision.reason_code == 'INTAKE_MODIFIABLE_COMMANDS_UNAVAILABLE'

    def test_default_hooks_use_provided_modifiable_command_ids(self) -> None:
        config = InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
        )
        active_command_ids = {'cmd_mod', 'cmd_done'}
        modifiable_command_ids: set[str] = set()
        hooks = build_default_intake_hooks(
            config,
            active_command_ids=active_command_ids,
            modifiable_command_ids=modifiable_command_ids,
        )

        modifiable_command_ids.add('cmd_mod')

        denied = validate_intake_stage(
            _make_context(
                action=ValidationAction.MODIFY,
                command_id='cmd_done',
                order_side=None,
            ),
            hooks=hooks,
        )
        allowed = validate_intake_stage(
            _make_context(
                action=ValidationAction.MODIFY,
                command_id='cmd_mod',
                order_side=None,
            ),
            hooks=hooks,
        )

        assert denied.allowed is False
        assert denied.reason_code == 'INTAKE_COMMAND_REFERENCE_INVALID'
        assert allowed.allowed is True

    def test_default_hooks_apply_config_max_order_rate(self) -> None:
        config = InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            max_order_rate=1,
        )

        times = [
            datetime(2026, 3, 23, 10, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 3, 23, 10, 0, 0, 50000, tzinfo=timezone.utc),
            datetime(2026, 3, 23, 10, 0, 0, 100000, tzinfo=timezone.utc),
        ]
        idx = {'n': 0}

        def now_fn() -> datetime:
            t = times[idx['n']]
            idx['n'] += 1
            return t

        hooks = build_default_intake_hooks(config, now_fn=now_fn)

        d1 = validate_intake_stage(_make_context(config=config), hooks=hooks)
        d2 = validate_intake_stage(_make_context(config=config), hooks=hooks)

        assert d1.allowed is True
        assert d2.allowed is False
        assert d2.reason_code == 'INTAKE_MAX_ORDER_RATE_EXCEEDED'

    def test_default_hooks_max_order_rate_override_takes_precedence(self) -> None:
        config = InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            max_order_rate=2,
        )

        times = [
            datetime(2026, 3, 23, 10, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 3, 23, 10, 0, 0, 50000, tzinfo=timezone.utc),
            datetime(2026, 3, 23, 10, 0, 0, 100000, tzinfo=timezone.utc),
        ]
        idx = {'n': 0}

        def now_fn() -> datetime:
            t = times[idx['n']]
            idx['n'] += 1
            return t

        hooks = build_default_intake_hooks(config, max_order_rate=1, now_fn=now_fn)

        d1 = validate_intake_stage(_make_context(config=config), hooks=hooks)
        d2 = validate_intake_stage(_make_context(config=config), hooks=hooks)

        assert d1.allowed is True
        assert d2.allowed is False
        assert d2.reason_code == 'INTAKE_MAX_ORDER_RATE_EXCEEDED'


class TestIntakeOperationalMode:
    '''Mode-state checks (PT-FIX-15).

    `HealthLoop` mutates `state.mode` on health degradation. Validator
    must enforce the documented contract before any submission reaches
    PraxisOutbound: ACTIVE allows everything, REDUCE_ONLY blocks ENTER,
    HALTED blocks ENTER + EXIT.
    '''

    def test_active_mode_allows_enter(self) -> None:

        state = InstanceState.fresh(Decimal('10000'))
        state.mode = ModeState(mode=OperationalMode.ACTIVE)

        decision = validate_intake_stage(
            _make_context(state=state, action=ValidationAction.ENTER),
        )

        assert decision.allowed is True

    def test_active_mode_allows_exit(self) -> None:

        state = InstanceState.fresh(Decimal('10000'))
        state.mode = ModeState(mode=OperationalMode.ACTIVE)

        decision = validate_intake_stage(
            _make_context(
                state=state,
                action=ValidationAction.EXIT,
                trade_id='trade_x',
            ),
        )

        assert decision.allowed is True

    def test_reduce_only_blocks_enter(self) -> None:

        state = InstanceState.fresh(Decimal('10000'))
        state.mode = ModeState(mode=OperationalMode.REDUCE_ONLY, trigger='health')

        decision = validate_intake_stage(
            _make_context(state=state, action=ValidationAction.ENTER),
        )

        assert decision.allowed is False
        assert decision.failed_stage == ValidationStage.INTAKE
        assert decision.reason_code == 'INTAKE_MODE_BLOCKS_ENTER'
        assert 'REDUCE_ONLY' in (decision.message or '')

    def test_reduce_only_allows_exit(self) -> None:

        state = InstanceState.fresh(Decimal('10000'))
        state.mode = ModeState(mode=OperationalMode.REDUCE_ONLY, trigger='health')

        decision = validate_intake_stage(
            _make_context(
                state=state,
                action=ValidationAction.EXIT,
                trade_id='trade_x',
            ),
        )

        assert decision.allowed is True

    def test_halted_blocks_enter(self) -> None:

        state = InstanceState.fresh(Decimal('10000'))
        state.mode = ModeState(mode=OperationalMode.HALTED, trigger='health')

        decision = validate_intake_stage(
            _make_context(state=state, action=ValidationAction.ENTER),
        )

        assert decision.allowed is False
        assert decision.reason_code == 'INTAKE_MODE_BLOCKS_ENTER'
        assert 'HALTED' in (decision.message or '')

    def test_halted_blocks_exit(self) -> None:

        state = InstanceState.fresh(Decimal('10000'))
        state.mode = ModeState(mode=OperationalMode.HALTED, trigger='health')

        decision = validate_intake_stage(
            _make_context(
                state=state,
                action=ValidationAction.EXIT,
                trade_id='trade_x',
            ),
        )

        assert decision.allowed is False
        assert decision.reason_code == 'INTAKE_MODE_HALTED_BLOCKS_EXIT'

    def test_halted_still_allows_cancel(self) -> None:

        state = InstanceState.fresh(Decimal('10000'))
        state.mode = ModeState(mode=OperationalMode.HALTED, trigger='health')

        decision = validate_intake_stage(
            _make_context(
                state=state,
                action=ValidationAction.CANCEL,
                command_id='cmd_to_cancel',
            ),
        )

        assert decision.allowed is True

    def test_halted_still_allows_abort(self) -> None:

        state = InstanceState.fresh(Decimal('10000'))
        state.mode = ModeState(mode=OperationalMode.HALTED, trigger='health')

        decision = validate_intake_stage(
            _make_context(
                state=state,
                action=ValidationAction.ABORT,
                command_id='cmd_to_abort',
            ),
        )

        assert decision.allowed is True
