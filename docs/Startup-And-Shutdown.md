# Startup And Shutdown

This guide explains how a Nexus Manager instance starts, reconciles with Praxis, and shuts down.

## What This Guide Covers

- the startup sequence as implemented today
- where strategy restoration and replay fit in
- what shutdown tries to guarantee
- which parts are complete and which parts are still partial

## Startup Sequence

The current startup orchestrator lives in `nexus/startup/sequencer.py`.

The implemented order is:

1. recover state
2. register with trading
3. reconcile capital
4. load manifest
5. instantiate strategies
6. restore strategy state
7. replay strategy events
8. wire sensors
9. register timers
10. determine mode
11. dispatch `on_startup`

That ordering is the product behavior. It tells you what Nexus considers required before a Manager instance is allowed to run.

## Recovery First

Startup begins by recovering persisted `InstanceState`.

If no persisted state exists, Nexus creates fresh state from allocated capital. That means fresh start and crash recovery use the same entry point rather than two different code paths.

See [Persistence And Recovery](Persistence-And-Recovery.md) for the storage details.

## Registration And Reconciliation

If Praxis outbound wiring is available, startup registers the account with Praxis and then pulls current Praxis positions for reconciliation.

The reconciliation step:

- compares Nexus positions against Praxis positions by `trade_id`
- logs mismatches
- updates `position_notional` to match Praxis totals
- checkpoints the adjusted state if needed

That gives Nexus a path toward account-level synchronization on restart.

## Strategy Restoration

After runtime state is available, startup restores strategy-local state blobs and replays persisted strategy events.

This is separate from `InstanceState`.

The distinction is:

- `InstanceState` restores Manager-level state
- `on_load(bytes)` restores strategy-private state
- `on_event_replay(...)` rebuilds strategy history from persisted events

That gives strategy authors a durable lifecycle hook without requiring them to own Manager persistence.

## Sensor Wiring And Timers

Once strategies exist, startup wires Limen Sensors and registers timer specs from the manifest.

Those runtime outputs feed:

- `PredictLoop` for live signal generation
- `TimerLoop` for callback-based time triggers

## Mode Determination

Startup determines the initial operational mode from health when a health evaluator and snapshot are available.

If they are not wired, Nexus currently defaults to `ACTIVE`.

That is an honest implementation detail, not a docs footnote. The health evaluator exists, but the live health source is still a separate integration concern.

## Shutdown Sequence

The shutdown path lives in `nexus/startup/shutdown_sequencer.py`.

The current order is:

1. stop signals
2. stop timers
3. dispatch `on_shutdown`
4. submit shutdown actions
5. wait for terminal outcomes
6. dispatch `on_save`
7. persist strategy state
8. final checkpoint
9. deregister

## What Shutdown Already Does

Shutdown already handles:

- stopping predict timers
- stopping strategy timers
- collecting strategy shutdown callbacks
- collecting serialized strategy state
- atomically persisting strategy blobs
- final checkpointing of Manager state

That means shutdown is already a real persistence boundary, not just process termination.

## Current Shutdown Limits

Shutdown action submission is not yet fully functional.

Today Nexus can:

- filter shutdown actions to `EXIT` and `ABORT`
- identify the intended control path

But it cannot yet complete the full validated submission path because the public `Action` surface is still missing fields needed for full `TradeCommand` translation.

Similarly, timeout-based ABORT escalation is identified in the design, but still tracked as follow-up work.

The correct current reading is:

- shutdown sequencing exists
- state save exists
- graceful live trade unwinding is only partially implemented

## Why This Layer Matters

In Nexus, startup and shutdown are not peripheral. They are part of the decision-layer contract.

If startup is wrong:

- strategies see the wrong state
- capital can be misrepresented
- mode can begin in the wrong operating state

If shutdown is wrong:

- strategy state is lost
- open actions can be left half-managed
- recovery becomes less trustworthy

## Read Next

- Continue to [Manifest](Manifest.md) for the configuration that startup consumes
- Continue to [Signal And Strategy Flow](Signal-And-Strategy-Flow.md) for what the live runtime does after startup
- Continue to [Persistence And Recovery](Persistence-And-Recovery.md) for the state model behind restart
