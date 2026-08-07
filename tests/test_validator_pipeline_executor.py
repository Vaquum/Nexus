'''Verify validator pipeline executor ordering and short-circuit behavior.'''

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from collections.abc import Callable
from typing import Any

import pytest

from nexus.core.capital_controller.reservation import Reservation
from nexus.core.validator import (
    DEFAULT_VALIDATION_STAGE_ORDER,
    ValidationAction,
    ValidationDecision,
    ValidationPipeline,
    ValidationRequestContext,
    ValidationStage,
    build_default_intake_hooks,
    validate_intake_stage,
)
from nexus.instance_config import InstanceConfig
from nexus.core.domain.instance_state import InstanceState


def _make_context(**overrides: Any) -> ValidationRequestContext:
    config = InstanceConfig(
        account_id='acc_001',
        venue='binance_spot',
    )
    defaults: dict[str, Any] = {
        'strategy_id': 'strat_a',
        'action': ValidationAction.ENTER,
        'command_id': 'cmd_exec_1',
        'order_notional': Decimal('100'),
        'current_order_notional': None,
        'estimated_fees': Decimal('1'),
        'strategy_budget': Decimal('5000'),
        'state': InstanceState.fresh(Decimal('10000')),
        'config': config,
    }
    defaults.update(overrides)
    return ValidationRequestContext(**defaults)


def _allow(_: ValidationRequestContext) -> ValidationDecision:
    return ValidationDecision(allowed=True)


class TestValidationPipelineConfig:
    def test_requires_validator_for_each_stage(self) -> None:
        validators = {
            stage: _allow
            for stage in DEFAULT_VALIDATION_STAGE_ORDER
            if stage != ValidationStage.PLATFORM_LIMITS
        }
        with pytest.raises(ValueError, match='missing validators'):
            ValidationPipeline(validators=validators)

    def test_rejects_non_callable_validator(self) -> None:
        validators: dict[ValidationStage, Any] = dict.fromkeys(
            DEFAULT_VALIDATION_STAGE_ORDER,
            _allow,
        )
        validators[ValidationStage.PRICE] = object()
        with pytest.raises(ValueError, match='must be callable'):
            ValidationPipeline(validators=validators)

    def test_rejects_incomplete_stage_order(self) -> None:
        validators = dict.fromkeys(DEFAULT_VALIDATION_STAGE_ORDER, _allow)
        with pytest.raises(ValueError, match='must contain 6 stages; got 5'):
            ValidationPipeline(
                validators=validators,
                stage_order=DEFAULT_VALIDATION_STAGE_ORDER[:-1],
            )


