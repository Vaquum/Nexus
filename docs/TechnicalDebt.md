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

## TD-009: StartupSequencer._wire_sensors is a stub

**Origin**: 9.1.6 (runtime setup stubs)
**Severity**: High (no signal generation)
**Module**: `nexus/startup/sequencer.py`

`_wire_sensors()` logs a warning and does nothing. Limen Sensors are not trained or wired, meaning no signals will be generated.

**When to fix**: When Sensor training and signal generation are built (X.1.2.2+).
**Migration**: Implement Sensor training and timer-based predict loop. Remove this entry when done.

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

`_stop_signals()` logs a warning and does nothing. Without stopping Sensor timers, new signals can arrive and trigger strategy callbacks during shutdown, causing race conditions. Blocked by TD-009 — cannot stop what was never wired.

**When to fix**: When Sensor wiring is built (after TD-009).
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

**When to fix**: When Action dataclass has full fields (TD-023) and shutdown integration is built.
**Migration**: Add validator and connector parameters to ShutdownSequencer. Validate filtered actions through pipeline, submit valid ones via connector. Remove this entry when done.

---

## TD-016: _wait_terminal lacks ABORT escalation

**Origin**: 9.2.5 (shutdown sequence), updated X.1.4.2
**Severity**: Medium (timeout logs warning but does not force-close)
**Module**: `nexus/startup/shutdown_sequencer.py`

`_wait_terminal()` now polls PraxisInbound for terminal outcomes with a configurable timeout. However, when timeout expires with commands still pending, it only logs a warning. The RFC specifies ABORT escalation: remaining in-flight commands should be force-aborted via PraxisOutbound, then wait again with a shorter timeout. This requires Action fields (TD-023) to construct ABORT TradeCommands.

**When to fix**: When TD-023 (Action fields) is resolved.
**Migration**: On timeout, submit ABORT for each pending command via PraxisOutbound, then re-enter wait loop with shorter deadline.

---

## TD-017: StrategySpec allows whitespace-padded strategy_id

**Origin**: 9.2 review (manifest validation gap)
**Severity**: Medium (potential collision after normalization)
**Module**: `nexus/infrastructure/manifest.py`

`StrategySpec.__post_init__` validates that `strategy_id.strip()` is non-empty but does not normalize or reject surrounding whitespace. This permits entries like `'s1'` and `' s1 '` to both pass validation as distinct strategies. Downstream code (StartupSequencer, ShutdownSequencer, StrategyRunner) uses `.strip()` on lookup, causing these entries to collide silently — actions and state get attributed to the wrong strategy.

**When to fix**: Before multi-strategy deployments.
**Migration**: Tighten `StrategySpec.__post_init__` to either (a) strip-and-store the normalized value, or (b) reject strategy_id with leading/trailing whitespace. Validate uniqueness after normalization. Remove this entry when done.

---

## TD-018: Performance bottlenecks in O(N) Python loops and Decimal arithmetic

**Origin**: 10.1 (performance audit)
**Severity**: Medium (scaling risk)
**Modules**:
- `nexus/infrastructure/loss_derivation.py`
- `nexus/infrastructure/state_store.py`
- `nexus/infrastructure/wal.py`
- `nexus/core/validator/intake_stage.py`
- `nexus/core/capital_controller/capital_controller.py`

Several hot paths and recovery routines use linear O(N) scans and manual dictionary cleanups in pure Python, which will bottleneck as event volume and strategy counts scale. Specifically:
- `derive_rolling_losses` performs iterative `Decimal` arithmetic over all events in the WAL.
- `WriteAheadLog.read_all` (called by `StateStore.recover`) performs sequential record-by-record reads over the WAL file.
- `make_duplicate_order_hook` and `_purge_expired` perform full dictionary scans on every call to find stale entries.

**When to fix**: Before high-frequency trading (HFT) or large-scale multi-strategy deployments.
**Migration**:
- Replace O(N) dictionary/list scans with O(1) or O(log N) structures (e.g., `collections.deque` or `heapq` for expiration tracking).
- If `Decimal` precision is not required for a hot path, consider switching that path to float-based aggregation and NumPy; otherwise optimize the `Decimal` path by reducing repeated `Decimal` operations and avoiding full re-scans.
- Implement batch processing or incremental updates for rolling loss windows so recovery and loss derivation update aggregates from prior state instead of recalculating across the full WAL.

---

## TD-019: Cohort (multi-decoder aggregation) not supported

**Origin**: MMVP-X.1 signal flow design (X.1.2.1)
**Severity**: Medium (single-decoder Trainer path works, Cohort deferred)
**Module**: `nexus/startup/sequencer.py`

