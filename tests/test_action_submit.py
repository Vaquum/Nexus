'''Tests for nexus/strategy/action_submit.py.'''

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nexus.core.capital_controller.capital_controller import CapitalController
from nexus.core.capital_controller.reservation import Reservation
from nexus.core.domain.capital_state import CapitalState
from nexus.core.domain.enums import OrderSide
from nexus.core.domain.instance_state import InstanceState
from nexus.core.domain.position import Position
from nexus.core.domain.order_types import ExecutionMode, MakerPreference, OrderType
from nexus.core.stp_mode import STPMode
from nexus.core.validator.pipeline_models import (
    ValidationAction,
    ValidationDecision,
    ValidationRequestContext,
    ValidationStage,
)
from nexus.infrastructure.praxis_connector.order_context import OrderContext
from nexus.infrastructure.praxis_connector.outcome_processor import OutcomeProcessor
from nexus.infrastructure.praxis_connector.trade_outcome import TradeOutcome
from nexus.infrastructure.praxis_connector.trade_outcome_type import TradeOutcomeType
from nexus.infrastructure.state_store import StateStore
from nexus.instance_config import InstanceConfig
from nexus.strategy.action import Action, ActionType
from nexus.strategy.action_submit import (
    SubmissionOutcome,
    SubmissionStatus,
    bridge_to_capital,
    submit_actions,
)


_NOW = datetime(2026, 4, 22, 12, 0, 0, tzinfo=timezone.utc)


def _now() -> datetime:
    return _NOW


def _config() -> InstanceConfig:
    return InstanceConfig(
        account_id='acc_001',
        venue='binance_spot',
        stp_mode=STPMode.CANCEL_TAKER,
    )


def _state() -> InstanceState:
    return InstanceState.fresh(Decimal('10000'))


def _enter_action() -> Action:
    return Action(
        action_type=ActionType.ENTER,
        direction=OrderSide.BUY,
        size=Decimal('0.01'),
        execution_mode=ExecutionMode.SINGLE_SHOT,
        order_type=OrderType.MARKET,
        deadline=300,
        maker_preference=MakerPreference.NO_PREFERENCE,
        reference_price=Decimal('100000'),
    )


def _abort_action(command_id: str = 'cmd_777') -> Action:
    return Action(action_type=ActionType.ABORT, command_id=command_id)


def _enter_context(strategy_id: str = 'strat_001') -> ValidationRequestContext:
    return ValidationRequestContext(
        strategy_id=strategy_id,
        order_notional=Decimal('1000'),
        estimated_fees=Decimal('1'),
        strategy_budget=Decimal('5000'),
        state=_state(),
        config=_config(),
        action=ValidationAction.ENTER,
        symbol='BTCUSDT',
        order_side=OrderSide.BUY,
        order_size=Decimal('0.01'),
    )


def _allow_decision() -> ValidationDecision:
    res = Reservation(
        reservation_id='res_001',
        strategy_id='strat_001',
        notional=Decimal('1000'),
        estimated_fees=Decimal('1'),
        created_at=_NOW,
        expires_at=_NOW + timedelta(minutes=1),
    )
    return ValidationDecision(allowed=True, reservation=res)


def _deny_decision() -> ValidationDecision:
    return ValidationDecision(
        allowed=False,
        failed_stage=ValidationStage.CAPITAL,
        reason_code='INSUFFICIENT_BUDGET',
        message='not enough capital',
    )


