'''Tests for nexus/strategy/action_submit.py.'''

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from nexus.core.capital_controller.reservation import Reservation
from nexus.core.domain.enums import OrderSide
from nexus.core.domain.instance_state import InstanceState
from nexus.core.domain.order_types import ExecutionMode, MakerPreference, OrderType
from nexus.core.stp_mode import STPMode
from nexus.core.validator.pipeline_models import (
    ValidationAction,
    ValidationDecision,
    ValidationRequestContext,
    ValidationStage,
)
from nexus.instance_config import InstanceConfig
from nexus.strategy.action import Action, ActionType
from nexus.strategy.action_submit import (
    SubmissionStatus,
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
