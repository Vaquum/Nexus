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

## TD-009: ~~StartupSequencer._wire_sensors is a stub~~ RESOLVED

**Status**: Implemented in v0.25.0 (X.1.2.2). `_wire_sensors()` trains Limen Sensors via `Trainer(experiment_dir).train(permutation_ids)` and stores `WiredSensor` entries.

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

~~`_stop_signals()` logs a warning and does nothing.~~ RESOLVED

**Status**: Implemented in v0.25.0 (X.1.2.5). `_stop_signals()` calls `PredictLoop.stop()` to cancel all sensor timers before shutdown proceeds.
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
- ~~Replace O(N) dictionary/list scans~~ RESOLVED in v0.26.0 (X.2.3.1) — `_purge_expired` uses heapq, `make_duplicate_order_hook` uses deque.
- `derive_rolling_losses` Decimal arithmetic is already O(n) single-pass with early exits. Float aggregation rejected — RFC requires Decimal precision for financial calculations. No further optimization needed unless event volume exceeds 100k per recovery.
- `WriteAheadLog.read_all` reads sequentially with length-prefixed records — no skip-ahead possible without reading headers. Memory-mapping doesn't help for variable-length records. Marginal optimization; real fix is incremental updates (below) that avoid full WAL reads.
- ~~Incremental rolling loss updates~~ RESOLVED by TD-002 (X.1.1.4) — snapshot preserves rolling losses, `truncate_keeping_events` retains post-checkpoint events only, recovery re-derives from delta events not full WAL.

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

---

## TD-024: Reconciliation cannot import Praxis-only positions

**Origin**: MMVP-X.1 capital reconciliation (X.1.5.2)
**Severity**: Medium (detected but not resolved)
**Module**: `nexus/startup/sequencer.py`

`_reconcile_capital()` detects positions that exist in Praxis but not in Nexus (logged as warnings). However, it cannot import them because Praxis `Position` has no `strategy_id` — Nexus requires `strategy_id` to assign positions to strategies for capital tracking, risk limits, and P&L attribution. The `trade_id → strategy_id` mapping only exists when Nexus originally submitted the command.

This affects crash recovery where Nexus state is lost but Praxis still holds positions, and phantom position detection (RFC misfire handling).

**When to fix**: Before production crash recovery or multi-strategy deployments.
**Migration**: Either (a) add `strategy_id` passthrough to Praxis Position (Praxis stores what Nexus sends in trade metadata), or (b) maintain a persistent `trade_id → strategy_id` mapping in Nexus WAL that survives state loss.

---

## TD-025: PredictLoop and TimerLoop use threading.Timer per tick (thread churn)

**Origin**: MMVP-X.1 predict loop (X.1.2.4), MMVP-X.2 timer loop (X.2.1.1)
**Severity**: Low (correct but wasteful at scale)
**Modules**: `nexus/strategy/predict_loop.py`, `nexus/strategy/timer_loop.py`

`threading.Timer` fires once and creates a new thread per fire. Both loops reschedule by creating a new Timer at the end of each tick. With many sensors/timers or short intervals, this causes thread churn — continuous thread creation and teardown. A single persistent thread per loop with `time.sleep` or `threading.Event.wait(timeout)` would be more efficient.

**When to fix**: Before HFT or high-sensor-count deployments.
**Migration**: Replace per-tick `threading.Timer` with a single scheduler thread per loop that sleeps between fires. Maintain the same lock-guarded `_running` check pattern.

---

## TD-026: Health snapshot has no live data source

**Origin**: MMVP-X.2 health evaluation (X.2.2)
**Severity**: High (mode determination always defaults to ACTIVE without real health data)
**Module**: `nexus/startup/sequencer.py`

`_determine_mode()` evaluates a `HealthSnapshot` against `HealthThresholds` to set operational mode. The evaluation logic works, but there is no mechanism to populate `HealthSnapshot` with real health data from Praxis. Health signals (latency, consecutive failures, failure rate, rate limit headroom, clock drift) must come from the Trading sub-system via `PraxisInbound` or a dedicated health channel.

Without a live health source, `_determine_mode()` defaults to ACTIVE at startup, and there is no periodic re-evaluation during runtime (the RFC requires continuous health monitoring via `CONTINUOUS_LIMIT_EVAL_INTERVAL_SECONDS`).