class TestSubmitActions:

    def test_allow_path_submits(self) -> None:
        '''An allowed action goes through validate → translate → send_command.'''

        ctx = _enter_context()
        decision = _allow_decision()

        validator = MagicMock()
        validator.validate.return_value = decision
        outbound = MagicMock()
        outbound.send_command.return_value = 'cmd_201'

        results = submit_actions(
            [_enter_action()],
            strategy_id='strat_001',
            config=_config(),
            praxis_outbound=outbound,
            validator=validator,
            build_context=lambda _a, _s: ctx,
            now=_now,
        )

        assert len(results) == 1
        _action, outcome = results[0]
        assert outcome.status == SubmissionStatus.SUBMITTED
        assert outcome.command_id == 'cmd_201'
        assert outcome.decision is decision
        validator.validate.assert_called_once_with(ctx)
        outbound.send_command.assert_called_once()
        assert outbound.send_abort.call_count == 0

    def test_reject_path_does_not_submit(self) -> None:
        '''A denied decision short-circuits before send_command.'''

        validator = MagicMock()
        validator.validate.return_value = _deny_decision()
        outbound = MagicMock()

        results = submit_actions(
            [_enter_action()],
            strategy_id='strat_001',
            config=_config(),
            praxis_outbound=outbound,
            validator=validator,
            build_context=lambda _a, _s: _enter_context(),
            now=_now,
        )

        _action, outcome = results[0]
        assert outcome.status == SubmissionStatus.REJECTED
        assert outcome.command_id is None
        assert outcome.decision is not None
        assert outcome.decision.reason_code == 'INSUFFICIENT_BUDGET'
        assert outbound.send_command.call_count == 0
        assert outbound.send_abort.call_count == 0

    def test_send_command_failure_is_per_action(self) -> None:
        '''send_command raising surfaces as SUBMIT_FAILED but does not abort the loop.'''

        ctx = _enter_context()
        validator = MagicMock()
        validator.validate.return_value = _allow_decision()
        outbound = MagicMock()
        outbound.send_command.side_effect = [
            TimeoutError('praxis took too long'),
            'cmd_202',
        ]

        results = submit_actions(
            [_enter_action(), _enter_action()],
            strategy_id='strat_001',
            config=_config(),
            praxis_outbound=outbound,
            validator=validator,
            build_context=lambda _a, _s: ctx,
            now=_now,
        )

        assert len(results) == 2
        assert results[0][1].status == SubmissionStatus.SUBMIT_FAILED
        assert 'praxis took too long' in (results[0][1].error or '')
        assert results[1][1].status == SubmissionStatus.SUBMITTED
        assert results[1][1].command_id == 'cmd_202'

    def test_validator_exception_is_per_action(self) -> None:
        '''A validator raising must not abort the tick.'''

        validator = MagicMock()
        validator.validate.side_effect = [
            RuntimeError('boom'),
            _allow_decision(),
        ]
        outbound = MagicMock()
        outbound.send_command.return_value = 'cmd_203'

        results = submit_actions(
            [_enter_action(), _enter_action()],
            strategy_id='strat_001',
            config=_config(),
            praxis_outbound=outbound,
            validator=validator,
            build_context=lambda _a, _s: _enter_context(),
            now=_now,
        )

        assert results[0][1].status == SubmissionStatus.SUBMIT_FAILED
        assert 'validator: boom' in (results[0][1].error or '')
        assert results[1][1].status == SubmissionStatus.SUBMITTED

    def test_context_unavailable_skips_action(self) -> None:
        '''build_context returning None marks the action INVALID and does not call validator.'''

        validator = MagicMock()
        outbound = MagicMock()

        results = submit_actions(
            [_enter_action()],
            strategy_id='strat_001',
            config=_config(),
            praxis_outbound=outbound,
            validator=validator,
            build_context=lambda _a, _s: None,
            now=_now,
        )

        _action, outcome = results[0]
        assert outcome.status == SubmissionStatus.INVALID
        assert outcome.error == 'context unavailable'
        assert validator.validate.call_count == 0
        assert outbound.send_command.call_count == 0

    def test_translate_exception_is_isolated(self) -> None:
        '''translate raising yields SUBMIT_FAILED; iteration continues for remaining actions.'''

        validator = MagicMock()
        validator.validate.return_value = _allow_decision()
        outbound = MagicMock()
        outbound.send_command.return_value = 'cmd_303'

        sentinel_cmd = MagicMock(name='TradeCommand')

        with patch(
            'nexus.strategy.action_submit.translate_to_trade_command',
            side_effect=[ValueError('synthetic translate failure'), sentinel_cmd],
        ):
            results = submit_actions(
                [_enter_action(), _enter_action()],
                strategy_id='strat_001',
                config=_config(),
                praxis_outbound=outbound,
                validator=validator,
                build_context=lambda _a, _s: _enter_context(),
                now=_now,
            )

        assert results[0][1].status == SubmissionStatus.SUBMIT_FAILED
        assert 'translate' in (results[0][1].error or '')
        assert results[1][1].status == SubmissionStatus.SUBMITTED
        outbound.send_command.assert_called_once_with(sentinel_cmd)

    def test_build_context_exception_is_isolated(self) -> None:
        '''build_context raising yields SUBMIT_FAILED; iteration continues for remaining actions.'''

        validator = MagicMock()
        validator.validate.return_value = _allow_decision()
        outbound = MagicMock()
        outbound.send_command.return_value = 'cmd_301'

        calls = {'n': 0}

        def flaky_build_context(_action: Action, _sid: str) -> ValidationRequestContext:
            calls['n'] += 1
            if calls['n'] == 1:
                raise KeyError('position lookup race')
            return _enter_context()

        results = submit_actions(
            [_enter_action(), _enter_action()],
            strategy_id='strat_001',
            config=_config(),
            praxis_outbound=outbound,
            validator=validator,
            build_context=flaky_build_context,
            now=_now,
        )

        assert results[0][1].status == SubmissionStatus.SUBMIT_FAILED
        assert 'build_context' in (results[0][1].error or '')
        assert results[1][1].status == SubmissionStatus.SUBMITTED

    def test_abort_bypasses_validator(self) -> None:
        '''ABORT goes directly to PraxisOutbound.send_abort without touching the validator.'''

        validator = MagicMock()
        outbound = MagicMock()

        results = submit_actions(
            [_abort_action('cmd_555')],
            strategy_id='strat_001',
            config=_config(),
            praxis_outbound=outbound,
            validator=validator,
            build_context=lambda _a, _s: pytest.fail('build_context should not be called for ABORT'),
            now=_now,
        )

        _action, outcome = results[0]
        assert outcome.status == SubmissionStatus.SUBMITTED
        assert outcome.command_id == 'cmd_555'
        assert validator.validate.call_count == 0
        outbound.send_abort.assert_called_once()
        kwargs = outbound.send_abort.call_args.kwargs
        assert kwargs['command_id'] == 'cmd_555'
        assert kwargs['account_id'] == 'acc_001'
        assert kwargs['reason'] == 'runtime_strategy_abort'
        assert kwargs['created_at'] == _NOW

    def test_abort_without_command_id_is_invalid(self) -> None:
        '''ABORT must carry command_id; without it, marked INVALID and not submitted.'''

        action = Action(action_type=ActionType.ABORT, command_id='cmd_x')
        # Force command_id to None via object.__setattr__ to bypass __post_init__
        object.__setattr__(action, 'command_id', None)

        validator = MagicMock()
        outbound = MagicMock()

        results = submit_actions(
            [action],
            strategy_id='strat_001',
            config=_config(),
            praxis_outbound=outbound,
            validator=validator,
            build_context=lambda _a, _s: None,
            now=_now,
        )

        outcome = results[0][1]
        assert outcome.status == SubmissionStatus.INVALID
        assert 'command_id' in (outcome.error or '')
        assert outbound.send_abort.call_count == 0

    def test_send_abort_failure_marks_submit_failed(self) -> None:
        '''send_abort raising propagates as SUBMIT_FAILED, not INVALID.'''

        validator = MagicMock()
        outbound = MagicMock()
        outbound.send_abort.side_effect = TimeoutError('praxis hung')

        results = submit_actions(
            [_abort_action('cmd_900')],
            strategy_id='strat_001',
            config=_config(),
            praxis_outbound=outbound,
            validator=validator,
            build_context=lambda _a, _s: None,
            now=_now,
        )

        outcome = results[0][1]
        assert outcome.status == SubmissionStatus.SUBMIT_FAILED
        assert 'praxis hung' in (outcome.error or '')

    def test_empty_actions_list_returns_empty(self) -> None:
        '''An empty action list yields an empty results list and touches nothing.'''

        validator = MagicMock()
        outbound = MagicMock()

        results = submit_actions(
            [],
            strategy_id='strat_001',
            config=_config(),
            praxis_outbound=outbound,
            validator=validator,
            build_context=lambda _a, _s: None,
            now=_now,
        )

        assert results == []
        assert validator.validate.call_count == 0
        assert outbound.send_command.call_count == 0
        assert outbound.send_abort.call_count == 0

    def test_results_preserve_input_order(self) -> None:
        '''Results are returned in the same order as input actions.'''

        ctx = _enter_context()
        validator = MagicMock()
        validator.validate.return_value = _allow_decision()
        outbound = MagicMock()
        outbound.send_command.side_effect = ['cmd_a', 'cmd_b', 'cmd_c']

        actions = [_enter_action(), _enter_action(), _enter_action()]
        results = submit_actions(
            actions,
            strategy_id='strat_001',
            config=_config(),
            praxis_outbound=outbound,
            validator=validator,
            build_context=lambda _a, _s: ctx,
            now=_now,
        )

        assert [r[1].command_id for r in results] == ['cmd_a', 'cmd_b', 'cmd_c']
        assert [r[0] for r in results] == actions