class TestValidationPipelineRun:
    def test_runs_stages_in_configured_order(self) -> None:
        order_seen: list[ValidationStage] = []

        def make_stage(
            stage: ValidationStage,
        ) -> Callable[[ValidationRequestContext], ValidationDecision]:
            def stage_fn(_: ValidationRequestContext) -> ValidationDecision:
                order_seen.append(stage)
                return ValidationDecision(allowed=True)

            return stage_fn

        validators = {
            stage: make_stage(stage) for stage in DEFAULT_VALIDATION_STAGE_ORDER
        }
        pipeline = ValidationPipeline(validators=validators)

        decision = pipeline.validate(_make_context())

        assert decision.allowed is True
        assert order_seen == list(DEFAULT_VALIDATION_STAGE_ORDER)

    def test_short_circuits_on_first_denial(self) -> None:
        order_seen: list[ValidationStage] = []

        def make_stage(
            stage: ValidationStage,
        ) -> Callable[[ValidationRequestContext], ValidationDecision]:
            def stage_fn(_: ValidationRequestContext) -> ValidationDecision:
                order_seen.append(stage)
                if stage == ValidationStage.PRICE:
                    return ValidationDecision(
                        allowed=False,
                        failed_stage=ValidationStage.PRICE,
                        reason_code='PRICE_STALE',
                        message='Book staleness exceeded threshold',
                    )
                return ValidationDecision(allowed=True)

            return stage_fn

        validators = {
            stage: make_stage(stage) for stage in DEFAULT_VALIDATION_STAGE_ORDER
        }
        pipeline = ValidationPipeline(validators=validators)

        decision = pipeline.validate(_make_context())

        assert decision.allowed is False
        assert decision.failed_stage == ValidationStage.PRICE
        assert order_seen == [
            ValidationStage.INTAKE,
            ValidationStage.RISK,
            ValidationStage.PRICE,
        ]

    def test_denial_must_match_current_stage(self) -> None:
        def intake(_: ValidationRequestContext) -> ValidationDecision:
            return ValidationDecision(
                allowed=False,
                failed_stage=ValidationStage.RISK,
                reason_code='X',
                message='x',
            )

        validators = dict.fromkeys(DEFAULT_VALIDATION_STAGE_ORDER, _allow)
        validators[ValidationStage.INTAKE] = intake

        pipeline = ValidationPipeline(validators=validators)
        with pytest.raises(ValueError, match='does not match current stage'):
            pipeline.validate(_make_context())

    @pytest.mark.parametrize(
        'action',
        [ValidationAction.EXIT, ValidationAction.ABORT, ValidationAction.CANCEL],
    )
    def test_safety_actions_bypass_capital_health_platform_limits_risk_and_price(
        self,
        action: ValidationAction,
    ) -> None:
        '''PT-FIX-32 + PT-FIX-37: bypass set is CAPITAL / HEALTH /
        PLATFORM_LIMITS / RISK / PRICE for EXIT / ABORT / CANCEL.
        Each gates *new* exposure, not exit. Stale orderbook is
        exactly when a fast EXIT matters most; pre-PT-FIX-37 a
        `PRICE_BOOK_STALE` denial silently dropped the EXIT.'''

        order_seen: list[ValidationStage] = []

        def make_stage(
            stage: ValidationStage,
        ) -> Callable[[ValidationRequestContext], ValidationDecision]:
            def stage_fn(_: ValidationRequestContext) -> ValidationDecision:
                order_seen.append(stage)
                if stage in (
                    ValidationStage.CAPITAL,
                    ValidationStage.HEALTH,
                    ValidationStage.PLATFORM_LIMITS,
                    ValidationStage.RISK,
                    ValidationStage.PRICE,
                ):
                    return ValidationDecision(
                        allowed=False,
                        failed_stage=stage,
                        reason_code='SHOULD_NOT_RUN',
                        message='safety action bypass failed',
                    )
                return ValidationDecision(allowed=True)

            return stage_fn

        validators = {
            stage: make_stage(stage) for stage in DEFAULT_VALIDATION_STAGE_ORDER
        }
        pipeline = ValidationPipeline(validators=validators)

        decision = pipeline.validate(_make_context(action=action))

        assert decision.allowed is True
        assert ValidationStage.CAPITAL not in order_seen
        assert ValidationStage.HEALTH not in order_seen
        assert ValidationStage.PLATFORM_LIMITS not in order_seen
        assert ValidationStage.RISK not in order_seen
        assert ValidationStage.PRICE not in order_seen

    @pytest.mark.parametrize(
        'action',
        [ValidationAction.EXIT, ValidationAction.ABORT, ValidationAction.CANCEL],
    )
    def test_risk_stage_failure_does_not_block_exit_actions(
        self,
        action: ValidationAction,
    ) -> None:
        '''PT-FIX-32 regression: a RISK validator that always denies
        must NOT block EXIT / ABORT / CANCEL. Pre-fix the EXIT was
        rejected with the RISK stage's reason code; post-fix the
        bypass returns `allowed=True` and the action progresses to
        the venue.'''

        def risk_always_denies(
            _: ValidationRequestContext,
        ) -> ValidationDecision:
            return ValidationDecision(
                allowed=False,
                failed_stage=ValidationStage.RISK,
                reason_code='RISK_DRAWDOWN_BREACH',
                message='drawdown limit hit',
            )

        validators: dict[
            ValidationStage,
            Callable[[ValidationRequestContext], ValidationDecision],
        ] = {
            stage: lambda _: ValidationDecision(allowed=True)
            for stage in DEFAULT_VALIDATION_STAGE_ORDER
        }
        validators[ValidationStage.RISK] = risk_always_denies
        pipeline = ValidationPipeline(validators=validators)

        decision = pipeline.validate(_make_context(action=action))

        assert decision.allowed is True
        assert decision.failed_stage is None

    def test_risk_stage_still_runs_for_enter_actions(self) -> None:
        '''PT-FIX-32 must not regress entry gating. RISK still runs
        (and can deny) for ENTER actions.'''

        def risk_always_denies(
            _: ValidationRequestContext,
        ) -> ValidationDecision:
            return ValidationDecision(
                allowed=False,
                failed_stage=ValidationStage.RISK,
                reason_code='RISK_DRAWDOWN_BREACH',
                message='drawdown limit hit',
            )

        validators: dict[
            ValidationStage,
            Callable[[ValidationRequestContext], ValidationDecision],
        ] = {
            stage: lambda _: ValidationDecision(allowed=True)
            for stage in DEFAULT_VALIDATION_STAGE_ORDER
        }
        validators[ValidationStage.RISK] = risk_always_denies
        pipeline = ValidationPipeline(validators=validators)

        decision = pipeline.validate(_make_context(action=ValidationAction.ENTER))

        assert decision.allowed is False
        assert decision.failed_stage == ValidationStage.RISK
        assert decision.reason_code == 'RISK_DRAWDOWN_BREACH'

    @pytest.mark.parametrize(
        'action',
        [ValidationAction.EXIT, ValidationAction.ABORT, ValidationAction.CANCEL],
    )
    def test_price_stage_failure_does_not_block_exit_actions(
        self,
        action: ValidationAction,
    ) -> None:
        '''PT-FIX-37 regression: a PRICE validator that always denies
        (e.g. `PRICE_BOOK_STALE` on a stale orderbook) must NOT block
        EXIT / ABORT / CANCEL. Pre-fix the EXIT was rejected before
        translate; post-fix the bypass returns `allowed=True`.'''

        def price_always_denies(
            _: ValidationRequestContext,
        ) -> ValidationDecision:
            return ValidationDecision(
                allowed=False,
                failed_stage=ValidationStage.PRICE,
                reason_code='PRICE_BOOK_STALE',
                message='orderbook staleness exceeded threshold',
            )

        validators: dict[
            ValidationStage,
            Callable[[ValidationRequestContext], ValidationDecision],
        ] = {
            stage: lambda _: ValidationDecision(allowed=True)
            for stage in DEFAULT_VALIDATION_STAGE_ORDER
        }
        validators[ValidationStage.PRICE] = price_always_denies
        pipeline = ValidationPipeline(validators=validators)

        decision = pipeline.validate(_make_context(action=action))

        assert decision.allowed is True
        assert decision.failed_stage is None

    def test_price_stage_still_runs_for_enter_actions(self) -> None:
        '''PT-FIX-37 must not regress entry gating. PRICE still runs
        (and can deny) for ENTER actions.'''

        def price_always_denies(
            _: ValidationRequestContext,
        ) -> ValidationDecision:
            return ValidationDecision(
                allowed=False,
                failed_stage=ValidationStage.PRICE,
                reason_code='PRICE_BOOK_STALE',
                message='orderbook staleness exceeded threshold',
            )

        validators: dict[
            ValidationStage,
            Callable[[ValidationRequestContext], ValidationDecision],
        ] = {
            stage: lambda _: ValidationDecision(allowed=True)
            for stage in DEFAULT_VALIDATION_STAGE_ORDER
        }
        validators[ValidationStage.PRICE] = price_always_denies
        pipeline = ValidationPipeline(validators=validators)

        decision = pipeline.validate(_make_context(action=ValidationAction.ENTER))

        assert decision.allowed is False
        assert decision.failed_stage == ValidationStage.PRICE
        assert decision.reason_code == 'PRICE_BOOK_STALE'

    def test_enter_does_not_bypass_health_and_platform_limits(self) -> None:
        order_seen: list[ValidationStage] = []

        def make_stage(
            stage: ValidationStage,
        ) -> Callable[[ValidationRequestContext], ValidationDecision]:
            def stage_fn(_: ValidationRequestContext) -> ValidationDecision:
                order_seen.append(stage)
                if stage == ValidationStage.HEALTH:
                    return ValidationDecision(
                        allowed=False,
                        failed_stage=ValidationStage.HEALTH,
                        reason_code='HEALTH_DENY',
                        message='health denied enter',
                    )
                return ValidationDecision(allowed=True)

            return stage_fn

        validators = {
            stage: make_stage(stage) for stage in DEFAULT_VALIDATION_STAGE_ORDER
        }
        pipeline = ValidationPipeline(validators=validators)

        decision = pipeline.validate(_make_context(action=ValidationAction.ENTER))

        assert decision.allowed is False
        assert decision.failed_stage == ValidationStage.HEALTH
        assert ValidationStage.HEALTH in order_seen

    def test_identical_input_produces_deterministic_denial_reason(self) -> None:
        def make_stage(
            stage: ValidationStage,
        ) -> Callable[[ValidationRequestContext], ValidationDecision]:
            def stage_fn(_: ValidationRequestContext) -> ValidationDecision:
                if stage == ValidationStage.PRICE:
                    return ValidationDecision(
                        allowed=False,
                        failed_stage=ValidationStage.PRICE,
                        reason_code='PRICE_STALE',
                        message='Book staleness exceeded threshold',
                    )
                return ValidationDecision(allowed=True)

            return stage_fn

        validators = {
            stage: make_stage(stage) for stage in DEFAULT_VALIDATION_STAGE_ORDER
        }
        pipeline = ValidationPipeline(validators=validators)
        context = _make_context(action=ValidationAction.ENTER)

        decision_a = pipeline.validate(context)
        decision_b = pipeline.validate(context)

        assert decision_a.allowed is False
        assert decision_b.allowed is False
        assert decision_a.failed_stage == ValidationStage.PRICE
        assert decision_b.failed_stage == ValidationStage.PRICE
        assert decision_a.reason_code == 'PRICE_STALE'
        assert decision_b.reason_code == 'PRICE_STALE'
        assert decision_a.message == 'Book staleness exceeded threshold'
        assert decision_b.message == 'Book staleness exceeded threshold'

    def test_modify_intake_denial_is_deterministic_when_modifiable_set_missing(
        self,
    ) -> None:
        context = _make_context(
            action=ValidationAction.MODIFY,
            command_id='cmd_exec_1',
            order_side=None,
            order_size=Decimal('1'),
        )
        hooks = build_default_intake_hooks(
            context.config,
            active_command_ids={'cmd_exec_1'},
        )

        def intake(_: ValidationRequestContext) -> ValidationDecision:
            return validate_intake_stage(_, hooks=hooks)

        validators = dict.fromkeys(DEFAULT_VALIDATION_STAGE_ORDER, _allow)
        validators[ValidationStage.INTAKE] = intake
        pipeline = ValidationPipeline(validators=validators)

        decision_a = pipeline.validate(context)
        decision_b = pipeline.validate(context)

        assert decision_a.allowed is False
        assert decision_b.allowed is False
        assert decision_a.failed_stage == ValidationStage.INTAKE
        assert decision_b.failed_stage == ValidationStage.INTAKE
        assert decision_a.reason_code == 'INTAKE_MODIFIABLE_COMMANDS_UNAVAILABLE'
        assert decision_b.reason_code == 'INTAKE_MODIFIABLE_COMMANDS_UNAVAILABLE'
        assert (
            decision_a.message
            == 'MODIFY requires modifiable_command_ids to be provided'
        )
        assert decision_b.message == decision_a.message

    def test_late_denial_returns_reservation_for_cleanup(self) -> None:
        created_at = datetime.now(tz=timezone.utc)
        reservation = Reservation(
            reservation_id='res_1',
            strategy_id='strat_a',
            notional=Decimal('100'),
            estimated_fees=Decimal('1'),
            created_at=created_at,
            expires_at=created_at + timedelta(seconds=30),
        )

        def make_stage(
            stage: ValidationStage,
        ) -> Callable[[ValidationRequestContext], ValidationDecision]:
            def stage_fn(_: ValidationRequestContext) -> ValidationDecision:
                if stage == ValidationStage.CAPITAL:
                    return ValidationDecision(allowed=True, reservation=reservation)
                if stage == ValidationStage.HEALTH:
                    return ValidationDecision(
                        allowed=False,
                        failed_stage=ValidationStage.HEALTH,
                        reason_code='HEALTH_DENY',
                        message='health denied enter',
                    )
                return ValidationDecision(allowed=True)

            return stage_fn

        validators = {
            stage: make_stage(stage) for stage in DEFAULT_VALIDATION_STAGE_ORDER
        }
        pipeline = ValidationPipeline(validators=validators)

        decision = pipeline.validate(_make_context(action=ValidationAction.ENTER))

        assert decision.allowed is False
        assert decision.failed_stage == ValidationStage.HEALTH
        assert decision.reservation == reservation

    def test_carries_capital_reservation_on_allowed_pipeline(self) -> None:
        created_at = datetime.now(tz=timezone.utc)
        reservation = Reservation(
            reservation_id='res_2',
            strategy_id='strat_a',
            notional=Decimal('100'),
            estimated_fees=Decimal('1'),
            created_at=created_at,
            expires_at=created_at + timedelta(seconds=30),
        )

        def make_stage(
            stage: ValidationStage,
        ) -> Callable[[ValidationRequestContext], ValidationDecision]:
            def stage_fn(_: ValidationRequestContext) -> ValidationDecision:
                if stage == ValidationStage.CAPITAL:
                    return ValidationDecision(allowed=True, reservation=reservation)
                return ValidationDecision(allowed=True)

            return stage_fn

        validators = {
            stage: make_stage(stage) for stage in DEFAULT_VALIDATION_STAGE_ORDER
        }
        pipeline = ValidationPipeline(validators=validators)

        decision = pipeline.validate(_make_context(action=ValidationAction.ENTER))

        assert decision.allowed is True
        assert decision.reservation == reservation

