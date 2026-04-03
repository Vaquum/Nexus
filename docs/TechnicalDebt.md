# Technical Debt

Known technical debt in shipped code. Each item includes origin, severity, and migration path.

---

## TD-001: WAL codec lacks version-dispatched deserialization

**Origin**: 1.2.1 (WAL entry types and serialization)
**Severity**: Low (only v1 exists)
**Module**: `nexus/infrastructure/wal_codec.py`

`deserialize_state` performs a strict equality check against `_CODEC_VERSION`. When v2 is introduced (e.g. adding a field to a domain dataclass), all data serialized with v1 becomes unreadable. WAL entries are ephemeral (truncated on snapshot), but snapshots persist — a codec bump without migration makes existing snapshots unrecoverable.

**When to fix**: Before any change to serialized domain dataclass fields.
**Migration**: Add version-dispatched deserialization (`_decode_v1()`, `_decode_v2()`, etc.) that routes on the embedded `_v` field so older snapshots remain readable across codec upgrades.

---

## TD-002: Checkpoint truncates strategy events needed for rolling loss windows

**Origin**: 1.3.4 (two-pass recovery with loss re-derivation)
**Severity**: Medium (losses undercounted after checkpoint)
**Module**: `nexus/infrastructure/state_store.py`

`checkpoint()` truncates the entire WAL including STRATEGY_EVENT entries. On recovery, `derive_rolling_losses()` only sees post-checkpoint events. Losses that occurred before the checkpoint but within the 24h/7d/30d windows are lost, causing rolling loss counters to undercount.

**When to fix**: Before periodic checkpoint scheduling (Phase 9).
**Migration**: Either (a) retain STRATEGY_EVENT entries for the longest window (30d) across checkpoints by truncating by age instead of full truncation, or (b) bake accurate rolling loss values into the snapshot at checkpoint time so they serve as baseline, with post-checkpoint events adjusting rather than overwriting.

---

## TD-003: Lifecycle APIs use soft-failure returns that can hide root causes

**Origin**: 2.x lifecycle hardening work (capital reservation/order transitions)
**Severity**: Medium (debuggability/observability risk)
**Modules**:
- `nexus/core/capital_controller/capital_controller.py`

Several lifecycle methods return `False` for failure paths (for example, missing reservation/order ids) instead of emitting typed failure categories. This is ergonomic for caller control flow, but it compresses different failure causes into a single boolean outcome and can hide invariant violations unless logs/metrics are consistently inspected.

**When to evaluate**: End of MVP (explicit post-MVP quality gate).
**Evaluation criteria (MVP close-out)**:
- Enumerate all soft-failure return sites in lifecycle APIs.
- Classify each as expected business miss vs invariant breach.
- Confirm each soft-failure path has structured observability (reason code and context).
- For invariant breaches, decide whether to migrate to hard-failure (`typed exception`) or typed result objects.

**Migration options**:
- Keep soft-failure behavior for expected misses and add strict reason taxonomy.
- Convert invariant-breach paths to hard-failure exceptions.
- Standardize on explicit result types (`ok`, `reason_code`, `message`, `context`) instead of bare booleans.

---

## TD-004: All timestamps must be UTC

**Origin**: 7.3 (event dispatch types)
**Severity**: High
**Modules**:
- `nexus/strategy/signal.py`
- `nexus/infrastructure/praxis_connector/trade_outcome.py`
- `nexus/infrastructure/strategy_event.py`
- `nexus/core/capital_controller/reservation.py`
- `nexus/core/capital_controller/tracked_order.py`
- `nexus/infrastructure/praxis_connector/trade_command.py`

Current validation checks for tz-awareness (`tzinfo is not None`) but does not enforce UTC specifically. A timestamp with `tzinfo=+05:00` passes validation but violates the UTC-only convention.

**When to fix**: ASAP
**Migration**: Replace tz-awareness checks with explicit UTC checks (`timestamp.tzinfo == timezone.utc`).

---

## TD-005: StartupSequencer._register_with_trading is a stub

**Origin**: 9.1.3 (external integration stubs)
**Severity**: High (no Trading sub-system registration)
**Module**: `nexus/startup/sequencer.py`

`_register_with_trading()` logs a warning and does nothing. The Manager instance does not register with the Trading sub-system (Praxis), meaning Praxis has no knowledge of active Manager instances.

**When to fix**: When Praxis Connector is built.
**Migration**: Implement actual registration via Praxis Connector API. Remove this entry when done.

---

## TD-006: StartupSequencer._reconcile_capital is a stub

**Origin**: 9.1.3 (external integration stubs)
**Severity**: High (no capital reconciliation)
**Module**: `nexus/startup/sequencer.py`

`_reconcile_capital()` logs a warning and does nothing. Capital state is not reconciled against Trading sub-system positions on startup, meaning Manager may have stale or incorrect capital/position data.

**When to fix**: When Reconciler is built.
**Migration**: Implement actual reconciliation via Reconciler. Remove this entry when done.

---

## TD-008: StartupSequencer._replay_strategy_events is a stub

**Origin**: 9.1.5 (strategy state restoration)
**Severity**: Medium (strategy internal state not rebuilt from events)
**Module**: `nexus/startup/sequencer.py`

`_replay_strategy_events()` logs a warning and does nothing. Strategy events from WAL are not replayed to strategies for internal state rebuilding (actions discarded during replay).

**When to fix**: When event replay infrastructure is built.
**Migration**: Read STRATEGY_EVENT entries from WAL, dispatch to strategies with actions discarded. Remove this entry when done.