def _exit_action(trade_id: str = 't1', size: Decimal = Decimal('0.5')) -> Action:
    return Action(
        action_type=ActionType.EXIT,
        trade_id=trade_id,
        size=size,
        execution_mode=ExecutionMode.SINGLE_SHOT,
        order_type=OrderType.MARKET,
        deadline=300,
    )


def _state_with_position(
    trade_id: str = 't1',
    size: Decimal = Decimal('1.0'),
    pending_exit: Decimal = Decimal('0'),
) -> InstanceState:
    state = InstanceState.fresh(Decimal('10000'))
    state.positions[trade_id] = Position(
        trade_id=trade_id,
        strategy_id='strat_001',
        symbol='BTCUSDT',
        side=OrderSide.BUY,
        size=size,
        entry_price=Decimal('50000'),
        pending_exit=pending_exit,
    )
    return state


def _exit_context(
    state: InstanceState,
    trade_id: str = 't1',
    order_size: Decimal = Decimal('0.5'),
) -> ValidationRequestContext:
    return ValidationRequestContext(
        strategy_id='strat_001',
        order_notional=Decimal('25000'),
        estimated_fees=Decimal('25'),
        strategy_budget=Decimal('5000'),
        state=state,
        config=_config(),
        action=ValidationAction.EXIT,
        symbol='BTCUSDT',
        order_side=OrderSide.SELL,
        order_size=order_size,
        trade_id=trade_id,
    )


class TestPendingExitIncrement:
    '''`Position.pending_exit` increments when an EXIT action is
    SUBMITTED so the validator's `INTAKE_EXIT_SIZE_EXCEEDS_REMAINING`
    defense fires on the next overlapping EXIT. Increment lives in
    `submit_actions` after `send_command` succeeds; the existing
    decrement in `OutcomeProcessor._reduce_position` /
    `_clear_pending_exit` completes the round-trip.
    '''

    def test_exit_submission_increments_pending_exit(self) -> None:
        state = _state_with_position()
        ctx = _exit_context(state)
        validator = MagicMock()
        validator.validate.return_value = ValidationDecision(allowed=True)
        outbound = MagicMock()
        outbound.send_command.return_value = 'cmd_x1'

        submit_actions(
            [_exit_action()],
            strategy_id='strat_001',
            config=_config(),
            praxis_outbound=outbound,
            validator=validator,
            build_context=lambda _a, _s: ctx,
            now=_now,
        )

        assert state.positions['t1'].pending_exit == Decimal('0.5')

    def test_pending_exit_tracks_ctx_order_size_not_action_size(self) -> None:
        '''`pending_exit` must track `ctx.order_size` (what was actually
        submitted via `translate_to_trade_command(... size=context.order_size ...)`)
        rather than `action.size`. If `build_context` rounds / clamps
        `order_size` away from `action.size`, the validator's
        `INTAKE_EXIT_SIZE_EXCEEDS_REMAINING` defense becomes unreliable
        when it consults `position.pending_exit` against the next
        EXIT's submitted size — they must match.
        '''

        state = _state_with_position()
        ctx = _exit_context(state, order_size=Decimal('0.4'))
        validator = MagicMock()
        validator.validate.return_value = ValidationDecision(allowed=True)
        outbound = MagicMock()
        outbound.send_command.return_value = 'cmd_x1'

        submit_actions(
            [_exit_action(size=Decimal('0.5'))],
            strategy_id='strat_001',
            config=_config(),
            praxis_outbound=outbound,
            validator=validator,
            build_context=lambda _a, _s: ctx,
            now=_now,
        )

        assert state.positions['t1'].pending_exit == Decimal('0.4')

    def test_exit_rejected_by_validator_does_not_increment(self) -> None:
        state = _state_with_position()
        ctx = _exit_context(state)
        validator = MagicMock()
        validator.validate.return_value = _deny_decision()
        outbound = MagicMock()

        submit_actions(
            [_exit_action()],
            strategy_id='strat_001',
            config=_config(),
            praxis_outbound=outbound,
            validator=validator,
            build_context=lambda _a, _s: ctx,
            now=_now,
        )

        assert state.positions['t1'].pending_exit == Decimal('0')
        outbound.send_command.assert_not_called()

    def test_exit_send_command_failure_does_not_increment(self) -> None:
        state = _state_with_position()
        ctx = _exit_context(state)
        validator = MagicMock()
        validator.validate.return_value = ValidationDecision(allowed=True)
        outbound = MagicMock()
        outbound.send_command.side_effect = RuntimeError('venue unreachable')

        submit_actions(
            [_exit_action()],
            strategy_id='strat_001',
            config=_config(),
            praxis_outbound=outbound,
            validator=validator,
            build_context=lambda _a, _s: ctx,
            now=_now,
        )

        assert state.positions['t1'].pending_exit == Decimal('0')

    def test_enter_action_does_not_touch_pending_exit(self) -> None:
        state = _state_with_position(pending_exit=Decimal('0.3'))
        ctx = _enter_context()
        validator = MagicMock()
        validator.validate.return_value = _allow_decision()
        outbound = MagicMock()
        outbound.send_command.return_value = 'cmd_e1'

        submit_actions(
            [_enter_action()],
            strategy_id='strat_001',
            config=_config(),
            praxis_outbound=outbound,
            validator=validator,
            build_context=lambda _a, _s: ctx,
            now=_now,
        )

        assert state.positions['t1'].pending_exit == Decimal('0.3')

    def test_exit_for_unknown_trade_id_does_not_raise(self) -> None:
        '''Defense in depth: if context.state.positions lacks the trade_id
        (race with a concurrent close on a different thread), the
        increment is a no-op rather than a KeyError.'''

        state = _state_with_position()
        del state.positions['t1']
        ctx = _exit_context(state)
        validator = MagicMock()
        validator.validate.return_value = ValidationDecision(allowed=True)
        outbound = MagicMock()
        outbound.send_command.return_value = 'cmd_x1'

        results = submit_actions(
            [_exit_action()],
            strategy_id='strat_001',
            config=_config(),
            praxis_outbound=outbound,
            validator=validator,
            build_context=lambda _a, _s: ctx,
            now=_now,
        )

        assert results[0][1].status == SubmissionStatus.SUBMITTED

    def test_overlapping_exit_denied_by_intake_after_first_increments(self) -> None:
        '''Cross-tick defense: tick 1 submits an EXIT for the full
        position size; tick 2 submits a second EXIT for the same
        trade_id — the validator's intake stage sees `pending_exit > 0`
        and denies with `INTAKE_EXIT_SIZE_EXCEEDS_REMAINING`.'''

        from nexus.core.validator import (
            make_reference_integrity_hook,
            validate_intake_stage,
        )

        state = _state_with_position(size=Decimal('1.0'))

        ctx1 = _exit_context(state, order_size=Decimal('1.0'))
        validator = MagicMock()
        validator.validate.return_value = ValidationDecision(allowed=True)
        outbound = MagicMock()
        outbound.send_command.return_value = 'cmd_x1'

        submit_actions(
            [_exit_action(size=Decimal('1.0'))],
            strategy_id='strat_001',
            config=_config(),
            praxis_outbound=outbound,
            validator=validator,
            build_context=lambda _a, _s: ctx1,
            now=_now,
        )
        assert state.positions['t1'].pending_exit == Decimal('1.0')

        ctx2 = ValidationRequestContext(
            strategy_id='strat_001',
            order_notional=Decimal('25000'),
            estimated_fees=Decimal('25'),
            strategy_budget=Decimal('5000'),
            state=state,
            config=_config(),
            action=ValidationAction.EXIT,
            symbol='BTCUSDT',
            order_side=OrderSide.SELL,
            order_size=Decimal('1.0'),
            trade_id='t1',
            command_id='cmd_x2',
        )
        ref_hook = make_reference_integrity_hook(active_command_ids=set())
        decision = validate_intake_stage(ctx2, hooks=(ref_hook,))

        assert decision.allowed is False
        assert decision.reason_code == 'INTAKE_EXIT_SIZE_EXCEEDS_REMAINING'