class TestSafetyBypassContract:
    '''The contract behind PT-FIX-32 + PT-FIX-37: every validator stage that
    gates *new* exposure must be in the EXIT/ABORT/CANCEL/MODIFY bypass set,
    so a stage that legitimately denies an ENTER (drawdown, stale orderbook,
    capital pressure, health degradation, platform limit breach) cannot
    block the strategy from cutting risk or re-parametrizing a live command.

    MODIFY is bypassed alongside the exit actions: an amend re-parametrizes
    an already-sized command and never changes total notional, so no stage
    that gates new exposure applies.

    INTAKE is the only stage NOT bypassed for safety actions — it handles
    symbol normalization and schema sanity that must run for all actions.
    Operational-mode gating inside INTAKE explicitly allows EXIT in
    REDUCE_ONLY (PT-FIX-15) and CANCEL/ABORT in HALTED, and blocks MODIFY
    under HALTED.

    This test pins the bypass set so a future stage addition that should
    bypass for exits cannot silently miss the bypass list.
    '''

    @pytest.mark.parametrize(
        'action',
        [
            ValidationAction.EXIT,
            ValidationAction.ABORT,
            ValidationAction.CANCEL,
            ValidationAction.MODIFY,
        ],
    )
    @pytest.mark.parametrize(
        'gating_stage',
        [
            ValidationStage.RISK,
            ValidationStage.PRICE,
            ValidationStage.CAPITAL,
            ValidationStage.HEALTH,
            ValidationStage.PLATFORM_LIMITS,
        ],
    )
    def test_every_gating_stage_is_bypassed_for_safety_actions(
        self,
        action: ValidationAction,
        gating_stage: ValidationStage,
    ) -> None:
        '''If a future stage is added to `DEFAULT_VALIDATION_STAGE_ORDER`
        without being added to `_should_bypass_stage`'s bypass set, this
        test (when extended with the new stage) catches it: the stage
        denies, the bypass should fire, the action stays allowed.'''

        denying_stage = gating_stage

        validators: dict[
            ValidationStage,
            Callable[[ValidationRequestContext], ValidationDecision],
        ] = {
            stage: lambda _: ValidationDecision(allowed=True)
            for stage in DEFAULT_VALIDATION_STAGE_ORDER
        }
        validators[denying_stage] = lambda _: ValidationDecision(
            allowed=False,
            failed_stage=denying_stage,
            reason_code='SHOULD_BE_BYPASSED_FOR_SAFETY',
            message='gating stage denial that should not block safety actions',
        )
        pipeline = ValidationPipeline(validators=validators)

        decision = pipeline.validate(_make_context(action=action))

        assert decision.allowed is True, (
            f'{action.value} was blocked by {gating_stage.value}: '
            f'{denying_stage.value} must be in the EXIT/ABORT/CANCEL bypass set'
        )

    def test_intake_is_not_bypassed_for_safety_actions(self) -> None:
        '''INTAKE handles symbol normalization, schema sanity, and
        operational-mode gating that must run for every action. It is
        deliberately excluded from the bypass set; its safety-action
        behavior is encoded inside `validate_intake_stage` itself
        (PT-FIX-15 mode gating, PT-FIX-33 zero-notional exemption).'''

        validators: dict[
            ValidationStage,
            Callable[[ValidationRequestContext], ValidationDecision],
        ] = {
            stage: lambda _: ValidationDecision(allowed=True)
            for stage in DEFAULT_VALIDATION_STAGE_ORDER
        }
        validators[ValidationStage.INTAKE] = lambda _: ValidationDecision(
            allowed=False,
            failed_stage=ValidationStage.INTAKE,
            reason_code='INTAKE_RUNS_FOR_ALL_ACTIONS',
            message='intake denial must propagate even for safety actions',
        )
        pipeline = ValidationPipeline(validators=validators)

        decision = pipeline.validate(_make_context(action=ValidationAction.EXIT))

        assert decision.allowed is False
        assert decision.failed_stage == ValidationStage.INTAKE