**When to fix**: When Praxis TD-016 exposes health signals.
**Migration**: Add a health signal delivery mechanism from Praxis (either via the outcome queue or a separate channel). Implement periodic health re-evaluation in the Nexus instance thread. Update mode on each evaluation and trigger mode transitions (ACTIVE → REDUCE_ONLY → HALTED) with alerts.

---

## TD-027: Bare `assert` in `OutcomeProcessor` fill handlers

**Origin**: Round-7 audit (Praxis issue #77)
**Severity**: Major (only fires under `python -O`)
**Module**: `nexus/infrastructure/praxis_connector/outcome_processor.py:116-119, 232-235, 268-269`

`_handle_fill`, `_grow_position`, and `_reduce_position` use `assert` to guard against `None` fill fields (`fill_notional`, `fill_size`, `fill_price`, `actual_fees`). Python `-O` strips `assert` statements; under `python -O`, a malformed PARTIAL/FILLED outcome with `None` fields raises `TypeError` deep inside capital arithmetic instead of returning a controlled `ProcessResult(success=False)`. `OutcomeLoop._dispatch`'s broad except catches and drops it silently. Production trading systems rarely use `python -O`, but the silent-failure mode is unsafe.

**When to fix**: Before any deployment that might inadvertently use `python -O` (e.g., CI artifacts, container images that strip bytecode for size).
**Migration**: Replace each `assert X is not None` with explicit `if X is None: return ProcessResult(success=False, ...)`.

---

## TD-028: Loop `.stop()` methods cancel future ticks but do not join in-flight callbacks

**Origin**: Round-7 + Round-8 audits (Praxis issue #77)
**Severity**: Major (degraded shutdown only)
**Modules**: `nexus/core/outcome_loop.py:140-147`, `nexus/strategy/predict_loop.py:stop`, `nexus/strategy/timer_loop.py:stop`, `nexus/core/health_loop.py:78-85` (the HealthLoop case is also tracked separately as PT-FIX-42 because it directly defeats PT-FIX-25's HALTED flip)

**Round-7 surface (OutcomeLoop):** `OutcomeLoop.stop()` signals the worker but does not block; if the worker doesn't terminate within `join_timeout=5.0`, the log-and-keep path leaves it alive. During `_submit_actions` a concurrent FILL processed by the still-live worker can remove a position from `state.positions` between `_build_exit_context` (`.get()`-checks the position) and `_build_exit_order_context` (uses `.get()` post-PT-FIX-36). The latter raises `ValueError`, the surrounding `try/except ValueError` catches it, and the shutdown EXIT command_id is appended to `_submitted_command_ids` without an entry in `_exit_contexts`. When the FILLED outcome later arrives, `_apply_terminal_outcome` finds `_exit_contexts.get(command_id) is None` and silently no-ops — the shutdown EXIT's fill is not applied to state.

**Round-8 surface (PredictLoop / TimerLoop):** Same cancel-but-no-join pattern. Their `.stop()` methods cancel future-scheduled timers but do not join the currently-executing callback. A predict tick or timer callback dispatched just before `.stop()` can still be running while the shutdown sequencer mutates `state.positions` (via `_apply_terminal_outcome` → `OutcomeProcessor._reduce_position` → `del self._state.positions[trade_id]`). `state.positions` is a plain dict with no lock; concurrent structural mutation while the predict callback iterates `state.positions.values()` inside `context_provider` is undefined under CPython's GIL for iteration.

**When to fix**: Before any deployment where shutdown can race with active fills, predict ticks, or timer callbacks.

**Migration**: Either (a) make every loop's `.stop()` block until the in-flight callback actually terminates (raise on timeout so shutdown aborts loudly), or (b) wrap `state.positions` in a lock that all callers acquire (extends the launcher's existing `positions_lock` from PT-FIX-28 into Nexus state mutations as well), or (c) take the cleanup-then-mutation approach: before `_submit_actions`, ensure ALL background loops have terminated by joining their threads with explicit timeouts and raising on overrun.

**Round-9 refinement** (related but distinct): when `_build_exit_order_context` raises `ValueError` (via the `.get()` + None check from PT-FIX-36), `_submit_exit` catches the exception but leaves the just-appended `command_id` in `_submitted_command_ids`. `_wait_terminal` then waits for that command's terminal outcome and times out (no `OrderContext` stored → `_apply_terminal_outcome` silently no-ops on the eventual fill). The dropped fill is BENIGN at the state level (the position was already absent — that's why `_build_exit_order_context` raised) but causes a wait-then-timeout penalty in shutdown latency. Quick fix: in the `except ValueError` block of `_submit_exit`, also `_submitted_command_ids.remove(returned_id)` so `_wait_terminal` doesn't include it.

---

## TD-029: `_grow_position` defensive gap on `trade_id=None`

**Origin**: Round-6 audit (Praxis issue #77)
**Severity**: Major (defensive — current launcher prevents)
**Module**: `nexus/infrastructure/praxis_connector/outcome_processor.py:230-260`

`OrderContext.trade_id` is typed `str | None`; `_grow_position` raises `RuntimeError('entry fill without trade_id')` when None. The current launcher (PT-FIX-20) always populates `trade_id` for ENTER via `forced_trade_id=outcome.command_id`, so this never fires in practice. A future launcher rewrite or alternative caller could trigger the crash; capital state would also diverge because `_capital.order_fill` runs first and commits before the position mutation is attempted.

**When to fix**: Before any launcher refactor that could relax the trade_id-is-always-set invariant.
**Migration**: Either tighten `OrderContext.trade_id: str` (no None) and have ENTER always allocate one, or rework `_handle_fill` so the position mutation runs BEFORE the capital commit (no commit if mutation fails).

---

## TD-030: `_poll_until_terminal` misleading double-dispatch structure

**Origin**: Round-6 audit (Praxis issue #77)
**Severity**: Low (maintenance hazard, no current bug)
**Module**: `nexus/startup/shutdown_sequencer.py:_poll_until_terminal`

The PT-FIX-38 implementation has two `_apply_terminal_outcome` call sites: one for `is_fill` (PARTIAL + FILLED) and one for `is_terminal and not is_fill` (REJECTED/EXPIRED/CANCELED). The control flow is correct (no double-dispatch on FILLED) but the dual-call structure is fragile — a future modification to `_apply_terminal_outcome`'s gate could expose double-decrement.

**When to fix**: Next time `_apply_terminal_outcome` is touched.
**Migration**: Restructure to a single `_apply_terminal_outcome` call with an explicit `should_apply: bool` flag computed once.

---

## TD-031: `PraxisOutbound` trade_id/command_id contract not asserted

**Origin**: Round-6 audit (Praxis issue #77)
**Severity**: Low (contract risk; verified safe today)
**Module**: `nexus/infrastructure/praxis_connector/praxis_outbound.py:79-116`

`send_command` passes `trade_id=command.trade_id or command.command_id` to Praxis's `submit_fn` and uses the returned `command_id` as the round-trip key. Praxis (`praxis/core/execution_manager.py:634`) generates its own UUID and returns it — so the Nexus `command_id` and Praxis `command_id` are distinct, and the Nexus side correctly uses the returned Praxis ID throughout. The contract is implicit in the Praxis implementation and not asserted in either side. A future Praxis change that uses the input `trade_id` as its returned `command_id` would silently break the round trip.

**When to fix**: Before any change to Praxis's `submit_command` ID-generation logic.
**Migration**: Add an assertion in `send_command` that the returned `command_id != command.trade_id` for ENTER actions, OR document the contract on the `PraxisOutbound.send_command` signature.

---

## TD-032: `on_startup` `capital_available` shows gross budget, not net of deployed

**Origin**: Round-7 audit (Praxis issue #77)
**Severity**: Low (cosmetic; capital stage validator catches over-reservation)
**Module**: `nexus/startup/sequencer.py:741`

`_dispatch_startup` builds `StrategyContext.capital_available = manifest.capital_pool * spec.capital_pct / _HUNDRED` — this is the gross strategy budget, not net of `state.capital.position_notional`. On crash-recovery boot with open positions, the strategy's `on_startup` callback receives an inflated capital figure. Any ENTER sized against it correctly fails the capital stage validator, but the strategy's view is misleading at the most consequential boot callback.

**When to fix**: Before strategies make decisions based on `capital_available` in `on_startup`.
**Migration**: Compute `capital_available = max(strategy_budget - per_strategy_deployed.get(strategy_id, _ZERO), _ZERO)` and pass that.

---

## TD-033: WAL file TOCTOU between `StateStore.__init__` and `recover()`

**Origin**: Round-9 audit (Praxis issue #77)
**Severity**: Low (multi-process scenario only — single-process paper-trade is safe)
**Module**: `nexus/infrastructure/state_store.py:57-61, 112-137`

`StateStore.__init__` initialises `self._sequence` from `read_safe()` on the existing WAL (line 61). `recover()` re-reads the same WAL and overwrites `self._sequence` at line 137 only when `wal_entries` is non-empty. The two reads are separate `stat`+`open` pairs with no lock between them. Single-process paper trade is safe (no concurrent writer). In a multi-process scenario where two Manager instances share a WAL path, a write between the two reads can cause `_sequence` to be one step ahead of what `recover()` will see, producing a sequence-number gap. `validate_magic` running between the two reads can also see a different file size than `read_safe` did.

**When to fix**: Before any deployment that runs multiple Manager instances against a shared state directory (current single-instance paper-trade design forbids this, but the constraint is documentation-only).

**Migration**: Either (a) hold a fcntl/flock around both reads in `__init__` + `recover()`, OR (b) read the WAL once into memory at `__init__` and have `recover()` consume the cached entries instead of re-reading, OR (c) document that a `state_dir` MUST be exclusive to a single Manager instance and add a startup lock-file check that errors out loudly if a sibling holds the lock.

---

## TD-040: WAL codec legacy snapshot decodes `avg_cost_basis` from `entry_price` (entry fees lost)

**Origin**: BLOCKER-A pre-PR review (this branch)
**Severity**: Low (one-cycle bounded; eliminated as positions cycle)
**Module**: `nexus/infrastructure/wal_codec.py:_decode_position`

Pre-fix `Position` had no `avg_cost_basis` field. Snapshots written before this PR's BLOCKER-A change carry only `entry_price` (fill-price VWAP, fees excluded). On boot the decoder defaults `avg_cost_basis = entry_price` for missing-key cases. Subsequent EXIT FILLs on these legacy positions decrement `position_notional` by `entry_price * fill_size` instead of the full cost basis (which would include entry fees), so the entry-fee portion is never released back to available capital.

Self-corrects as positions cycle: every fresh ENTER FILL via `_grow_position` populates `avg_cost_basis` correctly (`(old_size * old_avg_cost_basis + fill_notional + actual_fees) / new_size`), so any new position has the correct cost basis from the first fill.

**When to fix**: When a deployment carries a long-lived position across the v0.32.0 → v0.33.0 boundary AND the per-position entry-fee leak is large enough to matter. For paper-trade testnet at MMVP scope, the fee leak per position is ~0.1% of notional and bounded by position lifetime.

**Migration**: Write a one-time migration script that scans the snapshot for positions with avg_cost_basis equal to entry_price (best-effort heuristic; entry-fee data is unrecoverable from the snapshot alone) and either flags them for operator review or estimates the true avg_cost_basis from the WAL strategy events.

---

## TD-041: `Position.pending_exit` increment in `submit_actions` lacks lock protection

**Origin**: BLOCKER-C pre-PR review (this branch)
**Severity**: Low (race direction is safe today)
**Module**: `nexus/strategy/action_submit.py` (post-`send_command` increment)

The increment `position.pending_exit += action.size` runs on the predict-loop thread without acquiring any lock. The decrement sites in `OutcomeProcessor._reduce_position` and `_clear_pending_exit` run on the OutcomeLoop thread, also without a lock. CPython's GIL makes single Decimal field assignment atomic but the read-then-validate-then-write pattern in `intake_stage` (`remaining = position.size - position.pending_exit`) is not atomic against a concurrent OutcomeLoop decrement.

Race direction analysis: the only way this race produces incorrect behavior is if the validator sees a STALE-HIGH `pending_exit` (because OutcomeLoop's decrement hasn't landed yet) and over-denies an EXIT that would otherwise fit. Stale-low cannot fire because pending_exit is monotonically built up by submit_actions before the validator reads it. The over-deny mode is operationally safe — strategies retry on next tick.

**When to fix**: When a future code path adds a write-write race on `pending_exit` (e.g. a second submit_actions caller on a different thread), OR when the validator's read-modify-write window is widened by additional logic that needs to see a consistent snapshot.

**Migration**: Either (a) add a per-position lock to Position and acquire it around all read/write sites; OR (b) document the predict-loop-thread-only-writer invariant and add a `threading.get_ident()` assertion in `submit_actions` to catch future violations; OR (c) move the increment-decrement pair into a single owner module (e.g. extend `OutcomeProcessor` with a `register_pending_exit` method called from the launcher's submitter closure).