class TestBridgeToCapital:
    '''PT-FIX-27: `bridge_to_capital` converts a SUBMITTED reservation
    into a tracked IN_FLIGHT order via `CapitalController.send_order`.
    Without this call every later ACK / FILL / REJECT / CANCEL fails
    with `order not found` because `OutcomeProcessor.process` looks up
    `self._capital._orders[outcome.command_id]` — only `send_order`
    populates that dict.'''

    def _build_capital(self) -> tuple[CapitalController, Reservation, SubmissionOutcome]:
        controller = CapitalController(CapitalState(capital_pool=Decimal('10000')))
        reservation_result = controller.check_and_reserve(
            strategy_id='strat_001',
            order_notional=Decimal('100'),
            estimated_fees=Decimal('1'),
            strategy_budget=Decimal('5000'),
        )
        assert reservation_result.reservation is not None
        outcome = SubmissionOutcome(
            status=SubmissionStatus.SUBMITTED,
            command_id='cmd_xyz',
            decision=ValidationDecision(
                allowed=True,
                reservation=reservation_result.reservation,
            ),
        )
        return controller, reservation_result.reservation, outcome

    def test_bridge_calls_send_order_for_submitted_outcome(self) -> None:
        controller, reservation, outcome = self._build_capital()

        result = bridge_to_capital(controller, outcome)

        assert result is not None
        assert result.success is True
        assert reservation.reservation_id not in controller._reservations
        assert 'cmd_xyz' in controller._orders

    def test_bridge_round_trip_via_outcome_processor(self) -> None:
        '''Full round trip without launcher-side `send_order`. Drives an
        ACK through `OutcomeProcessor.process` and asserts capital state
        reflects the in-flight → working transition. Pre-fix the helper
        did not exist; without launcher wiring, the ACK would fail with
        `INVARIANT_BREACH: order not found`.'''

        capital_state = CapitalState(capital_pool=Decimal('10000'))
        instance_state = InstanceState(capital=capital_state)
        controller = CapitalController(capital_state)

        reservation_result = controller.check_and_reserve(
            strategy_id='strat_001',
            order_notional=Decimal('100'),
            estimated_fees=Decimal('1'),
            strategy_budget=Decimal('5000'),
        )
        assert reservation_result.reservation is not None
        outcome = SubmissionOutcome(
            status=SubmissionStatus.SUBMITTED,
            command_id='cmd_round_trip',
            decision=ValidationDecision(
                allowed=True,
                reservation=reservation_result.reservation,
            ),
        )

        bridge_to_capital(controller, outcome)

        with tempfile.TemporaryDirectory() as tmp:
            processor = OutcomeProcessor(
                controller, instance_state, StateStore(Path(tmp)),
            )
            ack_outcome = TradeOutcome(
                outcome_id='out_001',
                command_id='cmd_round_trip',
                outcome_type=TradeOutcomeType.ACK,
                timestamp=_NOW,
            )
            ctx = OrderContext(
                command_id='cmd_round_trip',
                strategy_id='strat_001',
                trade_id='trade_001',
                side=OrderSide.BUY,
                order_size=Decimal('0.01'),
                order_notional=Decimal('100'),
                estimated_fees=Decimal('1'),
                is_entry=True,
            )

            result = processor.process(ack_outcome, ctx)

        assert result.success is True
        assert result.error_reason is None

    def test_bridge_no_op_for_rejected_outcome(self) -> None:
        controller = CapitalController(CapitalState(capital_pool=Decimal('10000')))
        outcome = SubmissionOutcome(
            status=SubmissionStatus.REJECTED,
            decision=_deny_decision(),
        )

        assert bridge_to_capital(controller, outcome) is None
        assert controller._orders == {}

    def test_bridge_no_op_when_decision_missing_reservation(self) -> None:
        controller = CapitalController(CapitalState(capital_pool=Decimal('10000')))
        outcome = SubmissionOutcome(
            status=SubmissionStatus.SUBMITTED,
            command_id='cmd_no_res',
            decision=ValidationDecision(allowed=True),
        )

        assert bridge_to_capital(controller, outcome) is None
        assert controller._orders == {}


