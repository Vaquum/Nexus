# Persistence And Recovery

This guide explains how Nexus persists runtime state today and how recovery works after restart.

## What This Guide Covers

- the current WAL plus snapshot model
- what gets persisted
- how recovery rebuilds state
- where the current design is strong and where it is still under revision

## Current Persistence Model

Nexus currently has its own persistence layer separate from Praxis EventSpine.

The main entry point is `nexus/infrastructure/state_store.py`.

It manages:

- a full snapshot file for `InstanceState`
- a write-ahead log for post-snapshot mutations
- persisted strategy events used for strategy state reconstruction and rolling-loss re-derivation

Directory layout:

```text
{base_path}/
  snapshots/
    snapshot.bin
  wal/
    wal.bin
```

## What Gets Persisted

Two record types matter in the current design:

- `STATE_MUTATION`
- `STRATEGY_EVENT`

State mutations persist the latest `InstanceState`.
Strategy events persist domain events that strategies may need for replay and that Nexus uses to recalculate rolling losses.

## Checkpointing

Checkpointing writes a full snapshot and truncates the WAL.

That gives Nexus:

- fast recovery when a recent snapshot exists
- bounded replay volume
- a clear durable state boundary

But it also creates subtle design tradeoffs around rolling windows and event retention, which is why several checkpoint-related follow-ups are already captured in [Technical Debt](TechnicalDebt.md).

## Recovery Path

Recovery is a two-pass process.

1. Load the latest snapshot.
2. Replay WAL entries in sequence.
3. Apply the last persisted state mutation.
4. Collect strategy events.
5. Re-derive rolling losses from those events against the current recovery time.

That last step matters because rolling loss windows are time-based rather than static counters.

## Why Strategy Events Matter

Strategy events are not only for strategy code.

They also support:

- crash-safe replay of strategy-level state
- rolling loss reconstruction
- operational diagnostics about what the strategy previously did

That is why WAL truncation and event retention have to be treated carefully.

## What This Design Solves Well

The current model already gives Nexus:

- durable state across restart
- deterministic recovery
- a clean boundary between current state and event history
- the ability to rebuild strategy-facing history from persisted events

For the current Nexus maturity level, that is enough to support real recovery semantics instead of best-effort restart.

## Current Design Limits

This layer is also where several important follow-ups live.

Examples already tracked in [Technical Debt](TechnicalDebt.md):

- codec-version migration risk for old snapshots
- rolling-loss undercount risk across checkpoints
- longer-term tension between Nexus WAL and Praxis EventSpine

The docs should be explicit here: the current design is durable and intentional, but not the final persistence architecture.

## Likely Future Direction

The broader architecture work around the paper-trading pipeline is already exploring a more spine-driven future where Nexus state becomes projection-based rather than WAL-specific.

That is not the current implementation, so it should not be documented as present behavior. For now, the canonical truth is:

- Nexus persists through snapshot plus WAL
- Praxis persists through EventSpine
- recovery is split across those two layers

## Read Next

- Continue to [Startup And Shutdown](Startup-And-Shutdown.md) for where recovery fits into runtime boot
- Continue to [Reference Architecture](Reference-Architecture.md) for the wider system model
- Continue to [Technical Debt](TechnicalDebt.md) for the persistence issues still open
