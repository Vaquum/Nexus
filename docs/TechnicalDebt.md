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

The increment `position.pending_exit += ctx.order_size` runs on the predict-loop thread without acquiring any lock. The decrement sites in `OutcomeProcessor._reduce_position` and `_clear_pending_exit` run on the OutcomeLoop thread, also without a lock. CPython's GIL makes single Decimal field assignment atomic but the read-then-validate-then-write pattern in `intake_stage` (`remaining = position.size - position.pending_exit`) is not atomic against a concurrent OutcomeLoop decrement.

Race direction analysis: the only way this race produces incorrect behavior is if the validator sees a STALE-HIGH `pending_exit` (because OutcomeLoop's decrement hasn't landed yet) and over-denies an EXIT that would otherwise fit. Stale-low cannot fire because pending_exit is monotonically built up by submit_actions before the validator reads it. The over-deny mode is operationally safe — strategies retry on next tick.

**When to fix**: When a future code path adds a write-write race on `pending_exit` (e.g. a second submit_actions caller on a different thread), OR when the validator's read-modify-write window is widened by additional logic that needs to see a consistent snapshot.

**Migration**: Either (a) add a per-position lock to Position and acquire it around all read/write sites; OR (b) document the predict-loop-thread-only-writer invariant and add a `threading.get_ident()` assertion in `submit_actions` to catch future violations; OR (c) move the increment-decrement pair into a single owner module (e.g. extend `OutcomeProcessor` with a `register_pending_exit` method called from the launcher's submitter closure).

## TD-042: MODIFY action runs CAPITAL/HEALTH/PRICE/RISK/PLATFORM_LIMITS validator stages

**Origin**: Round-12 multi-lens audit
**Severity**: Low (conservative; possibly too aggressive under degraded conditions)
**Module**: `nexus/core/validator/pipeline_executor.py:153-158`

`_should_bypass_stage` bypass set is `{EXIT, ABORT, CANCEL}` only. MODIFY falls through and runs CAPITAL / HEALTH / PRICE / RISK / PLATFORM_LIMITS — the same gates as a fresh ENTER. Conservative but possibly too aggressive when an operator's MODIFY only reduces qty or adjusts price downward to cut exposure under degraded health conditions.

**When to fix**: When a MODIFY-down-only path becomes operationally important (e.g., emergency-reduce flows that must succeed even when CAPITAL is exhausted).
**Migration**: Either (a) document the choice and accept the constraint, OR (b) extend the bypass set with a `MODIFY_REDUCE` action subtype that bypasses CAPITAL/RISK when qty/price decreases.

---

## TD-043: `_clear_pending_exit` clamps negative to zero with no diagnostic

**Origin**: Round-14 8-pass aggregation
**Severity**: Low (operational hygiene; hides cross-strategy or multi-pending bugs)
**Module**: `nexus/infrastructure/praxis_connector/outcome_processor.py:459`

`position.pending_exit = max(_ZERO, position.pending_exit - size)` silently absorbs any over-decrement (double-decrement, attribution bug, or a CANCEL for size 2 against pending_exit=1). No log when the clamp fires.

**When to fix**: When a future code path produces multi-pending EXITs on the same position and the clamp would mask attribution drift.
**Migration**: Add a WARNING log when `position.pending_exit - size < _ZERO` (mirror the `_adjust_strategy_deployed` pattern at `capital_controller.py:836`).

---

## TD-044: `pending_exit` increment not durably persisted at submission time

**Origin**: Round-14 8-pass aggregation
**Severity**: Low (paper-trade safe; tradeoff between durability and submission-path I/O)
**Module**: `nexus/strategy/action_submit.py:237-240`

The increment runs in-memory. Persistence happens only when a later outcome triggers `process_outcome`'s `append_mutation`. A crash between increment and the first outcome's `append_mutation` loses the increment. Strategies may then submit duplicate EXITs across crash boundaries because the validator's intake check sees `pending_exit=0` even though the venue order is live. The boot-time `pending_exit` reset (MAJOR-R) sweeps stale values, but doesn't help when the in-memory increment is lost.

**When to fix**: When the duplicate-EXIT-across-crash failure mode becomes operationally observable, OR when a Praxis-side reconcile that surfaces in-flight commands becomes the canonical post-crash recovery path.
**Migration**: Either (a) call `state_store.append_mutation(state)` immediately after the `pending_exit += ctx.order_size` (one extra fsync per submitted EXIT), OR (b) document that EXIT validation post-crash relies on Praxis-side venue state reconciliation rather than Nexus persistence.

---

## TD-045: `_handle_ack` returns `success=False` for EXIT/MODIFY → false-positive WARN stream

**Origin**: Round-14 8-pass aggregation
**Severity**: Low (log noise; no state corruption)
**Module**: `nexus/infrastructure/praxis_connector/outcome_processor.py:98-112`

EXIT/MODIFY actions don't reserve capital, so `_orders[command_id]` returns None; `order_ack` returns `INVARIANT_BREACH: order not found`. `_handle_ack` propagates `result.success=False` → the launcher's `process_outcome` logs `'OutcomeProcessor reported failure'` WARNING for every EXIT ACK. Now that MAJOR-M's `append_mutation` gate also reads `capital_updated`, distinguishing these expected failures from genuine ones in the log stream is harder.

**When to fix**: When operators surface log-noise complaints during paper-trade observation, OR when the WARN stream masks a real failure that needs attention.
**Migration**: Short-circuit `_handle_ack` to no-op + `success=True, capital_updated=False` when `outcome.command_id not in self._capital._orders`, OR route via `context.is_entry` to skip the capital lookup entirely for EXIT/MODIFY ACK.

---

## TD-046: `_handle_cancel` `remaining_size is None` fallback may over-clear `pending_exit`

**Origin**: Round-14 8-pass aggregation
**Severity**: Low (currently dead branch; latent under future translator changes)
**Module**: `nexus/infrastructure/praxis_connector/outcome_processor.py:213-219`

For EXIT cancels with prior PARTIAL fills, `_reduce_position` already decremented `pending_exit` by the partial. If `outcome.remaining_size` were None, the fallback uses `context.order_size` — over-decrementing by the already-applied partial amount. `_clear_pending_exit` clamps to zero so the field never goes negative for the immediate clear (TD-043 covers the silent clamp), but a concurrent EXIT on the same position would have its `pending_exit` silently zeroed.

Currently the branch is unreachable: `outcome_translator.py:303-310 _build_terminal` always sets `remaining_size = outcome.target_qty - outcome.filled_qty` for CANCELED/EXPIRED. Fragile in the face of future translator paths.

**When to fix**: When a future translator path or venue adapter emits CANCEL with `remaining_size=None`.
**Migration**: Drop the `None` fallback in the `clear_size` computation and raise explicitly, OR document the contract that `remaining_size` is always populated for CANCEL/EXPIRED of an EXIT.

---

## TD-047: `_handle_reject` for EXIT silently succeeds after `order_reject` fails

**Origin**: Round-14 8-pass aggregation
**Severity**: Low (currently expected; latent under future EXIT-with-reservation extension)
**Module**: `nexus/infrastructure/praxis_connector/outcome_processor.py:184-202`

EXIT actions don't reserve capital so `order_reject` returns `INVARIANT_BREACH`. The handler swallows that as success. `result.reason` is discarded with no log. If a future EXIT path ever DOES register an order_id (EXIT-with-reservation extension), legitimate reject failures would also be silently swallowed.

**When to fix**: When an EXIT-with-reservation extension is added (e.g. fee-reservation for live trading where exit fees are pre-funded).
**Migration**: Log `result.reason` at WARNING when `result.success is False and context.is_exit`; consider a sentinel result type that distinguishes "expected — no order tracked" from "real failure".

---

## TD-048: `_handle_fill` post-success exception leaves capital + position mutated, registry + WAL inconsistent

**Origin**: Round-14 8-pass aggregation
**Severity**: Low (rare; couples with TD-A on durability path)
**Module**: `nexus/infrastructure/praxis_connector/outcome_processor.py:163-170`

After `order_exit` succeeds and `_reduce_position` succeeds, an exception in `update_cumulative_realized_pnl` (line 163) or `append_event` (line 170) propagates back to the launcher's `process_outcome`. The terminal-cleanup block at `praxis/launcher.py:1547-1558` is gated on `outcome.outcome_type.is_terminal` AFTER `outcome_processor.process(...)` returns — exception unwinds before reaching it. Capital + position already mutated; WAL `append_mutation` (gated below) never runs because exception unwound before result return.

**When to fix**: When `update_cumulative_realized_pnl` or `append_event` becomes a real failure mode (e.g., disk-full on the spine path).
**Migration**: Wrap the post-success block in `_handle_fill` (lines 163-170) in try/except; on raise, still execute the cleanup + `append_mutation` paths, OR panic explicitly.

---

## TD-049: `_purge_expired` only runs lazily inside `check_and_reserve` and `send_order`

**Origin**: Round-14 8-pass aggregation
**Severity**: Low (self-healing on next strategy tick; idle-window hazard only)
**Module**: `nexus/core/capital_controller/capital_controller.py:238, 459`

No periodic invocation. If neither method is called for >30s (idle window: no new actions, no submissions), expired reservations remain in `_reservations` and `_state.reservation_notional` stays inflated. Self-heals on the next strategy tick because both call sites run `_purge_expired`.

**When to fix**: When idle-window reservation drift becomes operationally observable, OR when a deployment with sparse-tick strategies (e.g. daily-cadence) is added.
**Migration**: Add a periodic `_purge_expired` tick from a scheduler/timer loop (could piggyback on HealthLoop), OR document the lazy-cleanup invariant alongside the TTL bump (MAJOR-K).

---

## TD-050: `bridge_to_capital` helper defined but production launcher does not use it

**Origin**: Round-14 8-pass aggregation
**Severity**: Low (silent divergence risk — currently no divergence)
**Module**: `nexus/strategy/action_submit.py:262-359` (helper); `praxis/launcher.py:1466-1469` (open-coded equivalent)

The contract documented in `bridge_to_capital`'s docstring is replicated open-coded in the launcher. Any future safety check added to `bridge_to_capital` silently misses the production path because the launcher calls `capital_controller.send_order(...)` directly.

**When to fix**: Before adding any new safety check to `bridge_to_capital` that the production path must also enforce.
**Migration**: Route the launcher through `bridge_to_capital`, OR add a smoke test that asserts the launcher's behavior matches the helper's contract on a representative outcome shape.

---

## TD-051: Realized PnL excludes exit fees; latent inconsistency with future fee_rate change

**Origin**: Round-14 8-pass aggregation
**Severity**: Low (currently safe — couples with TD-030 in Praxis fee_rate=0)
**Module**: `nexus/infrastructure/praxis_connector/outcome_processor.py:400`

`realized_pnl = side_multiplier * (fill_price - entry_price) * fill_size` — gross PnL, no exit-fee subtraction. `outcome.actual_fees` is available but unused for EXIT. Documented at `capital_controller.py:608-614` (order_exit docstring) as a deliberate design choice. Combined with Praxis TD-030 (translator fee_rate=0), the gap is currently zero, but a future fee_rate change without updating `_reduce_position` would silently inflate `strategy_realized_pnl` by total exit fees.

**When to fix**: When Praxis TD-030 is addressed (fee_rate flips non-zero), OR when a deployment needs strict realized-PnL accounting.
**Migration**: Update `_reduce_position` to subtract `outcome.actual_fees` from `realized_pnl`, OR add a `realized_fees` ledger per the existing `order_exit` docstring suggestion.


---

## TD-054: `OrderContext.is_exit = not is_entry` partition is unsafe for MODIFY / CANCEL

**Origin**: Round-17 audit (R15 MAJOR-D + R16-races TD-009 carried forward as FINAL-TD-03)
**Severity**: Low (latent — bounded today by `_build_validation_context` returning None for MODIFY)
**Module**: `nexus/infrastructure/praxis_connector/outcome_processor.py:204-205, 229-236`; `nexus/infrastructure/praxis_connector/order_context.py:108-114`; cross-repo: `praxis/launcher.py:1574-1581, 478-486` (current MODIFY rejection); `praxis/launcher.py:734-744` (`_build_order_context` discriminant)

`_handle_reject` / `_handle_cancel` branch on `context.is_exit` and call `_clear_pending_exit`. Any future MODIFY context (today a dead branch via `_build_validation_context` early-return) would silently decrement an unrelated EXIT's `pending_exit`. `_clear_pending_exit`'s clamp-to-zero hides the decrement when `pending_exit` is small or zero.

**When to fix**: Before any code path emits MODIFY or any non-ENTER, non-EXIT action (e.g. AMEND, CANCEL_ORDER). MMVP scope does not require it.
**Migration**: Add an explicit `action_type` discriminant on `OrderContext` so MODIFY does not fall into the EXIT branch; also makes `_clear_pending_exit`'s silent clamp explicit.

---

## TD-055: `CapitalController._orders` not WAL-persisted; mid-boot outcome can hit INVARIANT_BREACH

**Origin**: Round-17 audit (R15 TD-002 carried forward as FINAL-TD-04)
**Severity**: Low (Praxis-truth wins on next `pull_positions`; OK at MMVP scope)
**Module**: `nexus/core/capital_controller/capital_controller.py:64-83, 111-116`; `nexus/infrastructure/wal_codec.py` (no codec for `_orders`)

`CapitalController._orders` is in-memory only. After a crash mid-run with in-flight orders, an outcome arriving in the brief boot startup window between `_reconcile_capital` finishing and `outcome_loop.start()` being fully wired can hit `INVARIANT_BREACH` because the `_orders` skeleton is empty.

**When to fix**: Before long-running deployments without rapid Praxis pull-positions cadence, OR when a boot-window incident is observed.
**Migration**: Boot-time replay of `CommandAccepted` events from spine to rebuild `_orders` skeleton; substantial work with low expected value at MMVP.

---

## TD-056: Rolling-loss enforced at instance level not per-strategy

**Origin**: Round-17 audit (R15 TD-004 carried forward as FINAL-TD-05)
**Severity**: Low (operator-feature gap, not defect)
**Module**: `nexus/core/validator/risk_stage.py:124-141`; `nexus/core/domain/risk_state.py:168-184`

`validate_risk_stage` reads aggregate `RiskCheckMetrics`. Rolling-loss limits are enforced at the instance level, not per-strategy, even though `StrategyRiskState` per-strategy fields exist and `_update_strategy_risk_state` writes to them. A strategy that has never traded gets blocked when a sibling strategy hits the rolling-loss limit.

**When to fix**: When operator wants per-strategy isolation; conservative-failure-mode (over-deny rather than under-deny) acceptable at MMVP.
**Migration**: Add a per-strategy `risk_stage` variant; gate it behind a config flag that defaults to instance-level for MMVP.

---

## TD-057: `_build_exit_context` TOCTOU on `state.positions`

**Origin**: Round-17 audit (R16-races TD-008 carried forward as FINAL-TD-06)
**Severity**: Low (bounded reachability; self-corrects on the next tick)
**Module**: `nexus/core/validator/intake_stage.py` (verify `_build_exit_context` call site); `nexus/infrastructure/praxis_connector/outcome_processor.py:443-449` (concurrent `_reduce_position` `del`)

`_build_exit_context` checks `if trade_id in state.positions` then reads `position = state.positions[trade_id]` without `positions_lock`. Concurrent `_reduce_position` `del state.positions[trade_id]` between the two reads raises `KeyError`; the action is dropped at the validator layer.

**When to fix**: When the single-tick drop becomes operationally observable. The audit anticipated FINAL-MAJOR-02 would close this transitively, but the M02 fix scoped `state.risk.lock` to the risk dict only — it did not extend `positions_lock` coverage to `_build_exit_context`'s reads.
**Migration**: Acquire `positions_lock` around the existence check + dereference in `_build_exit_context`, OR catch `KeyError` and fall back to the missing-trade_id branch.

---

## TD-058: `ShutdownSequencer._halt_state_mode` writes `state.mode` without `HealthLoop._lock`

**Origin**: Round-17 audit (R17-A TD-053 carried forward as FINAL-TD-07)
**Severity**: Low (in-flight-tick race; PT-FIX-42 narrows the window)
**Module**: `nexus/startup/shutdown_sequencer.py:189-208`; `nexus/core/health_loop.py:160-172`

`ShutdownSequencer._halt_state_mode` writes `state.mode = ModeState(...)` on the shutdown thread without `HealthLoop._lock`. If a HealthLoop tick is mid-flight at `health_loop.py:160` (after `_lock` acquired, just before the write), the LAST writer wins and HALTED can be overwritten. PT-FIX-42's `_running` re-check narrows but does not close the in-flight-tick race.

**When to fix**: When operator-observable mode-loss occurs at shutdown. The audit anticipated FINAL-MAJOR-02 would close this transitively, but the M02 fix scoped `state.risk.lock` to the risk dict only — `state.mode` is not covered.
**Migration**: Acquire `HealthLoop._lock` (or a shared mode lock) in `_halt_state_mode` before the write so the HealthLoop tick's mode write cannot overwrite the persisted HALTED.

---

## TD-059: `WriteAheadLog.truncate_keeping_events` rewrites in-place without temp+rename

**Origin**: Round-17 audit (R17-D TD-PR-B carried forward as FINAL-TD-08)
**Severity**: Low (durability hole; NOT corruption — `_find_valid_end` + CRC are robust)
**Module**: `nexus/infrastructure/wal.py:294-303`; `nexus/infrastructure/snapshot.py:46-51` (snapshot already uses tmp+rename — this should mirror)

A crash mid-rewrite loses preserved STRATEGY_EVENTs, leaving rolling losses under-counted (over-permissive on rolling-loss gates) until manual repair. Truncate window is bounded (tight loop, completes in milliseconds for typical event counts). Failure direction is over-permissive on rolling-loss gates rather than over-restrictive.

**When to fix**: Self-contained one-line-ish fix; ship at convenience.
**Migration**: Mirror `save_snapshot`'s pattern — write to `wal.bin.tmp`, fsync, atomic rename.

---

## TD-060: `state.capital.fee_reserve` never re-derived from any source on boot

**Origin**: Round-17 audit (R17-D TD-PR-C carried forward as FINAL-TD-09)
**Severity**: Low (testnet `fee_rate=0` so unobserved at MMVP today; bounded mainnet failure mode)
**Module**: `nexus/infrastructure/wal_codec.py:124, 149`; `nexus/startup/sequencer.py:358-491`; `nexus/core/capital_controller/capital_controller.py:117-202, 812-815`; `nexus/infrastructure/state_store.py:112-161`

`state.capital.fee_reserve` is persisted but never re-derived from any source on boot. If a STATE_MUTATION is lost (TD-052 / FINAL-MAJOR-04 windows), `fee_reserve` is durably understated, increasing the chance of `EXPECTED_MISS` denials on future fee-deficit fills.

**When to fix**: When monetary conservation becomes load-bearing (mainnet).
**Migration**: Same pattern as TD-052 — extend STRATEGY_EVENT to carry the contributing fee delta; re-derive on recovery.

---

## TD-061: `Position.entry_price` / `avg_cost_basis` not aligned with venue tick / lot step

**Origin**: Round-17 audit (R16-N TD-NB carried forward as FINAL-TD-10)
**Severity**: Low (none of the targeted Binance USDT pairs has steps coarser than 6 decimals)
**Module**: `nexus/core/domain/position.py:50-55`; `nexus/strategy/action_submit.py:243-246`

`Position.entry_price` and `avg_cost_basis` are stored at Decimal default precision (28 digits), not aligned with venue `tick_size` / `lot_size_step_size`. Persisted values can carry more precision than the venue accepts on the next EXIT submission.

**When to fix**: Before adding any low-precision symbol; same fix root as FINAL-MAJOR-06 / FINAL-MAJOR-09 (quantize at venue lot-step granularity).
**Migration**: Quantize at write time using the venue's `lot_size_step_size`.

---

## TD-062: `make_duplicate_order_hook` casts `duplicate_window_ms / 1000` to `float`

**Origin**: Round-17 audit (R16-N TD-NC carried forward as FINAL-TD-11)
**Severity**: Low (at-boundary fires fewer than 1-in-2^52)
**Module**: `nexus/core/validator/intake_stage.py:137, 178`

The cast to `float` makes the comparison at line 178 flip near boundaries by a float ULP. Operational impact bounded; one-line fix.

**When to fix**: At convenience.
**Migration**: Replace `float` with `Decimal(duplicate_window_ms) / Decimal(1000)` and keep the comparison in Decimal.

---

## TD-063: `check_and_reserve` divides `order_notional / capital_pool` without `capital_pool == 0` guard

**Origin**: Round-17 audit (R16-N TD-NF carried forward as FINAL-TD-12)
**Severity**: Low (today unreachable; `CapitalState.__post_init__` rejects `capital_pool <= 0` but the field is mutable)
**Module**: `nexus/core/capital_controller/capital_controller.py:296-298, 325`

`check_and_reserve` divides `order_notional / capital_pool` for `allocation_pct` without guarding `capital_pool == 0`. Today unreachable because `CapitalState.__post_init__` rejects `capital_pool <= 0`, but the field is mutable.

**When to fix**: Before adding any code path that mutates `capital_pool` post-init.
**Migration**: Freeze the field (frozen=True dataclass), OR re-validate on mutation.

---

## TD-064: `_compute_exit_cost_basis` silently skips on `avg_cost_basis == 0`

**Origin**: Round-17 audit (R17-C TD-NM-C + R17-B TD-LC-A carried forward as FINAL-TD-13)
**Severity**: Low (today reachable only via the `_ensure_entry_position` placeholder, which is gated by the `size==0` EXIT denial at intake_stage.py:255-261; healed at next boot's `_reconcile_capital`)
**Module**: `nexus/infrastructure/praxis_connector/outcome_processor.py:319, 391-453`; `nexus/core/domain/position.py:55, 86-88`; `nexus/startup/shutdown_sequencer.py:673-738`; cross-repo: `praxis/launcher.py:679-686` (placeholder source of zero-acb)

`_compute_exit_cost_basis`'s strict equality `position.avg_cost_basis == _ZERO` check silently SKIPS the capital decrement on a position whose `avg_cost_basis` is exactly zero, while `_reduce_position` proceeds to mutate position state and write STRATEGY_EVENT. Capital aggregate stays inflated until next boot's `_reconcile_capital` heals it.

**When to fix**: Before any code path emits an EXIT against a zero-acb placeholder (defensive).
**Migration**: Replace silent skip with explicit fallback (`avg_cost_basis = entry_price`) or raise a hard error so the bad state is visible.

---

## TD-065: Imported SELL position would write wrong-signed `realized_pnl` (latent / non-MMVP)

**Origin**: Round-17 audit (R16-N MAJOR-NB downgraded per R17-C addendum §4)
**Severity**: Low (latent / non-MMVP under current spot-only Praxis `pull_positions` behaviour)
**Module**: `nexus/infrastructure/praxis_connector/outcome_processor.py:417-418`; `nexus/core/validator/intake_stage.py:222-229`; `nexus/startup/sequencer.py:293-301, 344-354`

`_reduce_position` honors `OrderSide.SELL` with sign-flipped P&L, but the validator only gates SUBMITTED ACTIONS, not IMPORTED positions. Combined with `_import_praxis_position` accepting any side from Praxis, an imported short position would write WRONG-SIGNED `realized_pnl` to `strategy_realized_pnl`, `cumulative_realized_pnl`, and the rolling-loss windows.

**When to fix**: BEFORE Praxis can import SELL positions from any configured venue / account mode (margin, futures, or any future spot venue path that surfaces short positions). Re-elevate to MAJOR at that point.
**Migration**: Gate `_resolve_imported_position_fields` on `side=BUY` for spot deployments AND tighten the EXIT validator to additionally require `position.side == OrderSide.BUY`.

---

## TD-066: `_dispatch_shutdown` reads Position references after `positions_lock` release

**Origin**: Round-17 audit (R15 TD-006 carried forward as FINAL-TD-16)
**Severity**: Low (bounded reachability — both join-timeout AND on_shutdown reads multi-field Position data)
**Module**: `nexus/startup/shutdown_sequencer.py:270-301`

`_dispatch_shutdown` reads Position references after `positions_lock` release. `Position` is mutable; a still-alive OutcomeLoop after timed-out join can write `size` / `entry_price` / `avg_cost_basis` / `pending_exit` mid-`on_shutdown`. Reachability requires both `_stop_outcome_loop` join timeout AND a strategy whose `on_shutdown` reads multi-field Position data.

**When to fix**: When operator-observable mid-shutdown data tear becomes reachable. The audit anticipated FINAL-MAJOR-02 would close this transitively, but M02's `state.risk.lock` is scoped to the risk dict only; `_dispatch_shutdown`'s Position-field reads remain outside any held lock.
**Migration**: Hold `positions_lock` across the snapshot iteration AND the per-strategy `on_shutdown` callback dispatch (or copy each Position to a frozen snapshot under the lock before releasing).


## TD-067: `recover_orphaned_order` ships with no production caller

**Origin**: pr-prep Greybeard pre-PR review (round-17)
**Severity**: Low (defense-in-depth helper for FINAL-MAJOR-01; planned cross-repo wiring)
**Module**: `nexus/core/capital_controller/capital_controller.py:957-994`

`CapitalController.recover_orphaned_order(order_id, outcome_type)` was added as a defense-in-depth helper for FINAL-MAJOR-01: when the launcher's `process_outcome` hits the no-OrderContext terminal cleanup branch, the helper releases the orphan order's capital aggregates. The helper is currently unreferenced — the Praxis launcher's terminal-no-context branch (`praxis/launcher.py:1559-1568`) does not call it. Dead code at ship time.

**When to fix**: When the Praxis launcher follow-up wires the helper into `process_outcome`'s terminal-no-context branch (separate cross-repo PR per the round-17 audit issue).
**Migration**: Praxis-side: in `process_outcome`'s terminal-cleanup block, call `capital_controller.recover_orphaned_order(outcome.command_id, outcome.outcome_type.value)` for terminal outcomes.

---

## TD-069: `StrategyEvent.outcome_id` empty-string is the dedup-skip sentinel

**Origin**: pr-prep Greybeard pre-PR review (round-17)
**Severity**: Low (legacy v1-codec compat hack)
**Module**: `nexus/infrastructure/strategy_event.py:38`; `nexus/infrastructure/loss_derivation.py:_dedup_by_outcome_id`

`StrategyEvent.outcome_id: str = ''` defaults to the empty string. `_dedup_by_outcome_id` treats the empty string as "legacy event, do not dedup" so v1-codec-decoded events pass through unfiltered. A future writer that accidentally produces an empty `outcome_id` (instead of a real id) silently bypasses dedup with no signal — the magic-empty-string sentinel hides the misuse.

**When to fix**: When the v1 event codec is removed (no more legacy events to dedup-skip), OR when a third sentinel state would be useful (e.g. "explicitly opted-out of dedup").
**Migration**: Change the field to `outcome_id: str | None = None` so the type system catches accidental misuse; update `_dedup_by_outcome_id` to check `is None` instead of empty-string falsy.

---

## TD-070: `_decode_event_v1` and `_decode_event_v2` are near-duplicates

**Origin**: pr-prep Greybeard pre-PR review (round-17)
**Severity**: Low (two decoders is fine for two versions; refactor when v3 lands)
**Module**: `nexus/infrastructure/wal_codec.py:430-481`

`_decode_event_v1` and `_decode_event_v2` differ only in whether `outcome_id` is read from the dict. Two near-identical decoders means two places to update on the next schema bump. Pattern scales linearly with codec versions; not yet load-bearing.

**When to fix**: Before adding a v3 event codec.
**Migration**: Extract a common decoder that takes a list of optional fields with defaults; each version function becomes a thin wrapper that supplies the version-appropriate field set.

---

## TD-071: `state_store.recover()` writes to `srs.high_water_mark` and `srs.strategy_realized_pnl` outside any lock

**Origin**: pr-prep Greybeard pre-PR review (round-17)
**Severity**: Low (single-threaded at boot per StartupSequencer phase ordering — convention drift, not a race)
**Module**: `nexus/infrastructure/state_store.py:201-211`

`recover()` runs as part of `StartupSequencer._recover_state` which executes single-threaded before any loops start, so the unlocked writes are safe by construction today. But every other write to these fields (in `_update_strategy_risk_state`, `refresh_rolling_losses`) is lock-protected. The convention drift would bite if any future refactor ever calls `recover()` mid-run (e.g. for soft re-recovery or debugging).

**When to fix**: When any code path can call `recover()` with worker threads alive (currently impossible by sequencer construction).
**Migration**: Wrap the per-strategy write loop in `state.risk.lock_cm()` (no-op when lock is None, which is the current boot path).

---

## TD-072: `recover()` overwrites `strategy_realized_pnl` from WAL events alone, losing pre-snapshot cumulative beyond the 30-day retention window

**Origin**: PR #55 round-3 review
**Severity**: Major (silently understates `cumulative_realized_pnl`, equity, and drawdown derivatives over time)
**Module**: `nexus/infrastructure/state_store.py:204`, `nexus/infrastructure/snapshot.py:52-53`

`recover()` line 204 unconditionally overwrites `srs.strategy_realized_pnl = derived_pnl.get(sid, _ZERO)` where `derived_pnl` is computed from `STRATEGY_EVENT` entries currently in the WAL. `save_snapshot` truncates the WAL keeping only events within `_EVENT_RETENTION_DAYS = 30` of the snapshot time. Result: any realized P&L that contributed to the persisted snapshot but whose underlying events are now older than 30 days is dropped from the post-recovery `strategy_realized_pnl`. The instance-level `cumulative_realized_pnl` derived from the per-strategy sum at line 213 inherits the same understatement, and downstream `recompute_drawdown_metrics` sees inflated equity / understated drawdown for the rest of the process lifetime.

The behavior is a regression from the FINAL-TD-01 fix, which was specifically designed to re-derive `strategy_realized_pnl` from events to avoid losing a single STATE_MUTATION-side delta on crash between `append_event` and `append_mutation`. The trade-off was made without persisting a snapshot watermark, so the current code chooses "lose a single recent delta on crash" → "lose all events older than 30 days every recover".

**When to fix**: Before any deployment whose lifetime exceeds the 30-day retention window or whose drawdown / equity gates are load-bearing on cumulative P&L accuracy. For paper-trade MMVP at testnet cadence the impact is bounded by the test session length (typically << 30 days), so the regression is dormant.

**Migration**: Persist a snapshot watermark (sequence number or timestamp) inside the snapshot payload. On `recover()` (a) adopt `srs.strategy_realized_pnl` from the snapshot as the baseline, (b) replay only `STRATEGY_EVENT` entries with sequence > watermark to add post-snapshot deltas. Combined with the existing v2 `outcome_id` dedup this preserves both the "no lost delta on crash" guarantee FINAL-TD-01 introduced AND the "no silent 30-day truncation drift" guarantee the snapshot was supposed to provide.

---

## TD-073: `state.risk.lock` held across WAL fsync / full-scan creates validator-latency spikes

**Origin**: PR #55 round-8 review
**Severity**: Low at MMVP testnet cadence (event count low, fsync fast on local SSD); Major at sustained mainnet cadence with large WAL retention
**Module**: `nexus/infrastructure/state_store.py:269-285` (`refresh_rolling_losses`); `nexus/infrastructure/praxis_connector/outcome_processor.py:181-197` (`_handle_fill`)

The PR #55 round-7 fix held `state.risk.lock` across two slow operations to close a read-overwrite race:
1. `_handle_fill` holds the lock across `_update_strategy_risk_state_locked` + `update_cumulative_realized_pnl` + `append_event` (synchronous WAL append + fsync).
2. `refresh_rolling_losses` holds the lock across `read_events()` (full WAL scan + decode) + derivation + per-strategy write.

The validator's `to_risk_check_metrics()` and `intake_stage` action validation also acquire `state.risk.lock`. Result: every exit-fill validator path waits for an fsync, and every refresh tick blocks all action validation for the full WAL-scan duration. On a 30-day retained WAL with material event volume the refresh stall could exceed the validator's tick budget.

The trade-off was deliberate: pre-fix the refresher could read stale WAL events, derive stale rolling losses, and overwrite a freshly-applied OutcomeProcessor write. Closing the race the simple way meant accepting the lock-held-across-IO cost.

**When to fix**: Before any deployment whose validator-tick budget is load-bearing AND whose retained WAL grows past a few thousand events.

**Migration**: Two viable approaches:
- **Generation counter**: snapshot WAL sequence under a short-held lock, derive losses without the lock, re-acquire and write only if no new event was appended since the snapshot. Adds an `_event_sequence_counter` field on StateStore protected by `_wal_lock`.
- **Delta-only refresh**: refresher ONLY decays losses out of the rolling window (subtracts events that aged out), never re-derives totals. OutcomeProcessor remains the only producer of additions. Eliminates the race-vs-latency conflict by construction at the cost of a per-event timestamp check during decay.

---

## TD-074: `StrategyEvent.outcome_id` empty-string default lets producers silently fall back to legacy v1 codec

**Origin**: PR #55 round-8 review
**Severity**: Low (only one production producer today, OutcomeProcessor, which always populates)
**Module**: `nexus/infrastructure/strategy_event.py:39`

`StrategyEvent.outcome_id: str = ''` defaults to empty so legacy v1-decoded events deserialize cleanly. Side effect: any new production producer that constructs a `StrategyEvent` without populating `outcome_id` silently emits a v1-encoded payload (per `serialize_event`'s conditional dispatch), bypassing dedup during recovery. The producer mistake won't fail fast — it will manifest later as duplicate P&L / rolling-loss accounting on Praxis re-deliveries.

Today's only production producer (`OutcomeProcessor._handle_fill` line 184) always sets `outcome_id=outcome.outcome_id`, so the failure mode is dormant. But the risk grows with every new producer added.

**When to fix**: Before adding any second production producer of `StrategyEvent`, OR before any operator-observable double-counting incident traces back to a producer-mistake.

**Migration**: Split the dataclass:
- `LegacyStrategyEvent` (no `outcome_id` field) — used only by `_decode_event_v1` for legacy WAL replay. No dedup contract.
- `StrategyEvent` (`outcome_id: str` required, no default) — used by all production producers and `_decode_event_v2`. Producer mistakes fail at construction time.
- `derive_rolling_losses` accepts both via a small adapter that maps `LegacyStrategyEvent` → `StrategyEvent('')` only for the dedup-skip path.

---

## TD-075: `_handle_fill` partial-rollback gap — capital + position mutations not protected by the round-10 append-first contract

**Origin**: PR #55 round-14 review
**Severity**: Low at MMVP testnet cadence (WAL append failures are rare; capital aggregates self-heal at boot via `_reconcile_capital`); Major for any deployment where mid-run consistency between in-memory state and WAL is load-bearing
**Module**: `nexus/infrastructure/praxis_connector/outcome_processor.py:147-208`

The PR #55 round-10 fix inverted the order inside the `state.risk.lock_cm()` block to "append first, then mutate risk state" so a `StateStore.append_event` raise leaves the **risk fields** in sync with the WAL. But round-10 only protected steps inside that block. The earlier mutations in `_handle_fill` are NOT covered:

1. `self._capital.order_fill(...)` / `order_exit(...)` mutate capital aggregates (`working_order_notional`, `position_notional`, `per_strategy_deployed`, `fee_reserve`) before the risk-lock block.
2. `_grow_position` / `_reduce_position` mutate position fields (`size`, `entry_price`, `avg_cost_basis`) before the risk-lock block.
3. The risk-lock block then calls `append_event`. If it raises, steps 1-2 already happened; in-memory state has the fill applied; WAL does not.

Failure-mode propagation:
- Capital aggregates: self-heal on next boot via `_reconcile_capital` (Praxis is the source of truth — Nexus adopts Praxis position+capital reconciliation at startup).
- Position fields: `size` cross-checks during reconcile, but `entry_price` and `avg_cost_basis` are not part of Praxis's surface — they would drift permanently across the failure-without-restart window.
- Shutdown path: `_apply_terminal_outcome` swallows the exception and continues, so the final snapshot can be inconsistent with what was actually processed (no rollback fires before the snapshot is taken).

**When to fix**: Before any deployment whose WAL is on flaky storage, OR whose mid-run `entry_price` / `avg_cost_basis` accuracy is load-bearing for strategy logic between boots.

**Migration**: Refactor `_handle_fill` to compute-then-append-then-mutate:
- Extract `_compute_position_mutation(outcome, context) → PositionMutation` (pure, no side effects) returning the new size / entry_price / avg_cost_basis values + the realized_pnl for exit fills.
- Extract `_compute_capital_delta(outcome, context) → CapitalDelta` (pure) returning the aggregate adjustments.
- Order: build StrategyEvent with the computed `realized_pnl`, acquire `state.risk.lock_cm()`, `append_event(event)` first (raises bubble up with no mutation), then apply `CapitalDelta` (under capital lock, brief), then apply `PositionMutation` (under positions lock, brief — same lock as risk.lock by FINAL-MAJOR-02 wiring), then apply risk update. All three in-memory mutations are pure dict / Decimal ops that cannot raise; if they do, that's a programmer error, not a recoverable I/O failure.

---

## TD-076: No end-to-end test composes EXIT fill loss → rolling-loss update → submit_actions → RISK-stage rejection

**Origin**: Round-18 codex-supervised audit (Pass 5)
**Severity**: Low (component tests cover each step; integration gap only)
**Module**: tests (no existing end-to-end rolling-loss test); production paths verified clean in audit

Component tests cover (a) loss recording on EXIT fill, (b) `derive_rolling_losses` from WAL events, (c) validator RISK stage threshold checks. No integration test composes the full chain: EXIT fill loss → `_update_strategy_risk_state_locked` → `state_store.append_event` → `refresh_rolling_losses` → `submit_actions(ENTER)` → `validate_risk_stage` rejection with correct reason code. A future refactor that breaks any link could pass CI while breaking the rolling-loss enforcement contract.

**When to fix**: Before any refactor of the rolling-loss derivation path, OR before relying on rolling-loss caps as a primary risk control in production.
**Migration**: Add a single integration test that drives an EXIT FILL outcome through `OutcomeProcessor`, ticks `HealthLoop.refresh_rolling_losses`, and asserts the next `submit_actions(ENTER)` for that strategy is rejected with `RISK_ROLLING_LOSS_*` reason code. Pre-fix the test should pass; the assertion is regression-only.

---

## TD-077: Persisted/manual HALTED mode is not sticky across restart because HealthLoop can overwrite it

**Origin**: Round-18 codex-supervised audit (Pass 5)
**Severity**: Low (validator risk-stage enforcement is independent of mode; mode is operator-facing only)
**Module**: `nexus/core/health_loop.py` (HealthLoop overrides `state.mode` from snapshot); `nexus/startup/sequencer.py:710-745` (`_determine_mode` at boot)

Distinct from TD-058 (which covers the shutdown-time race on `_halt_state_mode` write). This TD is about post-restart behavior: a manual or persisted HALTED mode in the snapshot is overwritten by HealthLoop's first tick if `HealthSnapshot` evaluates healthy. The validator's risk-stage rejection of breached strategies is independent of `state.mode`, so trading safety is preserved; but operator-mode workflows (e.g., "I HALTED this account by hand, expect it to stay HALTED across restart") will surprise.

**When to fix**: When operator-mode workflows are formalized, OR when manual HALTED is used as a deployment guard that must survive restart.
**Migration**: Persist a `mode_lock` flag alongside `state.mode` in the snapshot. HealthLoop checks the flag and refuses to demote/promote when set. Operator clears the flag explicitly via runbook step or admin endpoint.

---

## TD-078: Boot reconciliation keeps existing Nexus `avg_cost_basis` even when Praxis has fresher position truth

**Origin**: Round-18 codex-supervised audit (Pass 4)
**Severity**: Low (size/existence reconciled; cost basis used only for PnL attribution and EXIT cost-basis-released math)
**Module**: `nexus/startup/sequencer.py:358-484` (`_reconcile_capital`)

Distinct from TD-024 (which covers Praxis-only-position imports). This TD is about positions present in BOTH repos with diverging cost basis: `_reconcile_capital` rebuilds size/existence from Praxis truth but does NOT overwrite `avg_cost_basis` for already-present Nexus positions. After a missed fill (e.g., MAJOR-004 outcome dropped), the position exists in both repos but the Nexus `avg_cost_basis` is stale. Strategy decisions and PnL attribution drift; `_compute_exit_cost_basis` decrements the wrong notional on the next EXIT.

**When to fix**: Alongside MAJOR-004 (outcome delivery best-effort) — once outcome delivery is reliable, this TD's exposure shrinks. Otherwise before any deployment where PnL attribution accuracy matters across restarts.
**Migration**: During reconcile, compare Praxis `avg_entry_price` against Nexus `avg_cost_basis`; on disagreement either (a) overwrite Nexus to Praxis truth (simpler, accepts Praxis as the source of truth), or (b) re-derive from spine FillReceived events for that trade_id (more precise but heavier).

---

## TD-079: No architectural guard against future direct `PraxisOutbound.send_command` callers

**Origin**: Round-18 codex-supervised audit (Pass 7)
**Severity**: Low (current callers are correct; surface area is small)
**Module**: `nexus/strategy/action_submit.py:236` and `nexus/startup/shutdown_sequencer.py:435` are the only production callers

The validator chain has exactly two production entry points to `praxis_outbound.send_command`: `submit_actions` (validator-gated) and `ShutdownSequencer._submit_exit` (intentional bypass). Code review is the only barrier to a third caller appearing. No import-restriction lint, no architectural test asserting these are the only references.

**When to fix**: Before any expansion of the validator-bypass surface, OR alongside any refactor that exposes `PraxisOutbound` more broadly.
**Migration**: Add a lightweight architectural test (`tests/test_no_direct_send_command.py`) that uses `ast.parse` (or `tldr impact`) on `nexus/` to assert only `nexus/strategy/action_submit.py` and `nexus/startup/shutdown_sequencer.py` reference `PraxisOutbound.send_command`. Failure points to a regression that needs review.

---

## TD-080: `ShutdownSequencer.shutdown()` does not guarantee `_final_checkpoint` if an earlier step raises

**Origin**: Round-18 codex-supervised audit (Pass 9)
**Severity**: Low (no current bug; defense-in-depth gap)
**Module**: `nexus/startup/shutdown_sequencer.py:215-236`

Only `_final_checkpoint` is wrapped in try/except inside `shutdown()`. Steps 1-9 (`_halt_state_mode` through `_persist_strategy_state`) propagate uncaught exceptions. A regression in any of those steps (e.g., a refactor introducing AttributeError in `_dispatch_save`, or a per-strategy file write failure in `_persist_strategy_state` outside its per-blob try/except) skips `_final_checkpoint`. Recovery loads the previous snapshot, losing every since-then risk-state mutation. The launcher's outer try/except in `_run_nexus_instance` catches the exception but does not invoke `_final_checkpoint` on the way out.

**When to fix**: Before the next `shutdown_sequencer` refactor, OR alongside any change that adds a new step to `shutdown()`.
**Migration**: Wrap steps 1-9 in `try: ... finally: try: _final_checkpoint() except: log; _deregister()`. Add a regression test that injects a raise in `_persist_strategy_state` and asserts `state_store.checkpoint` is called once.

---

## TD-081: `_persist_strategy_state` and `_final_checkpoint` block on disk fsync without timeout

**Origin**: Round-18 codex-supervised audit (Pass 9)
**Severity**: Low (requires disk pathology; container orchestrators typically force-kill after grace period)
**Module**: `nexus/startup/shutdown_sequencer.py:835-940`; `nexus/infrastructure/snapshot.py:33-53`

`_persist_strategy_state` writes per-strategy blobs with `os.fsync(fd)` and parent dir fsync; no timeout. `_final_checkpoint` calls `state_store.checkpoint` → `save_snapshot` → tmp.write_bytes + fsync + tmp.replace + fsync_directory + wal.truncate_keeping_events. No timeout anywhere. A degraded or unresponsive disk (NFS hang, hardware fault) blocks shutdown indefinitely. Container orchestrators (Render, k8s) typically issue SIGTERM then SIGKILL after a grace period; if the grace period is shorter than the fsync hang, the process is force-killed mid-fsync — same as a SIGKILL crash. WAL torn-tail handling recovers cleanly, so this is not data loss, but produces user-visible "stuck shutdown" behavior.

**When to fix**: When the deployment's grace period is tightened past observed fsync latency, OR when shutdown observability becomes load-bearing.
**Migration**: Light TD or runbook. Document the grace-period requirement (snapshot fsync can take O(state size) time on slow storage). Add an alert if shutdown exceeds N seconds. A more invasive fix would run fsync in a background thread with a parent-side timeout, but fsync semantics make timeout-and-abandon risky.

---

## TD-082: Validator's `platform_limits_stage` does not include Binance symbol-filter checks

**Origin**: Round-18 codex-supervised audit (Pass 10)
**Severity**: Low (BinanceAdapter `_validate_order` covers compliance after capital reservation)
**Module**: `nexus/core/validator/platform_limits_stage.py`; cross-repo: `praxis/infrastructure/binance_adapter.py:944-983`

`PlatformLimitsStageLimits` has no fields for Binance LOT_SIZE, MIN_NOTIONAL, or PRICE_FILTER; `validate_platform_limits_stage` has no codes for venue-filter violations. Symbol-filter compliance is delegated entirely to `BinanceAdapter._validate_order`, which runs AFTER capital reservation, AFTER spine append, AFTER TradingState mutation. A future Praxis variant or a new venue adapter without `_validate_order` would silently submit invalid orders. Cross-cutting venue-filter compliance is not enforced at the validator boundary.

**When to fix**: Alongside MAJOR-007 (filter ValueError orphan) — both fixes need to coordinate on where symbol-filter compliance is enforced. OR before adding a non-Binance venue adapter.
**Migration**: Add a new validator stage (e.g., `VENUE_FILTERS`) or extend `PlatformLimitsStageLimits` with a venue-filter dataclass populated at boot from `BinanceAdapter._filters`. Enforces violations BEFORE capital reservation, freeing the validator to reject without leaking capital and without consuming venue rate budget.

---

## TD-083: `OutcomeProcessor` dedup `_dedup_lock` is performative under concurrent callers

**Origin**: Greybeard / Copilot pre-PR review of round-18 MAJOR-004 part A
**Severity**: Low (single-thread OutcomeLoop makes this unreachable today); Major if a future cross-thread caller (e.g., Praxis-driven retry path bypassing OutcomeLoop) appears
**Module**: `nexus/infrastructure/praxis_connector/outcome_processor.py:107-149`

`process(outcome, context)` acquires `_dedup_lock` to read `_processed_outcome_ids`, releases it, runs the work (capital + position + WAL append + risk update), then re-acquires the lock to add the `outcome_id`. Two concurrent `process()` calls for the same `outcome_id` would both pass the membership check (set still empty), both run the work, and both add — double-mutation. Currently unreachable because the launcher's per-Nexus `OutcomeLoop` is the sole caller and runs single-threaded; the runtime invariant that protects against this is implicit, not asserted.

**When to fix**: Before adding any cross-thread caller of `OutcomeProcessor.process` (e.g., Praxis-driven runtime retry from a different thread), OR before MAJOR-004 part B's planned boot replay path lands and starts driving `process` from `Trading.start` while `OutcomeLoop` may also be running.

**Migration**: Either (a) hold `_dedup_lock` for the entire `process` call so the check-and-add is atomic with the work — straightforward but slow if multiple per-account processors share contention (current per-account isolation makes this fine); or (b) add an explicit `assert threading.get_ident() == self._caller_thread_id` (or equivalent single-thread guard) that fails loud if the invariant is ever broken; document the contract in the `process` docstring either way.

---

## TD-084: `OutcomeProcessor._processed_outcome_ids` set grows unbounded for the process lifetime

**Origin**: Greybeard pre-PR review of round-18 MAJOR-004 part A
**Severity**: Low (paper-trade rates ~300 outcomes/day → ~100k entries/year; bounded but unbounded in principle)
**Module**: `nexus/infrastructure/praxis_connector/outcome_processor.py:71`

`_processed_outcome_ids: set[str]` accumulates one entry per successful `process()` call and is never pruned. Same family as TD-020 (`command_strategy_ids`), TD-023 (`_accepted_commands` / `_command_trade_ids`), TD-033 (Praxis ExecutionManager registries), TD-048 (translator state). At MMVP testnet rates the long-run footprint is negligible (~1 MB/year); over multi-day paper-trade or production runs it warrants pruning.

**When to fix**: Before sustained multi-day paper-trade or any production run where the per-process registry footprint is observable.

**Migration**: Replace the `set[str]` with an `OrderedDict[str, None]`-backed LRU cap (use `OrderedDict.move_to_end` on hit, `popitem(last=False)` to evict on insert past the cap). Cap at e.g. 100k recent outcomes — large enough to cover any plausible Praxis retry / boot-replay window, small enough to stay bounded. Evicted-then-replayed outcomes would re-process (acceptable given dedup is best-effort within the process lifetime; cross-restart safety lives on the Praxis side via `OutcomeAcked`).

---

## TD-085: `submit_actions` validator-exception path can leak a granted reservation

**Origin**: PR #57 review (round-18 post-merge follow-up)
**Severity**: Low (very low probability — stages are designed to return decisions, not raise)
**Module**: `nexus/strategy/action_submit.py:175-190`

The `try: validator.validate(ctx) except Exception` branch logs and marks the action `SUBMIT_FAILED` without calling `_release_granted_reservation`. If a stage callable raises (rather than returning a denied `ValidationDecision`) AFTER the CAPITAL stage has already granted a reservation, the reservation parks in `_reservations` until 30s TTL eviction. Same family as round-18 MAJOR-006, but fired through the exception path rather than the REJECTED path.

The pipeline executor at `nexus/core/validator/pipeline_executor.py:42-78` does NOT catch exceptions from stage callables — it propagates them to the `submit_actions` exception handler. The `Pipeline.validate` re-attach behavior at lines 65-72 only triggers when a stage RETURNS a denied decision; an exception bypasses that path entirely, so the granted `Reservation` is never re-attached to a `decision` object — there is no `decision` for the SUBMIT_FAILED branch to release from.

Closing this requires the pipeline executor to track granted reservations independently of the per-stage decision return, OR `submit_actions` to track the partial validator state via instrumentation. Both are bigger than a one-line fix.

**When to fix**: When the validator stage surface area grows (e.g., a new `OperationalMode` transition or a new platform-limit derivation that has a non-trivial chance of raising), OR when sustained mainnet operation makes the 30s TTL window observable.

**Migration**: Either (a) extend `Pipeline.validate` to return a structured intermediate state on exception (e.g., wrap the propagated exception with the granted reservation context so the caller can release), or (b) reshape `Pipeline.validate` to catch stage exceptions itself, release any granted reservation via an injected `capital_controller` reference, and re-raise — concentrates the rollback contract in one place. Option (b) is the cleaner long-term shape; option (a) is the more conservative migration.

---

## TD-086: `OutcomeProcessor.process` records `outcome_id` only after successful return — a raise mid-`_handle_fill` leaves capital/position mutated AND the dedup set empty

**Origin**: Copilot PR #57 review (post-merge follow-up to round-18 MAJOR-004 part A)
**Severity**: Low today (currently unreachable — append_event raise + Praxis retry of the exact same outcome_id is narrow); High if MAJOR-004 part B (Praxis boot replay-from-spine) lands without a paired fix here
**Module**: `nexus/infrastructure/praxis_connector/outcome_processor.py:71-149` (`process`); `nexus/infrastructure/praxis_connector/outcome_processor.py:167-252` (`_handle_fill`)

`process()` adds `outcome.outcome_id` to `_processed_outcome_ids` only after the per-outcome handler returns a `success=True` `ProcessResult`. `_handle_fill` mutates capital (`order_fill` / `order_exit`) and position (`_grow_position` / `_reduce_position`) BEFORE calling `state_store.append_event`. If `append_event` raises (transient I/O / WAL validation failure — covered by `TestExitFillWalAppendFailureRollback`), the exception propagates out of `process()`, the `outcome_id` is never recorded, and a retry of the exact same outcome will re-enter `_handle_fill`. The retry-side behavior diverges by direction:

- **EXIT FILL**: `_compute_exit_cost_basis` re-reads `position.avg_cost_basis` and `position.size`. If `_reduce_position` already deleted the position, the helper returns None (no second capital decrement) but `_reduce_position` then raises `RuntimeError('exit fill for missing position')`. If the position is not yet closed, the helper computes a fresh decrement against the now-smaller size and `_reduce_position` reduces the size again — capital and position both double-mutated.
- **ENTRY FILL**: `order_fill` runs again on the same `command_id` → capital deployed is double-decremented; `_grow_position` runs again → position size grows by 2× the fill_size. No append_event in this path, so the original raise can only come from `order_fill` itself, but the dedup-after-success contract is still broken.

The existing `TestExitFillWalAppendFailureRollback` only verifies that risk state is unmutated; it does NOT cover capital/position rollback or retry safety. The `_dedup_lock` referenced in TD-083 is a separate concurrency concern; this TD is about ordering of side-effects vs. dedup-set commit.

**When to fix**: Before MAJOR-004 part B (Praxis boot replay-from-spine) lands and starts driving `process()` from the boot path while `OutcomeLoop` may also be running — the boot replay greatly increases the probability of a same-`outcome_id` re-entry into `process` (a snapshot crash mid-`_handle_fill` is now reachable through normal recovery, not just disk pathology).

**Migration**: Two complementary changes:

1. **Reorder `_handle_fill` so I/O that can raise happens BEFORE state mutation.** For EXIT FILL: build the `StrategyEvent` and call `state_store.append_event` (under `risk.lock`) BEFORE `order_exit` and `_reduce_position`. The risk-state mutation already happens after the append today — extend the same discipline to capital and position. The per-method assertions about read-modify-write ordering need to be re-checked to ensure no further reads-then-writes hide a race (the existing positions_lock contract should still hold).
2. **Mark `outcome_id` as in-flight before mutation begins, committed after.** Add a second lock-guarded set `_in_flight_outcome_ids: set[str]`. On entry: if `outcome_id in _processed OR outcome_id in _in_flight` → dedup hit. On entry: add to `_in_flight`. On success: move to `_processed`. On exception: leave in `_in_flight` (forces operator-visible alerting; never silently re-runs the failed-mid-mutation work). Operator-driven recovery would then explicitly re-process by clearing `_in_flight` — manual intervention is the right gate when state has already partially mutated.

Both changes together close the window. Option (1) alone is preferred where feasible because it is purely structural (no new state, no new operator surface).

**Acceptance addendum (codex-supervised audit re-run, 2026-05-04)**:
- Coverage must include **ENTRY FILL** replay idempotency, not only **EXIT FILL** `append_event` failure. **ENTRY FILL** is the most frequent outcome shape and has no built-in WAL-event guard.
- Coverage must include `append_mutation` failure followed by later `_final_checkpoint` (clean-shutdown variant where in-memory mutation is persisted to snapshot even though `OutcomeAcked` was withheld).
- Neither of the two migration options listed above (reorder I/O before mutation; in-memory `_in_flight_outcome_ids` set) currently produces a `recover()`-observable signal that an outcome was applied. For the cross-restart paired-boundary use with Praxis TD-052, the migration must additionally include a durable applied-outcome marker — either by extending option (2) so the `_processed_outcome_ids` set is persisted to the WAL on the success-side commit (not just held in memory after the in-flight handoff), OR by adding a third migration step that writes an outcome-applied record to the snapshot/WAL on `append_mutation`. Whichever shape is chosen, `StateStore.recover()` (Nexus-side) must reconstruct the applied-outcome marker into a `_processed_outcome_ids`-equivalent set on the rebuilt `OutcomeProcessor`. The dedup happens server-side inside Nexus when Praxis TD-052 replay re-delivers a `TradeOutcome` (which arrives at `OutcomeProcessor.process` carrying its Nexus-derived `outcome_id`) that is already in the recovered set — Praxis itself does not read Nexus state.
- TD-086 must NOT be deferred past the landing of Praxis TD-052 (paired implementation boundary): TD-052 boot replay-from-spine cannot safely run while `OutcomeProcessor.process` retains the dedup-after-success contract.