class TestReleaseReservationOnSubmitFailure:
    '''MAJOR-G: granted Reservation must be released when downstream
    translate / send_command raises. Pre-fix the reservation parked in
    `_reservations` until 30s TTL eviction; deterministic failures
    (config bug, malformed symbol) re-granted on the next tick and
    parked again, eventually starving capital.
    '''

    def test_send_command_failure_releases_reservation(self) -> None:
        controller = CapitalController(CapitalState(capital_pool=Decimal('10000')))
        res = controller.check_and_reserve(
            strategy_id='strat_001',
            order_notional=Decimal('100'),
            estimated_fees=Decimal('1'),
            strategy_budget=Decimal('5000'),
        )
        assert res.reservation is not None
        ctx = _enter_context()
        validator = MagicMock()
        validator.validate.return_value = ValidationDecision(
            allowed=True, reservation=res.reservation,
        )
        outbound = MagicMock()
        outbound.send_command.side_effect = RuntimeError('venue unreachable')

        results = submit_actions(
            [_enter_action()],
            strategy_id='strat_001',
            config=_config(),
            praxis_outbound=outbound,
            validator=validator,
            build_context=lambda _a, _s: ctx,
            now=_now,
            capital_controller=controller,
        )

        assert results[0][1].status == SubmissionStatus.SUBMIT_FAILED
        assert controller._state.reservation_notional == Decimal('0')
        assert res.reservation.reservation_id not in controller._reservations

    def test_translate_failure_releases_reservation(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        controller = CapitalController(CapitalState(capital_pool=Decimal('10000')))
        res = controller.check_and_reserve(
            strategy_id='strat_001',
            order_notional=Decimal('100'),
            estimated_fees=Decimal('1'),
            strategy_budget=Decimal('5000'),
        )
        assert res.reservation is not None
        ctx = _enter_context()
        validator = MagicMock()
        validator.validate.return_value = ValidationDecision(
            allowed=True, reservation=res.reservation,
        )
        outbound = MagicMock()

        def _raise(*_args: object, **_kwargs: object) -> None:
            msg = 'translate exploded'
            raise RuntimeError(msg)

        monkeypatch.setattr(
            'nexus.strategy.action_submit.translate_to_trade_command', _raise,
        )

        results = submit_actions(
            [_enter_action()],
            strategy_id='strat_001',
            config=_config(),
            praxis_outbound=outbound,
            validator=validator,
            build_context=lambda _a, _s: ctx,
            now=_now,
            capital_controller=controller,
        )

        assert results[0][1].status == SubmissionStatus.SUBMIT_FAILED
        assert controller._state.reservation_notional == Decimal('0')
        assert res.reservation.reservation_id not in controller._reservations
        outbound.send_command.assert_not_called()

    def test_chronic_failure_storm_does_not_grow_reservations(self) -> None:
        '''100 ticks each granting + failing must leave _reservations
        empty (each rollback releases) rather than accumulating until
        check_and_reserve hits the per-trade allocation limit.
        '''

        controller = CapitalController(CapitalState(capital_pool=Decimal('10000')))
        ctx = _enter_context()
        validator = MagicMock()
        outbound = MagicMock()
        outbound.send_command.side_effect = RuntimeError('venue down')

        for _ in range(100):
            res = controller.check_and_reserve(
                strategy_id='strat_001',
                order_notional=Decimal('100'),
                estimated_fees=Decimal('1'),
                strategy_budget=Decimal('5000'),
            )
            assert res.reservation is not None
            validator.validate.return_value = ValidationDecision(
                allowed=True, reservation=res.reservation,
            )
            submit_actions(
                [_enter_action()],
                strategy_id='strat_001',
                config=_config(),
                praxis_outbound=outbound,
                validator=validator,
                build_context=lambda _a, _s: ctx,
                now=_now,
                capital_controller=controller,
            )

        assert len(controller._reservations) == 0
        assert controller._state.reservation_notional == Decimal('0')

    def test_no_capital_controller_legacy_path_unchanged(self) -> None:
        '''When capital_controller is omitted (test path), the rollback
        is skipped and the reservation is left for TTL eviction —
        preserves backward compatibility for tests that pass a Mock
        validator without a real controller.
        '''

        ctx = _enter_context()
        validator = MagicMock()
        validator.validate.return_value = _allow_decision()
        outbound = MagicMock()
        outbound.send_command.side_effect = RuntimeError('venue down')

        results = submit_actions(
            [_enter_action()],
            strategy_id='strat_001',
            config=_config(),
            praxis_outbound=outbound,
            validator=validator,
            build_context=lambda _a, _s: ctx,
            now=_now,
        )

        assert results[0][1].status == SubmissionStatus.SUBMIT_FAILED


class TestReleaseReservationOnLateValidatorDeny:
    '''Round-18 MAJOR-006: validator stage order is INTAKE -> RISK ->
    PRICE -> CAPITAL -> HEALTH -> PLATFORM_LIMITS. CAPITAL is fourth,
    not last. When CAPITAL grants but HEALTH or PLATFORM_LIMITS denies,
    Pipeline.validate attaches the granted reservation to the denied
    decision so the caller can release. Pre-fix submit_actions REJECTED
    branch returned without calling _release_granted_reservation,
    leaving the reservation parked in _reservations until TTL eviction.
    Repeated late-stage denies (e.g., spread limit on degraded venue)
    starved available capital.
    '''

    def test_health_deny_after_capital_grant_releases_reservation(
        self,
    ) -> None:
        controller = CapitalController(CapitalState(capital_pool=Decimal('10000')))
        res = controller.check_and_reserve(
            strategy_id='strat_001',
            order_notional=Decimal('100'),
            estimated_fees=Decimal('1'),
            strategy_budget=Decimal('5000'),
        )
        assert res.reservation is not None
        assert controller._state.reservation_notional == Decimal('101')
        denial = ValidationDecision(
            allowed=False,
            failed_stage=ValidationStage.HEALTH,
            reason_code='HEALTH_RATE_LIMIT_HEADROOM',
            message='venue rate-limit headroom below threshold',
            reservation=res.reservation,
        )
        validator = MagicMock()
        validator.validate.return_value = denial
        outbound = MagicMock()

        results = submit_actions(
            [_enter_action()],
            strategy_id='strat_001',
            config=_config(),
            praxis_outbound=outbound,
            validator=validator,
            build_context=lambda _a, _s: _enter_context(),
            now=_now,
            capital_controller=controller,
        )

        assert results[0][1].status == SubmissionStatus.REJECTED
        assert results[0][1].decision is denial
        assert controller._state.reservation_notional == Decimal('0')
        assert res.reservation.reservation_id not in controller._reservations
        outbound.send_command.assert_not_called()

    def test_platform_limits_deny_after_capital_grant_releases(
        self,
    ) -> None:
        controller = CapitalController(CapitalState(capital_pool=Decimal('10000')))
        res = controller.check_and_reserve(
            strategy_id='strat_001',
            order_notional=Decimal('100'),
            estimated_fees=Decimal('1'),
            strategy_budget=Decimal('5000'),
        )
        assert res.reservation is not None
        denial = ValidationDecision(
            allowed=False,
            failed_stage=ValidationStage.PLATFORM_LIMITS,
            reason_code='PLATFORM_LIMITS_MAX_ORDER_NOTIONAL_LIMIT',
            message='order_notional exceeded operator cap',
            reservation=res.reservation,
        )
        validator = MagicMock()
        validator.validate.return_value = denial
        outbound = MagicMock()

        submit_actions(
            [_enter_action()],
            strategy_id='strat_001',
            config=_config(),
            praxis_outbound=outbound,
            validator=validator,
            build_context=lambda _a, _s: _enter_context(),
            now=_now,
            capital_controller=controller,
        )

        assert controller._state.reservation_notional == Decimal('0')
        assert res.reservation.reservation_id not in controller._reservations

    def test_pre_capital_deny_is_safe_when_no_reservation_attached(
        self,
    ) -> None:
        '''Pre-CAPITAL stages (INTAKE, RISK, PRICE) deny without a
        reservation. _release_granted_reservation must short-circuit
        on `decision.reservation is None` so the rejected-branch call
        is a no-op rather than touching the controller. Asserted by
        spying on the controller via MagicMock in place of the real
        one — `release_reservation` must not be invoked.
        '''

        controller = MagicMock(spec=CapitalController)
        denial = ValidationDecision(
            allowed=False,
            failed_stage=ValidationStage.INTAKE,
            reason_code='INTAKE_MODE_BLOCKS_ENTER',
            message='operational mode HALTED blocks new entries',
        )
        validator = MagicMock()
        validator.validate.return_value = denial
        outbound = MagicMock()

        submit_actions(
            [_enter_action()],
            strategy_id='strat_001',
            config=_config(),
            praxis_outbound=outbound,
            validator=validator,
            build_context=lambda _a, _s: _enter_context(),
            now=_now,
            capital_controller=controller,
        )

        controller.release_reservation.assert_not_called()
        outbound.send_command.assert_not_called()

    def test_abort_path_does_not_invoke_release(self) -> None:
        '''ABORT bypasses the validator entirely (validator.validate is
        not called for ABORT actions) so the late-deny code path is
        unreachable for ABORT. Confirm the controller is never touched.
        '''

        controller = MagicMock(spec=CapitalController)
        validator = MagicMock()
        outbound = MagicMock()

        submit_actions(
            [_abort_action()],
            strategy_id='strat_001',
            config=_config(),
            praxis_outbound=outbound,
            validator=validator,
            build_context=lambda _a, _s: _enter_context(),
            now=_now,
            capital_controller=controller,
        )

        validator.validate.assert_not_called()
        controller.release_reservation.assert_not_called()
        outbound.send_command.assert_not_called()
        outbound.send_abort.assert_called_once()


class TestFinalMajor03PendingExitLockCoverage:
    '''FINAL-MAJOR-03: the EXIT `position.pending_exit += ctx.order_size`
    write at submit_actions:~250 was unprotected. A concurrent
    OutcomeProcessor decrement (`_reduce_position` /
    `_clear_pending_exit` on the OutcomeLoop thread) racing the
    increment would lose the update via torn read-modify-write,
    undermining the validator's `INTAKE_EXIT_SIZE_EXCEEDS_REMAINING`
    defense within the same tick. Post-fix the increment runs under
    the launcher-supplied `positions_lock`.
    '''

    def test_concurrent_increments_and_decrements_no_lost_update(self) -> None:
        '''Two threads serialise on the same lock: one tight-loops
        the locked submit-side increment helper, the other tight-loops
        a locked decrement that mirrors `_clear_pending_exit`. Final
        `pending_exit` must equal sum-of-increments minus
        sum-of-decrements, with no lost update from torn RMW.
        '''

        import threading as _threading

        position = Position(
            trade_id='t1',
            strategy_id='strat_001',
            symbol='BTCUSDT',
            side=OrderSide.BUY,
            size=Decimal('1000'),
            entry_price=Decimal('100'),
            avg_cost_basis=Decimal('100'),
        )

        lock = _threading.Lock()
        increments = 1000
        decrements = 500
        increment_size = Decimal('1')
        decrement_size = Decimal('1')

        def increment_many() -> None:
            for _ in range(increments):
                with lock:
                    position.pending_exit += increment_size

        def decrement_many() -> None:
            for _ in range(decrements):
                with lock:
                    position.pending_exit = max(
                        Decimal('0'), position.pending_exit - decrement_size,
                    )

        position.pending_exit = Decimal('0')

        all_threads = [
            _threading.Thread(target=increment_many),
            _threading.Thread(target=decrement_many),
        ]
        for t in all_threads:
            t.start()
        for t in all_threads:
            t.join(timeout=10)

        alive = [t.name for t in all_threads if t.is_alive()]
        assert not alive, f'threads did not finish: {alive}'

        expected = (
            Decimal(increments) * increment_size
            - Decimal(decrements) * decrement_size
        )
        assert position.pending_exit == expected, (
            f'lost-update detected — pending_exit={position.pending_exit} '
            f'expected={expected}'
        )

    def test_submit_actions_with_lock_increments_pending_exit(self) -> None:
        '''Smoke: submit_actions threads positions_lock to the
        increment site; the locked write produces the same final
        value as the unlocked baseline test
        (test_exit_submission_increments_pending_exit) on the happy
        path with no contention.
        '''

        import threading as _threading

        state = _state_with_position()
        ctx = _exit_context(state)
        validator = MagicMock()
        validator.validate.return_value = ValidationDecision(allowed=True)
        outbound = MagicMock()
        outbound.send_command.return_value = 'cmd_x1'

        submit_actions(
            [_exit_action()],
            strategy_id='strat_001',
            config=_config(),
            praxis_outbound=outbound,
            validator=validator,
            build_context=lambda _a, _s: ctx,
            now=_now,
            positions_lock=_threading.Lock(),
        )

        assert state.positions['t1'].pending_exit == Decimal('0.5')


class TestPerActionBoundContext:
    '''Per-action structlog contextvars bind during the iteration body.

    `submit_actions` wraps the per-action body in
    `bound_context(strategy_id=..., action_type=..., trade_id=...,
    command_id=...)` so any downstream emit (validator stage,
    capital_controller, praxis_outbound) carries the action's
    correlation fields without each emit site threading them through
    `extra={...}`. The pin: validator.validate sees the contextvars
    bound mid-iteration; after `submit_actions` returns the
    contextvars are unbound (so the caller's outer context is not
    polluted by the per-action keys).
    '''

    def test_validator_sees_bound_strategy_and_action_type(self) -> None:
        '''The validator runs *inside* the per-action with-block.'''

        from structlog.contextvars import get_contextvars  # local import to keep top of file lean

        from nexus.infrastructure.observability import clear_context

        clear_context()
        captured: dict[str, object] = {}

        def _capture_validate(_ctx: ValidationRequestContext) -> ValidationDecision:
            captured.update(get_contextvars())
            return _allow_decision()

        validator = MagicMock()
        validator.validate.side_effect = _capture_validate
        outbound = MagicMock()
        outbound.send_command.return_value = 'cmd_btc'

        submit_actions(
            [_enter_action()],
            strategy_id='strat_btc_logreg',
            config=_config(),
            praxis_outbound=outbound,
            validator=validator,
            build_context=lambda _a, _s: _enter_context(),
            now=_now,
        )

        assert captured.get('strategy_id') == 'strat_btc_logreg'
        assert captured.get('action_type') == 'enter'

        clear_context()

    def test_contextvars_unbound_after_submit_returns(self) -> None:
        '''Per-action keys do not leak past the submit_actions call.'''

        from structlog.contextvars import get_contextvars

        from nexus.infrastructure.observability import clear_context

        clear_context()

        validator = MagicMock()
        validator.validate.return_value = _allow_decision()
        outbound = MagicMock()
        outbound.send_command.return_value = 'cmd_ok'

        submit_actions(
            [_enter_action()],
            strategy_id='strat_btc_logreg',
            config=_config(),
            praxis_outbound=outbound,
            validator=validator,
            build_context=lambda _a, _s: _enter_context(),
            now=_now,
        )

        leaked = get_contextvars()
        assert 'strategy_id' not in leaked
        assert 'action_type' not in leaked
        assert 'trade_id' not in leaked
        assert 'command_id' not in leaked

        clear_context()

    def test_exit_action_binds_trade_id(self) -> None:
        '''An EXIT action with a trade_id binds trade_id for the iteration.'''

        from structlog.contextvars import get_contextvars

        from nexus.infrastructure.observability import clear_context

        clear_context()
        captured: dict[str, object] = {}

        def _capture_validate(_ctx: ValidationRequestContext) -> ValidationDecision:
            captured.update(get_contextvars())
            return _allow_decision()

        validator = MagicMock()
        validator.validate.side_effect = _capture_validate
        outbound = MagicMock()
        outbound.send_command.return_value = 'cmd_exit'

        submit_actions(
            [_exit_action(trade_id='t_xyz')],
            strategy_id='strat_btc_logreg',
            config=_config(),
            praxis_outbound=outbound,
            validator=validator,
            build_context=lambda _a, _s: _exit_context(
                _state_with_position('t_xyz'),
                trade_id='t_xyz',
            ),
            now=_now,
        )

        assert captured.get('strategy_id') == 'strat_btc_logreg'
        assert captured.get('action_type') == 'exit'
        assert captured.get('trade_id') == 't_xyz'

        clear_context()

    def test_abort_action_binds_command_id(self) -> None:
        '''An ABORT action with command_id binds command_id for the iteration.'''

        from structlog.contextvars import get_contextvars

        from nexus.infrastructure.observability import clear_context

        clear_context()
        captured: dict[str, object] = {}

        outbound = MagicMock()

        def _capture_send_abort(**_kw: object) -> None:
            captured.update(get_contextvars())

        outbound.send_abort.side_effect = _capture_send_abort
        validator = MagicMock()

        submit_actions(
            [_abort_action(command_id='cmd_abort_777')],
            strategy_id='strat_btc_logreg',
            config=_config(),
            praxis_outbound=outbound,
            validator=validator,
            build_context=lambda _a, _s: _enter_context(),
            now=_now,
        )

        assert captured.get('strategy_id') == 'strat_btc_logreg'
        assert captured.get('action_type') == 'abort'
        assert captured.get('command_id') == 'cmd_abort_777'

        clear_context()


class TestPreRegistration:

    def _allow_with_reservation(
        self,
    ) -> tuple[CapitalController, object, ValidationDecision]:
        controller = CapitalController(CapitalState(capital_pool=Decimal('10000')))
        res = controller.check_and_reserve(
            strategy_id='strat_001',
            order_notional=Decimal('100'),
            estimated_fees=Decimal('1'),
            strategy_budget=Decimal('5000'),
        )
        assert res.reservation is not None
        decision = ValidationDecision(allowed=True, reservation=res.reservation)

        return controller, res.reservation, decision

    def test_handle_marked_submitted_on_success(self) -> None:
        ctx = _enter_context()
        controller, _res, decision = self._allow_with_reservation()
        validator = MagicMock()
        validator.validate.return_value = decision
        outbound = MagicMock()
        outbound.send_command.return_value = 'cmd_det_aaaaaaaaaaaaaaaa'
        handle = MagicMock()

        results = submit_actions(
            [_enter_action()],
            strategy_id='strat_001',
            config=_config(),
            praxis_outbound=outbound,
            validator=validator,
            build_context=lambda _a, _s: ctx,
            now=_now,
            capital_controller=controller,
            pre_register=lambda _cmd, _dec: handle,
        )

        assert results[0][1].status == SubmissionStatus.SUBMITTED
        handle.mark_submitted.assert_called_once_with('cmd_det_aaaaaaaaaaaaaaaa')
        handle.rollback.assert_not_called()
        handle.mark_unknown.assert_not_called()

    def test_timeout_with_handle_does_not_release_reservation(self) -> None:
        # submit_actions does NOT release on timeout when a handle owns
        # rollback; the real launcher handle would have consumed the
        # reservation into a capital order via send_order before the
        # timeout, so release ownership has moved to the handle. The mock
        # handle is inert, so the reservation simply stays untouched here.
        ctx = _enter_context()
        controller, res, decision = self._allow_with_reservation()
        validator = MagicMock()
        validator.validate.return_value = decision
        outbound = MagicMock()
        outbound.send_command.side_effect = TimeoutError('praxis did not respond')
        handle = MagicMock()

        results = submit_actions(
            [_enter_action()],
            strategy_id='strat_001',
            config=_config(),
            praxis_outbound=outbound,
            validator=validator,
            build_context=lambda _a, _s: ctx,
            now=_now,
            capital_controller=controller,
            pre_register=lambda _cmd, _dec: handle,
        )

        outcome = results[0][1]
        assert outcome.status == SubmissionStatus.SUBMISSION_UNKNOWN
        assert outcome.command_id is not None
        handle.mark_unknown.assert_called_once()
        handle.rollback.assert_not_called()
        assert res.reservation_id in controller._reservations

    def test_handle_callback_exception_does_not_abort_tick(self) -> None:
        ctx = _enter_context()
        controller, _res, decision = self._allow_with_reservation()
        validator = MagicMock()
        validator.validate.return_value = decision
        outbound = MagicMock()
        outbound.send_command.return_value = 'cmd_det_bbbbbbbbbbbbbbbb'
        handle = MagicMock()
        handle.mark_submitted.side_effect = RuntimeError('handle bug')

        results = submit_actions(
            [_enter_action()],
            strategy_id='strat_001',
            config=_config(),
            praxis_outbound=outbound,
            validator=validator,
            build_context=lambda _a, _s: ctx,
            now=_now,
            capital_controller=controller,
            pre_register=lambda _cmd, _dec: handle,
        )

        assert len(results) == 1
        assert results[0][1].status == SubmissionStatus.SUBMITTED

    def test_mark_unknown_exception_still_returns_unknown(self) -> None:
        ctx = _enter_context()
        controller, _res, decision = self._allow_with_reservation()
        validator = MagicMock()
        validator.validate.return_value = decision
        outbound = MagicMock()
        outbound.send_command.side_effect = TimeoutError('praxis did not respond')
        handle = MagicMock()
        handle.mark_unknown.side_effect = RuntimeError('handle bug')

        results = submit_actions(
            [_enter_action()],
            strategy_id='strat_001',
            config=_config(),
            praxis_outbound=outbound,
            validator=validator,
            build_context=lambda _a, _s: ctx,
            now=_now,
            capital_controller=controller,
            pre_register=lambda _cmd, _dec: handle,
        )

        assert results[0][1].status == SubmissionStatus.SUBMISSION_UNKNOWN

    def test_timeout_without_handle_is_submit_failed_and_releases(self) -> None:
        ctx = _enter_context()
        controller, res, decision = self._allow_with_reservation()
        validator = MagicMock()
        validator.validate.return_value = decision
        outbound = MagicMock()
        outbound.send_command.side_effect = TimeoutError('praxis did not respond')

        results = submit_actions(
            [_enter_action()],
            strategy_id='strat_001',
            config=_config(),
            praxis_outbound=outbound,
            validator=validator,
            build_context=lambda _a, _s: ctx,
            now=_now,
            capital_controller=controller,
        )

        assert results[0][1].status == SubmissionStatus.SUBMIT_FAILED
        assert res.reservation_id not in controller._reservations

    def test_non_timeout_failure_with_handle_rolls_back(self) -> None:
        ctx = _enter_context()
        controller, _res, decision = self._allow_with_reservation()
        validator = MagicMock()
        validator.validate.return_value = decision
        outbound = MagicMock()
        outbound.send_command.side_effect = RuntimeError('venue rejected')
        handle = MagicMock()

        results = submit_actions(
            [_enter_action()],
            strategy_id='strat_001',
            config=_config(),
            praxis_outbound=outbound,
            validator=validator,
            build_context=lambda _a, _s: ctx,
            now=_now,
            capital_controller=controller,
            pre_register=lambda _cmd, _dec: handle,
        )

        assert results[0][1].status == SubmissionStatus.SUBMIT_FAILED
        handle.rollback.assert_called_once()
        handle.mark_submitted.assert_not_called()

    def test_pre_register_failure_is_pre_handoff(self) -> None:
        ctx = _enter_context()
        controller, res, decision = self._allow_with_reservation()
        validator = MagicMock()
        validator.validate.return_value = decision
        outbound = MagicMock()

        def _boom(_cmd: object, _dec: object) -> object:
            msg = 'registry insert failed'
            raise RuntimeError(msg)

        results = submit_actions(
            [_enter_action()],
            strategy_id='strat_001',
            config=_config(),
            praxis_outbound=outbound,
            validator=validator,
            build_context=lambda _a, _s: ctx,
            now=_now,
            capital_controller=controller,
            pre_register=_boom,
        )

        assert results[0][1].status == SubmissionStatus.SUBMIT_FAILED
        outbound.send_command.assert_not_called()
        assert res.reservation_id not in controller._reservations
