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
                if reservation is not None and decision.reservation is None:
                    return ValidationDecision(
                        allowed=False,
                        failed_stage=decision.failed_stage,
                        reason_code=decision.reason_code,
                        message=decision.message,
                        reservation=reservation,
                    )
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
    '''Decide whether to skip a validator stage for a given action.

    EXIT / ABORT / CANCEL are exit-type actions whose only purpose is
    to reduce existing exposure. Any stage that gates *new* exposure
    must not block exits — otherwise the very conditions that make an
    exit critical (drawdown, degraded venue health, capital pressure,
    stale orderbook) prevent the strategy from cutting risk.

    MODIFY joins them for the capital-type stages. An amend
    re-parametrizes an already-sized command — retargeting a scheme's
    slices/cadence, a bracket leg's price, an iceberg's display, a
    ladder's rung distribution — and never changes total notional, so
    the original ENTER reservation still covers it: an amend reserves
    no new capital and must not be re-gated on exposure.

    The bypass set has grown incrementally as each gating stage was
    audited:
    - CAPITAL / HEALTH / PLATFORM_LIMITS — original safety bypass.
    - PT-FIX-32 added `RISK` (drawdown / portfolio-risk checks).
    - PT-FIX-37 added `PRICE` (book-staleness / spread checks). A
      stale or wide-spread market is exactly when a fast EXIT
      matters most; pre-fix `PRICE_BOOK_STALE` and
      `PRICE_SPREAD_LIMIT` denials silently dropped the EXIT.

    `INTAKE` is deliberately NOT in the bypass set — it handles
    symbol normalization, schema sanity, and operational-mode gating
    that must apply to every action. For MODIFY this is the whole
    point: intake enforces the HALTED-blocks-amends gate and the
    `modifiable_command_ids` lifecycle check. The intake stage's
    `INTAKE_ORDER_NOTIONAL_ZERO` check is exempted for safety
    actions inside that stage's logic (PT-FIX-33), not via this
    bypass set.
    '''

    if action not in (
        ValidationAction.EXIT,
        ValidationAction.ABORT,
        ValidationAction.CANCEL,
        ValidationAction.MODIFY,
    ):
        return False

    return stage in (
        ValidationStage.CAPITAL,
        ValidationStage.HEALTH,
        ValidationStage.PLATFORM_LIMITS,
        ValidationStage.RISK,
        ValidationStage.PRICE,
    )
