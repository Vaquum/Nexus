# Reference Architecture

This page explains how Nexus fits into the wider Vaquum stack and how the main Nexus runtime surfaces work together.

## Nexus In The Stack

Nexus sits between Limen and Praxis.

- Limen produces trained Sensors and live signal inputs
- Nexus runs one per-account Manager instance that turns strategy intent into validated decisions
- Praxis owns downstream execution, venue interaction, and outcome delivery

Nexus does not own market-data research, venue adapters, or system-wide oversight. Its responsibility is narrower: make deterministic, capital-aware, recovery-safe decisions before anything reaches execution.

## One Manager Instance Per Account

The core runtime unit in Nexus is the Manager instance. In the current codebase that concept is implemented through several cooperating components rather than one `Manager` class:

- `nexus/instance_config.py` defines per-account identity, capital limits, price checks, STP mode, and shutdown timeouts
- `nexus/core/domain/instance_state.py` holds mutable runtime state such as capital, risk, positions, and operational mode
- `nexus/startup/sequencer.py` builds the runtime on startup
- `nexus/startup/shutdown_sequencer.py` closes it down safely
- `nexus/strategy/runner.py` dispatches strategy callbacks

That composition is what the docs call a `Manager instance`.

## Core Flow

The current Nexus runtime story is:

1. Load immutable account config and strategy manifest.
2. Recover persisted state from snapshot plus WAL.
3. Wire Limen Sensors and instantiate strategies.
4. Generate Signals from live market data.
5. Dispatch Signals or timers into strategy callbacks.
6. Validate requested actions through ordered stages.
7. Reserve capital atomically when an action needs deployment.
8. Translate allowed actions into Praxis `TradeCommand` objects.
9. Consume Praxis outcomes and update state.
10. Persist state changes and strategy events for recovery.

## Main Subsystems

### Manifest And Runtime Wiring

The manifest defines strategy files, Limen experiments, prediction intervals, timers, and per-strategy capital percentages. Startup turns that static YAML into runtime strategy executors and `WiredSensor` entries.

See:

- [Manifest](Manifest.md)
- [Startup And Shutdown](Startup-And-Shutdown.md)

### Signal And Strategy Flow

Each `WiredSensor` carries a Limen-trained sensor, its Limen manifest, round parameters, target strategy, and prediction cadence. `PredictLoop` pulls live market data, runs feature preparation, predicts on the most recent row, and dispatches the resulting `Signal` into the bound strategy.

See:

- [Signal And Strategy Flow](Signal-And-Strategy-Flow.md)

### Validation And Capital Control

Nexus validates actions in strict stage order: intake, risk, price, capital, health, and platform limits. The pipeline fails fast on deny. Capital reservation is atomic and guarded by a lock so competing strategies cannot over-deploy the shared account pool.

See:

- [Validation Pipeline](Validation-Pipeline.md)

### Persistence And Recovery

Today Nexus uses its own persistence layer:

- state snapshots for full `InstanceState`
- WAL entries for state mutations and strategy events
- replay plus rolling-loss re-derivation during recovery

This is separate from Praxis EventSpine. That split is intentional in the current code, but longer-term event-sourcing convergence is already being considered.

See:

- [Persistence And Recovery](Persistence-And-Recovery.md)
- [Technical Debt](TechnicalDebt.md)

### Startup, Reconciliation, And Shutdown

Startup and shutdown sequencing are first-class parts of the product boundary. They decide whether a Manager instance starts with synchronized state, how it restores strategy state, and how it leaves the system on exit.

See:

- [Startup And Shutdown](Startup-And-Shutdown.md)

## Current Boundary Versus Planned Boundary

The current docs should distinguish clearly between what exists now and what is still planned.

Implemented now:

- manifest loading and validation
- Limen sensor wiring
- signal production and timer loops
- capital reservation lifecycle
- validator stage pipeline
- translation into Praxis trade commands
- WAL plus snapshot recovery
- startup registration and capital reconciliation hooks

Still incomplete or stubbed:

- full action surface for tradeable decisions
- live strategy context from real positions and capital
- health mode selection from live Praxis data
- fully functional shutdown action submission and ABORT escalation
- package-level docs and full reference layer

Use [Technical Debt](TechnicalDebt.md) as the canonical list of those gaps.

## Read Next

- Start with [Manifest](Manifest.md) to understand how runtime configuration enters Nexus
- Continue to [Signal And Strategy Flow](Signal-And-Strategy-Flow.md) for the upstream-to-strategy path
- Continue to [Validation Pipeline](Validation-Pipeline.md) for the decision gate
- Continue to [Persistence And Recovery](Persistence-And-Recovery.md) and [Startup And Shutdown](Startup-And-Shutdown.md) for runtime durability
