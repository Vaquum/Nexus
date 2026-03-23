'''Verify validator pipeline executor ordering and short-circuit behavior.'''

from __future__ import annotations

from decimal import Decimal
from collections.abc import Callable
from typing import Any

import pytest

from nexus.core.validator import (
    DEFAULT_VALIDATION_STAGE_ORDER,
    ValidationAction,
    ValidationDecision,
    ValidationPipeline,
    ValidationRequestContext,
    ValidationStage,
)
from nexus.instance_config import InstanceConfig
from nexus.core.domain.instance_state import InstanceState


def _make_context(
    *, action: ValidationAction = ValidationAction.ENTER
) -> ValidationRequestContext:
    config = InstanceConfig(
        account_id='acc_001',
        venue='binance_spot',
        allocated_capital=Decimal('10000'),
    )
    return ValidationRequestContext(
        strategy_id='strat_a',
        action=action,
        command_id='cmd_exec_1',
        order_notional=Decimal('100'),
        estimated_fees=Decimal('1'),
        strategy_budget=Decimal('5000'),
        state=InstanceState.from_config(config),
        config=config,
    )


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

    def test_safety_actions_bypass_health_and_platform_limits(self) -> None:
        order_seen: list[ValidationStage] = []

        def make_stage(
            stage: ValidationStage,
        ) -> Callable[[ValidationRequestContext], ValidationDecision]:
            def stage_fn(_: ValidationRequestContext) -> ValidationDecision:
                order_seen.append(stage)
                if stage in (
                    ValidationStage.HEALTH,
                    ValidationStage.PLATFORM_LIMITS,
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

        decision = pipeline.validate(_make_context(action=ValidationAction.EXIT))

        assert decision.allowed is True
        assert ValidationStage.HEALTH not in order_seen
        assert ValidationStage.PLATFORM_LIMITS not in order_seen

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
