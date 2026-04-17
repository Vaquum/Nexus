# Validation Pipeline

This guide explains how Nexus checks strategy intent before it becomes a Praxis command.

## What This Guide Covers

- the stage order Nexus enforces today
- fail-fast behavior and shutdown bypass rules
- how capital reservation fits into validation
- how allowed actions are translated into `TradeCommand`

## Stage Order

The canonical validator order is defined in `nexus/core/validator/pipeline_models.py`:

1. `INTAKE`
2. `RISK`
3. `PRICE`
4. `CAPITAL`
5. `HEALTH`
6. `PLATFORM_LIMITS`

Nexus treats that order as a contract. A valid pipeline must contain each stage exactly once.

## Why Ordered Validation Matters

The pipeline is deliberately strict.

- cheap and structural checks happen first
- capital reservation only happens after earlier checks pass
- later stages can deny even after earlier stages succeeded
- the first deny stops the pipeline

That keeps failure modes deterministic and prevents wasted state mutations.

## Request Context

Validation runs on an immutable `ValidationRequestContext`.

It carries:

- `strategy_id`
- action type
- symbol
- order side and size
- trade and command references
- order notional and estimated fees
- strategy budget
- current `InstanceState`
- current `InstanceConfig`

This is the boundary between strategy intent and decision enforcement.

## Allow And Deny Semantics

Each stage returns a `ValidationDecision`.

Allowed decisions may optionally carry a capital reservation.

Denied decisions must include:

- the failed stage
- a machine-readable reason code
- a human-readable message

If a later stage denies after capital has already reserved funds, the pipeline preserves the reservation object in the final deny result so callers can release it correctly.

## Shutdown Bypass Rules

For `EXIT`, `ABORT`, and `CANCEL`, Nexus bypasses:

- `CAPITAL`
- `HEALTH`
- `PLATFORM_LIMITS`

That design reflects the safety priority during reduction or cancellation flows: getting risk off should not depend on the same checks that gate new exposure.

## Capital Stage

Capital checks delegate to `nexus/core/capital_controller/capital_controller.py`.

The controller enforces:

- per-trade allocation ceiling
- per-strategy budget ceiling
- available-capital ceiling
- total utilization ceiling
- reservation expiry

Reservations are atomic and lock-guarded. That prevents two strategies from racing each other into the same capital pool.

The reservation lifecycle is:

1. reserve capital
2. move reservation to in-flight order state
3. move in-flight to working order state
4. deploy capital on fill
5. release remaining capital on reject, cancel, or completion

## Health Stage

The health evaluator maps a `HealthSnapshot` into one of three modes:

- `ACTIVE`
- `REDUCE_ONLY`
- `HALTED`

It already has threshold logic for latency, failure counts, failure rate, rate-limit headroom, and clock drift. What it does not yet have is a fully live health feed from Praxis in the main runtime.

That distinction matters:

- the evaluation model exists
- the live data source is still a follow-up

## Translation To Praxis Commands

After an action is allowed, Nexus can translate it into a `TradeCommand` through `nexus/infrastructure/praxis_connector/translate.py`.

Translation sets:

- command type
- account id
- venue
- symbol
- notional
- UTC creation timestamp
- side and size when required
- STP mode from `InstanceConfig`
- trade id and reservation id when present

## Current Scope Limits

This is the most important caveat in the current decision layer.

The validator and translation path are structurally in place, but the public `Action` surface is not yet rich enough to express full live trade instructions end to end. That gap is already tracked in [Technical Debt](TechnicalDebt.md).

So the right current reading is:

- the validation architecture exists
- the capital-control model exists
- the Praxis command model exists
- the final action payload contract is still being completed

## Example Reading Path

For one `ENTER` action, the intended flow is:

1. strategy emits `ENTER`
2. request context is built
3. intake checks structure and duplication rules
4. risk and price checks evaluate exposure and market conditions
5. capital stage reserves funds atomically
6. health and platform checks confirm the runtime can still deploy
7. action is translated into a Praxis `TradeCommand`

## Read Next

- Continue to [Persistence And Recovery](Persistence-And-Recovery.md) for how state changes survive crashes
- Continue to [Startup And Shutdown](Startup-And-Shutdown.md) for where validator-adjacent runtime steps happen
- Continue to [Technical Debt](TechnicalDebt.md) for the remaining action-surface gaps