---

## TD-009: StartupSequencer._wire_predictor_fns is a stub

**Origin**: 9.1.6 (runtime setup stubs)
**Severity**: High (no signal subscription)
**Module**: `nexus/startup/sequencer.py`

`_wire_predictor_fns()` logs a warning and does nothing. Strategies are not subscribed to predictor functions, meaning no signals will be received.

**When to fix**: When predictor_fn subscription system is built.
**Migration**: Implement signal subscription wiring. Remove this entry when done.

---

## TD-010: StartupSequencer._register_timers is a stub

**Origin**: 9.1.6 (runtime setup stubs)
**Severity**: Medium (no timer callbacks)
**Module**: `nexus/startup/sequencer.py`

`_register_timers()` logs a warning and does nothing. Strategy timers are not registered, meaning on_timer callbacks will not fire.

**When to fix**: When timer system is built.
**Migration**: Implement timer registration. Remove this entry when done.

---

## TD-011: StartupSequencer._determine_mode always sets ACTIVE

**Origin**: 9.1.7 (startup dispatch)
**Severity**: Medium (no health-based mode selection)
**Module**: `nexus/startup/sequencer.py`

`_determine_mode()` always sets ACTIVE without checking health. Should set REDUCE_ONLY if health degraded, HALTED if critical.

**When to fix**: When health monitoring is built.
**Migration**: Implement health check and mode selection logic. Remove this entry when done.

---

## TD-012: OutboundConnector lacks register/deregister API

**Origin**: 9.2 (shutdown sequence planning)
**Severity**: Medium (no Trading sub-system lifecycle management)
**Module**: `nexus/infrastructure/praxis_connector/outbound_connector.py`

`OutboundConnector` protocol only defines `send_command()`. RFC-3001 specifies startup registration and shutdown deregistration with the Trading sub-system, but no API exists. Related to TD-005 (registration stub).

**When to fix**: When Praxis Connector integration is built.
**Migration**: Extend `OutboundConnector` protocol with `register(account_id)` and `deregister(account_id)` methods. Implement in concrete connector. Remove this entry when done.

---

## TD-013: ShutdownSequencer._stop_signals is a stub

**Origin**: 9.2.2 (shutdown sequence)
**Severity**: High (signals continue during shutdown)
**Module**: `nexus/startup/shutdown_sequencer.py`

`_stop_signals()` logs a warning and does nothing. Without unsubscribing from predictor_fns, new signals can arrive and trigger strategy callbacks during shutdown, causing race conditions. Blocked by TD-009 — cannot stop what was never wired.

**When to fix**: When predictor_fn subscription system is built (after TD-009).
**Migration**: Implement signal unsubscription. Remove this entry when done.

---

## TD-014: ShutdownSequencer._stop_timers is a stub

**Origin**: 9.2.2 (shutdown sequence)
**Severity**: Medium (timers continue during shutdown)
**Module**: `nexus/startup/shutdown_sequencer.py`

`_stop_timers()` logs a warning and does nothing. Without cancelling timers, on_timer callbacks can fire during shutdown, causing race conditions. Blocked by TD-010 — cannot stop what was never registered.

**When to fix**: When timer system is built (after TD-010).
**Migration**: Implement timer cancellation. Remove this entry when done.

---

## TD-015: ShutdownSequencer._submit_actions lacks Validator/Connector

**Origin**: 9.2.4 (shutdown sequence)
**Severity**: High (shutdown EXIT actions not submitted)
**Module**: `nexus/startup/shutdown_sequencer.py`

`_submit_actions()` filters actions to EXIT/ABORT but cannot validate or submit them. No ValidationPipeline or OutboundConnector is wired in. EXIT actions from on_shutdown are logged but not executed.

**When to fix**: When shutdown integration is built.
**Migration**: Add validator and connector parameters to ShutdownSequencer. Validate filtered actions through pipeline, submit valid ones via connector. Remove this entry when done.

---

## TD-016: ShutdownSequencer._wait_terminal is a stub

**Origin**: 9.2.5 (shutdown sequence)
**Severity**: High (no graceful position closure)
**Module**: `nexus/startup/shutdown_sequencer.py`

`_wait_terminal()` logs a warning and returns immediately. Submitted EXIT commands are not tracked to completion. Shutdown proceeds without confirming positions are closed.

**When to fix**: When TradeOutcome inbound integration is built.
**Migration**: Subscribe/poll for TradeOutcome until all commands reach terminal state. Implement timeout with ABORT escalation. Remove this entry when done.

---

## TD-017: StrategySpec allows whitespace-padded strategy_id

**Origin**: 9.2 review (manifest validation gap)
**Severity**: Medium (potential collision after normalization)
**Module**: `nexus/infrastructure/manifest.py`

`StrategySpec.__post_init__` validates that `strategy_id.strip()` is non-empty but does not normalize or reject surrounding whitespace. This permits entries like `'s1'` and `' s1 '` to both pass validation as distinct strategies. Downstream code (StartupSequencer, ShutdownSequencer, StrategyRunner) uses `.strip()` on lookup, causing these entries to collide silently — actions and state get attributed to the wrong strategy.

**When to fix**: Before multi-strategy deployments.
**Migration**: Tighten `StrategySpec.__post_init__` to either (a) strip-and-store the normalized value, or (b) reject strategy_id with leading/trailing whitespace. Validate uniqueness after normalization. Remove this entry when done.
