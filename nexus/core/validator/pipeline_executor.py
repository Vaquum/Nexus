'''Ordered validator pipeline execution with deny short-circuit semantics.'''

from __future__ import annotations

from collections.abc import Callable, Mapping

from nexus.core.validator.pipeline_models import (
    ValidationAction,
    DEFAULT_VALIDATION_STAGE_ORDER,
    ValidationDecision,
    ValidationRequestContext,
    ValidationStage,
)

__all__ = ['StageValidator', 'ValidationPipeline']

StageValidator = Callable[[ValidationRequestContext], ValidationDecision]


class ValidationPipeline:
    '''Execute validator stages in strict order with fail-fast denial behavior.

    Args:
        validators: Mapping from stage to stage validator callable.
        stage_order: Ordered stage execution contract.
    '''

    def __init__(
        self,
        validators: Mapping[ValidationStage, StageValidator],
        stage_order: tuple[ValidationStage, ...] = DEFAULT_VALIDATION_STAGE_ORDER,
    ) -> None:
        self._stage_order = _validate_stage_order(stage_order)
        self._validators = _validate_validators(validators, self._stage_order)

    @property
    def stage_order(self) -> tuple[ValidationStage, ...]:
        '''Return the configured stage execution order.'''

        return self._stage_order

    def validate(self, context: ValidationRequestContext) -> ValidationDecision:
        '''Run configured stages in order until deny or successful completion.'''

        reservation = None

        for stage in self._stage_order:
            if _should_bypass_stage(context.action, stage):
                continue

            decision = self._validators[stage](context)
            if not isinstance(decision, ValidationDecision):
                msg = (
                    f'Validator for stage {stage.value} must return ValidationDecision'
                )
                raise ValueError(msg)

            if not decision.allowed:
                if decision.failed_stage != stage:
                    msg = (
                        'Denied ValidationDecision failed_stage '
                        f'{decision.failed_stage} does not match current stage {stage}'
                    )
                    raise ValueError(msg)
                return decision

            if decision.reservation is not None:
                reservation = decision.reservation

        return ValidationDecision(allowed=True, reservation=reservation)


def _validate_stage_order(
    stage_order: tuple[ValidationStage, ...],
) -> tuple[ValidationStage, ...]:
    if not isinstance(stage_order, tuple):
        msg = 'stage_order must be a tuple of ValidationStage values'
        raise ValueError(msg)

    if len(stage_order) != len(ValidationStage):
        msg = (
            'stage_order must contain '
            f'{len(ValidationStage)} stages; got {len(stage_order)}'
        )
        raise ValueError(msg)

    if set(stage_order) != set(ValidationStage):
        msg = 'stage_order must contain each ValidationStage exactly once'
        raise ValueError(msg)

    return stage_order


def _validate_validators(
    validators: Mapping[ValidationStage, StageValidator],
    stage_order: tuple[ValidationStage, ...],
) -> dict[ValidationStage, StageValidator]:
    if not isinstance(validators, Mapping):
        msg = 'validators must be a mapping of ValidationStage to callable'
        raise ValueError(msg)

    missing = [stage for stage in stage_order if stage not in validators]
    if missing:
        missing_names = ', '.join(stage.value for stage in missing)
        msg = f'missing validators for stages: {missing_names}'
        raise ValueError(msg)

    normalized: dict[ValidationStage, StageValidator] = {}
    for stage in stage_order:
        validator = validators[stage]
        if not callable(validator):
            msg = f'validator for stage {stage.value} must be callable'
            raise ValueError(msg)
        normalized[stage] = validator

    return normalized


def _should_bypass_stage(action: ValidationAction, stage: ValidationStage) -> bool:
    if action not in (
        ValidationAction.EXIT,
        ValidationAction.ABORT,
        ValidationAction.CANCEL,
    ):
        return False

    return stage in (ValidationStage.HEALTH, ValidationStage.PLATFORM_LIMITS)