Nexus trains Sensors via `Trainer(experiment_dir).train(permutation_ids)` — this produces one Sensor per permutation ID from a single SFD experiment. Limen's Cohort system (RegimeDiversifiedOpinionPools) aggregates predictions across multiple decoders/regimes into a single callable, but Cohort is not yet ready in Limen.

When Cohort becomes available, Nexus must support a second path in the manifest where a strategy references a Cohort rather than individual Trainer permutations. The Cohort callable exposes the same `predict()` interface as Sensor, so the downstream dispatch (Signal → strategy) is unchanged.

**When to fix**: When Limen Cohort is production-ready.
**Migration**: Add `cohort` as an alternative to `experiment` + `permutation_ids` in the manifest `sensors` schema. Implement Cohort instantiation path in StartupSequencer alongside the existing Trainer path.

---

## TD-020: No experiment directory sandboxing per account

**Origin**: MMVP-X.1 manifest schema (X.1.2.1)
**Severity**: High (access control gap)
**Module**: `nexus/infrastructure/manifest.py`

`SensorSpec.experiment_dir` accepts any path on disk. A manifest can reference any experiment directory, regardless of which account ran that experiment. In a multi-account process, account A's manifest could point to account B's experiments, or to experiments the account owner never ran. There is no validation that an account is authorized to use a given experiment.

**When to fix**: Before multi-tenant or multi-account production deployment.
**Migration**: Introduce per-account experiment directory allowlists or a scoped base path per account (e.g. `{base}/{account_id}/experiments/`). Validate during manifest load that all `experiment_dir` paths fall within the account's allowed scope. Reject manifests that reference experiments outside the account's sandbox.

---

## TD-021: PredictLoop uses stub market data provider

**Origin**: MMVP-X.1 predict loop (X.1.2.4)
**Severity**: High (no real market data flows to Sensors)
**Module**: `nexus/strategy/predict_loop.py`

`PredictLoop` accepts a `market_data_provider: Callable[[int], pl.DataFrame]` that returns a rolling DataFrame of bars for a given kline_size. No concrete provider exists — the predict loop works but has nothing to call in production.

The concrete provider depends on Praxis TD-016 #3 (shared market data poller) which fetches klines per unique kline_size using `binancial.compute.get_spot_klines`. The kline_size for each sensor is in the Limen manifest's `data_source_config.params['kline_size']` — already extracted by `PredictLoop._extract_kline_size()`.

**When to fix**: When Praxis TD-016 #3 (shared market data poller) is built.
**Migration**: Implement the concrete market data provider that wraps the shared poller's rolling DataFrames. Wire it into PredictLoop construction during Nexus instance startup.

---

## TD-022: Sensor hot reload not implemented

**Origin**: MMVP-X.1 signal flow (X.1.2.6)
**Severity**: Medium (requires process restart to change sensors)
**Modules**: `nexus/startup/sequencer.py`, `nexus/strategy/predict_loop.py`

When the manifest changes experiment directories or permutation IDs, Sensors should be re-trained and the predict loop restarted without process restart. This requires: manifest file watching, diffing old vs new SensorSpecs, stopping the predict loop, re-running `_wire_sensors` with updated specs, restarting the loop with new WiredSensors. The RFC describes a full hot reload system with tier-1/tier-2/tier-3 change classification — none of this infrastructure exists yet.

**When to fix**: When manifest hot reload infrastructure is built.
**Migration**: Implement manifest file watcher, change diffing, and Sensor re-training via `importlib` reload. Integrate with PredictLoop start/stop lifecycle.

---

## TD-023: Action dataclass lacks trade fields

**Origin**: MMVP-X.1 command flow (X.1.3.3)
**Severity**: High (strategies cannot express tradeable decisions)
**Module**: `nexus/strategy/action.py`

`Action` only has `action_type` (ENTER, EXIT, MODIFY, ABORT). The RFC specifies additional fields required for trade execution: `direction` (BUY/SELL), `size` (base asset quantity), `execution_mode` (SingleShot, Bracket, TWAP, etc.), `order_type` (Market, Limit, etc.), `execution_params` (mode-specific), `deadline` (timeout seconds), `trade_id` (for EXIT/MODIFY/ABORT), `maker_preference`, `reference_price`. Without these fields, the Action → ValidationPipeline → TradeCommand → Praxis submission chain cannot function. The shutdown action submission (TD-015) and the live strategy action flow both depend on this.

**When to fix**: Before end-to-end strategy → trade execution.
**Migration**: Add RFC-specified fields to Action dataclass. Update ValidationPipeline to validate the new fields. Update `translate_to_trade_command` to map from the enriched Action.
