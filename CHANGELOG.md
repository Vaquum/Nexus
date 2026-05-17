# Changelog

## v0.1.0 on 16th of March, 2026

- Add CI pipeline mirroring Praxis: Ruff, Mypy strict, pytest, CodeQL workflows
- Add `pytest>=8.0` and `mypy>=1.10` as dev dependencies
- Add strict Ruff linting and Mypy configuration in [`pyproject.toml`](pyproject.toml)
- Add `.github/CODEOWNERS` with `@zero-bang`
- Add `nexus/` package with empty `__init__.py`
- Add [`test_placeholder.py`](tests/test_placeholder.py) with `import nexus` smoke test
- Update project metadata to `vaquum-nexus`
- Remove template `tests/run.py`

## v0.2.0 on 17th of March, 2026

- Add module structure for all RFC components: `core/domain/`, `core/validator/`, `core/capital_controller/`, `infrastructure/`, `infrastructure/praxis_connector/`, `strategy/runner/`, `reconciler/`, `trail/`
- Add `structlog>=24.0` and `orjson>=3.10` as runtime dependencies
- Add [`observability.py`](nexus/infrastructure/observability.py) with `configure_logging`, `bind_context`, `clear_context`, `get_logger`
- Add [`instance_config.py`](nexus/instance_config.py) with frozen `InstanceConfig` dataclass (identity + capital ceiling)
- Add [`test_observability.py`](tests/test_observability.py) with 10 tests covering JSON output, context binding, level filtering, and stdlib integration
- Add [`test_instance_config.py`](tests/test_instance_config.py) with 7 tests covering creation, immutability, and validation
- Add `nexus-journals/` to `.gitignore`
- Wire `nexus/__init__.py` public API exports

## v0.3.0 on 17th of March, 2026

- Add [`capital_state.py`](nexus/core/domain/capital_state.py) with mutable `CapitalState` dataclass and derived `available` property
- Add [`enums.py`](nexus/core/domain/enums.py) with `OperationalMode`, `OrderSide`, `BreachLevel`
- Add [`instance_state.py`](nexus/core/domain/instance_state.py) composing all state with `from_config` factory
- Add [`operational_mode.py`](nexus/core/domain/operational_mode.py) with `ModeState` and `StrategyModeState` (composes `ModeState`)
- Add [`position.py`](nexus/core/domain/position.py) with mutable `Position` dataclass (trade_id, strategy_id, symbol, side, size, entry_price, unrealized_pnl, pending_exit)
- Add [`risk_state.py`](nexus/core/domain/risk_state.py) with `RiskState` and `StrategyRiskState` (instance-level losses derived from per-strategy state)
- Add [`nexus/core/domain/__init__.py`](nexus/core/domain/__init__.py) re-exports
- Add 34 tests covering enums, position, capital state, risk state, operational mode, and instance state composition

## v0.4.0 on 18th of March, 2026

- Add `msgpack>=1.0` as runtime dependency
- Add [`wal_entry.py`](nexus/infrastructure/wal_entry.py) with `WALEntryType` enum (SNAPSHOT, STATE_MUTATION, STRATEGY_EVENT) and frozen `WALEntry` dataclass
- Add [`wal_codec.py`](nexus/infrastructure/wal_codec.py) with explicit per-type `serialize_state` / `deserialize_state` for InstanceState via msgpack, codec version embedding
- Add [`wal.py`](nexus/infrastructure/wal.py) with `WriteAheadLog` — append-only binary log with 8-byte magic header, per-record CRC32 integrity, length-prefixed msgpack records, fsync durability
- Add [`snapshot.py`](nexus/infrastructure/snapshot.py) with atomic `save_snapshot` (tmp+rename+fsync, WAL truncation) and `load_snapshot`
- Add [`state_store.py`](nexus/infrastructure/state_store.py) with `StateStore` facade — manages `snapshots/` and `wal/` subdirectories, `checkpoint`, `append_mutation`, `recover` via snapshot + WAL replay
- Add [`docs/TechnicalDebt.md`](docs/TechnicalDebt.md) with TD-001 (codec version-dispatched deserialization)
- Add mypy override for msgpack missing stubs
- Add 64 tests covering WAL entries, codec round-trip, Decimal precision, codec versioning, WAL append/read/truncate, magic header validation, CRC32 corruption detection, snapshot save/load, state store checkpoint/recover cycles

## v0.5.0 on 19th of March, 2026

- Add [`strategy_event.py`](nexus/infrastructure/strategy_event.py) with frozen `StrategyEvent` dataclass (strategy_id, event_type, realized_pnl, timestamp)
- Add `serialize_event` / `deserialize_event` to [`wal_codec.py`](nexus/infrastructure/wal_codec.py) with versioned msgpack format, Decimal-as-string precision
- Add `StateStore.append_event()` for writing `STRATEGY_EVENT` WAL entries alongside `STATE_MUTATION` entries
- Add [`loss_derivation.py`](nexus/infrastructure/loss_derivation.py) with `derive_rolling_losses()` pure function — scans strategy events by 24h/7d/30d windows, sums negative realized P&L per strategy
- Enhance `StateStore.recover()` with two-pass recovery: (1) snapshot + STATE_MUTATION replay, (2) STRATEGY_EVENT scan to re-derive and overwrite rolling loss counters
- Add 44 tests covering strategy event construction/validation, event codec round-trip, loss derivation window boundaries, and enhanced recovery with checkpoint boundary handling

## v0.6.0 on 19th of March, 2026

- Add [`reservation.py`](nexus/core/capital_controller/reservation.py) with frozen `Reservation` dataclass (reservation_id, strategy_id, notional, estimated_fees, created_at, expires_at) and `ReservationResult` outcome type
- Add [`capital_controller.py`](nexus/core/capital_controller/capital_controller.py) with thread-safe `CapitalController` guarding `CapitalState` behind `threading.Lock`
- Add `check_and_reserve()` with 4 ordered atomic checks: per-trade allocation, strategy budget, available capital, total utilization
- Add `release_reservation()` returning locked capital to the available pool
- Add constants `MAX_ALLOCATION_PER_TRADE_PCT` (0.15) and `MAX_CAPITAL_UTILIZATION_PCT` (0.80)
- Add 39 tests covering reservation validation, all check failure paths, release lifecycle, and 10-thread concurrency contention

## v0.7.0 on 20th of March, 2026

- Add [`tracked_order.py`](nexus/core/capital_controller/tracked_order.py) with `OrderLifecycleState` enum (IN_FLIGHT, WORKING) and frozen `TrackedOrder` dataclass
- Add `CapitalController.send_order()` to convert reservation into in-flight order
- Add `CapitalController.order_ack()` to transition in-flight → working
- Add `CapitalController.order_reject()` to release in-flight order capital
- Add `CapitalController.order_fill()` to handle partial/full fills with proportional fee allocation
- Add `CapitalController.order_cancel()` to release working order remaining capital
- Add 45 tests covering TrackedOrder validation, all lifecycle transitions, and concurrency (281 total)

## v0.8.0 on 20th of March, 2026

- Add warning log on reservation TTL expiry with reservation_id, strategy_id, total, held duration
- Add 2 tests for expiry logging (288 total)

## v0.9.0 on 21st of March, 2026

- Add instance-level drawdown state fields to `RiskState`: starting capital, cumulative realized/unrealized P&L, equity/HWMs, current drawdowns, and max drawdown metrics
- Add deterministic recompute and update triggers for drawdown metrics via `recompute_drawdown_metrics`, `update_cumulative_realized_pnl`, and `update_unrealized_pnl`
- Extend WAL risk-state codec to persist and recover all drawdown metrics with backward-compatible defaults for older payloads
- Expose drawdown views for validator and diagnostics consumers via `to_risk_check_metrics()` and `to_drawdown_diagnostics()`
- Add comprehensive drawdown tests for formula correctness, monotonic HWM/max drawdown behavior, and codec round-trip coverage (304 total)

## v0.10.0 on 21st of March, 2026

- Add `CapitalState.per_strategy_deployed` with finite non-negative validation for per-strategy deployment accounting
- Add `InstanceConfig.capital_pct` with allocation validation (`(0,100]` per strategy, total `<= 100`)
- Add `CapitalController.compute_strategy_budget()` to derive budget from `capital_pool` and `capital_pct`, with optional realized P&L auto-compounding
- Refactor `check_and_reserve()` to enforce strategy budget from controller-owned deployed state instead of caller-provided `strategy_deployed`
- Add lifecycle deployed accounting updates across reservation expiry/release and order reject/cancel paths
- Extend WAL/state recovery to persist and restore `per_strategy_deployed` with backward-compatible defaults for older payloads
- Add comprehensive per-strategy isolation and accounting invariant tests (326 total)

## v0.11.0 on 24th of March, 2026

- Add validator pipeline contracts for 3.1 with canonical six-stage ordering (`INTAKE -> RISK -> PRICE -> CAPITAL -> HEALTH -> PLATFORM_LIMITS`)
- Add ordered pipeline executor with strict short-circuit denial semantics and stage/result consistency validation
- Add RFC stage-1 intake hooks and checks (`MAX_ORDER_RATE`, `DUPLICATE_ORDER_WINDOW_MS`, reference-integrity, spot direction, action/size guards)
- Add risk-stage adapter mapping `RiskState.to_risk_check_metrics()` outputs to validator deny decisions
- Add price-stage checks with stable reason codes and consequence routing metadata for strategy owner vs platform ops audiences
- Add capital-stage adapter integrating `CapitalController.check_and_reserve(...)` into validator decision contracts with reservation passthrough
- Add health-stage telemetry policy evaluation contracts (warn/breach/halt over latency, consecutive failures, failure rate, rate-limit headroom, clock drift)
- Add stage-6 platform-limits contracts for absolute operator limits (`max_order_notional`, `max_order_rate`, `max_position`, `max_daily_loss`, `max_capital_utilization`)
- Add safety-action bypass for `EXIT`/`ABORT`/`CANCEL` across `CAPITAL`, `HEALTH`, and `PLATFORM_LIMITS`
- Add deterministic denial coverage tests proving identical inputs/snapshots produce stable `failed_stage`, `reason_code`, and message outputs

## v0.12.0 on 24th of March, 2026

- Add `InstanceConfig.max_order_rate` as optional operator config with strict validation (reject bool/non-int/non-positive values)
- Update default intake hook wiring to source `MAX_ORDER_RATE` from `InstanceConfig`, while preserving explicit hook override precedence
- Add duplicate-window key regression coverage to lock idempotency semantics on (`strategy_id`, `command_id`) rather than order-shape ambiguity
- Add temporal intake checks coverage for rate-window recovery and duplicate-window replay expiry behavior
- Add explicit duplicate-hook non-ENTER bypass coverage and message-stability assertions for core intake deny paths
- Expand validator intake test coverage and raise full-suite baseline to 470 passing tests

## v0.13.0 on 24th of March, 2026

- Add `MODIFY` edit-baseline field `current_order_notional` to `ValidationRequestContext` with strict finite/non-negative validation
- Add capital-stage delta semantics for `MODIFY`: when `current_order_notional` is provided, reserve only positive deltas and skip reservation for no-op/decrease edits; otherwise reserve full `order_notional`
- Add strict lifecycle gate for `MODIFY` in intake hooks: require `modifiable_command_ids`, deny when unavailable, and validate command membership against modifiable set
- Add capital-stage tests for `MODIFY` increase/decrease/no-op reservation behavior
- Add pipeline-executor tests for `MODIFY` increase/decrease/no-op flows and deterministic deny stage/reason/message behavior
- Expand validator test baseline to 486 passing tests

## v0.14.0 on 24th of March, 2026

- Add RFC 3.4 price-validation config fields to `InstanceConfig`: `book_staleness_max_seconds`, `max_spread_bps`, `price_deviation_max_bps`, `reference_price_source`
- Add strict validation for 3.4 config fields: positive staleness seconds, finite non-negative bps thresholds, allowed `reference_price_source`, and deviation-source requirement
- Add `build_price_stage_limits_from_config(...)` to map `book_staleness_max_seconds` to `max_staleness_ms` and wire spread/deviation/source limits into Stage 3
- Add reference-source identity to price contracts via `PriceCheckSnapshot.reference_price_source` and `PriceStageLimits.reference_price_source` with non-empty validation
- Fix deviation-stage behavior to deny deterministically when reference source is missing or mismatched (`PRICE_SYSTEM_DATA_UNAVAILABLE`/`PRICE_SNAPSHOT_INVALID`)
- Add and expand tests for 3.4 config validation, source-aware deviation paths, consequence routing (stale => platform-critical, spread/deviation => strategy-warning), and full-suite stability (512 passing tests)

## v0.15.0 on 25th of March, 2026

- Add [`stp_mode.py`](nexus/core/stp_mode.py) with `STPMode` enum (CANCEL_MAKER, CANCEL_TAKER, CANCEL_BOTH) for self-trade prevention
- Add `stp_mode` field to `InstanceConfig` with default `CANCEL_TAKER` and validation
- Add [`trade_command_type.py`](nexus/infrastructure/praxis_connector/trade_command_type.py) with `TradeCommandType` enum (NEW_ORDER, AMEND_ORDER, CANCEL_ORDER)
- Add [`trade_command.py`](nexus/infrastructure/praxis_connector/trade_command.py) with frozen `TradeCommand` dataclass and command-type-specific validation
- Add [`translate.py`](nexus/infrastructure/praxis_connector/translate.py) with `translate_to_trade_command()` mapping ValidationAction to TradeCommand
- Add [`outbound_connector.py`](nexus/infrastructure/praxis_connector/outbound_connector.py) with `OutboundConnector` protocol for Trading sub-system dispatch
- Enforce `translate_to_trade_command` guards for allowed decision and non-empty `command_id`
- Enforce `TradeCommand` rejection of `side`/`size` for `AMEND_ORDER`/`CANCEL_ORDER` command types
- Enforce robust `expires_at` computation in tests using `timedelta` instead of `minute` replacement
- Add 45 tests covering STPMode, TradeCommandType, TradeCommand validation, and translation for all action types (558 total)

## v0.16.0 on 27th of March, 2026

- Add [`trade_outcome_type.py`](nexus/infrastructure/praxis_connector/trade_outcome_type.py) with `TradeOutcomeType` enum (ACK, PARTIAL, FILLED, REJECTED, EXPIRED, CANCELED)
- Add [`trade_outcome.py`](nexus/infrastructure/praxis_connector/trade_outcome.py) with frozen `TradeOutcome` dataclass and outcome-type-specific validation (fill outcomes require `actual_fees`)
- Add [`inbound_connector.py`](nexus/infrastructure/praxis_connector/inbound_connector.py) with `InboundConnector` protocol for receiving outcomes from Trading sub-system
- Add [`order_context.py`](nexus/infrastructure/praxis_connector/order_context.py) with frozen `OrderContext` dataclass bridging outcome processing with order metadata (side, trade_id, estimated_fees)
- Add [`process_result.py`](nexus/infrastructure/praxis_connector/process_result.py) with frozen `ProcessResult` dataclass for outcome processing results
- Extend `CapitalController.order_fill()` with `actual_fees` parameter and fee reconciliation against `fee_reserve`
- Add [`outcome_processor.py`](nexus/infrastructure/praxis_connector/outcome_processor.py) with `OutcomeProcessor` routing outcomes to Capital Controller lifecycle methods
- Add position growth with VWAP entry price calculation on entry fills
- Add position reduction with `pending_exit` clearing on exit fills, removing closed positions from state
- Add 126 tests covering TradeOutcome validation, OutcomeProcessor routing, position growth/reduction, and fee reconciliation (638 total)

## v0.17.0 on 28th of March, 2026

- Add `StateStore` dependency to `OutcomeProcessor` for WAL persistence of risk events
- Compute realized P&L in `_reduce_position` using side-aware formula: `side_multiplier × (fill_price - entry_price) × fill_size` where `side_multiplier` is `-1` for shorts
- Add `_update_strategy_risk_state()` to update per-strategy `strategy_realized_pnl`, `rolling_loss_24h/7d/30d`, and `high_water_mark`
- Update instance-level `cumulative_realized_pnl` triggering `recompute_drawdown_metrics()` on exit fills
- Persist `StrategyEvent` to WAL via `StateStore.append_event()` on every exit fill
- Add 7 tests covering risk metrics recalculation, strategy isolation, and WAL persistence (652 total)

## v0.18.0 on 31st of March, 2026

- Add [`manifest.py`](nexus/infrastructure/manifest.py) with frozen `StrategySpec` dataclass (strategy_id, file, permutation_ids, capital_pct) and frozen `Manifest` dataclass (capital_pool, strategies)
- Add `load_manifest()` to parse YAML manifest files with `allocated_capital` ceiling validation
- Add `_validate_strategy_files()` to verify strategy .py files exist and have valid Python syntax via `ast.parse()`
- Add `pyyaml>=6.0` runtime dependency and mypy override for yaml module
- Add 58 tests covering StrategySpec, Manifest, load_manifest parsing, and file validation (714 total)

## v0.19.0 on 31st of March, 2026

- Add [`base.py`](nexus/strategy/base.py) with `Strategy` ABC defining `on_save() → bytes` and `on_load(bytes) → None` lifecycle hooks for state persistence
- Add [`loader.py`](nexus/strategy/loader.py) with `load_strategy_class()` for dynamic import from .py files and `instantiate_strategy()` for creating Strategy instances from manifest specs
- Add path traversal prevention and ABC inheritance validation in strategy loader
- Add 27 tests covering Strategy ABC, on_save/on_load lifecycle, and dynamic loading (742 total)

## v0.20.0 on 1st of April, 2026

- Add [`params.py`](nexus/strategy/params.py) with `StrategyParams` frozen dataclass wrapping manifest parameters
- Add [`signal.py`](nexus/strategy/signal.py) with `Signal` frozen dataclass for predictor function output (predictor_fn_id, values dict, timestamp)
- Add [`action.py`](nexus/strategy/action.py) with `ActionType` enum (ENTER, EXIT, MODIFY, ABORT) and `Action` frozen dataclass
- Add [`context.py`](nexus/strategy/context.py) with `StrategyContext` frozen dataclass (positions, capital_available, operational_mode)
- Extend `Strategy` ABC with five event callbacks: `on_startup`, `on_signal`, `on_outcome`, `on_timer`, `on_shutdown`
- Wire new types in [`nexus/strategy/__init__.py`](nexus/strategy/__init__.py) public API
- Add 57 tests covering event dispatch types and Strategy callbacks (772 total)

## v0.21.0 on 2nd of April, 2026

- Add [`executor.py`](nexus/strategy/executor.py) with `StrategyExecutor` class wrapping Strategy instance with `threading.Lock` for serialized callback execution
- Add [`runner.py`](nexus/strategy/runner.py) with `StrategyRunner` class orchestrating multiple executors by strategy_id
- Add 29 tests covering executor delegation, runner routing, unknown strategy rejection, and concurrent dispatch serialization (806 total)

## v0.22.0 on 2nd of April, 2026

- Add [`nexus/startup/`](nexus/startup/) module with `StartupSequencer` and `StartupError`
- Add `StartupSequencer.start()` orchestrating full startup sequence: recover state, register with trading, reconcile capital, load manifest, instantiate strategies, restore strategy state, replay events, wire predictor_fns, register timers, determine mode, dispatch startup
- Add state recovery via `StateStore.recover()` delegation
- Add manifest loading and strategy instantiation with executor/runner building
- Add external integration stubs: `_register_with_trading` (TD-005), `_reconcile_capital` (TD-006)
- Add state restoration stubs: `_restore_strategy_state` (TD-007), `_replay_strategy_events` (TD-008)
- Add runtime setup stubs: `_wire_predictor_fns` (TD-009), `_register_timers` (TD-010)
- Add startup dispatch: `_determine_mode` stub (TD-011, always ACTIVE), `_dispatch_startup` calling strategies with context
- Add 32 tests covering construction validation, state recovery, stubs, manifest loading, strategy instantiation, dispatch, and integration (838 total)

## v0.23.0 on 3rd of April, 2026

- Add [`shutdown_sequencer.py`](nexus/startup/shutdown_sequencer.py) with `ShutdownSequencer` orchestrating graceful termination: stop signals → stop timers → dispatch on_shutdown → submit actions → wait terminal → dispatch on_save → persist strategy state → final checkpoint → deregister
- Add `_dispatch_shutdown()` building per-strategy context and collecting shutdown actions
- Add `_submit_actions()` filtering EXIT/ABORT actions, skipping ENTER/MODIFY (TD-015 for Validator integration)
- Add `_dispatch_save()` calling strategy on_save() and collecting state blobs
- Add `_persist_strategy_state()` writing strategy state to individual `.bin` files with atomic tmp+rename pattern
- Add `_final_checkpoint()` delegating to `StateStore.checkpoint()` for final snapshot + WAL truncation
- Add `ShutdownError` exception to [`error.py`](nexus/startup/error.py)
- Add `StrategyExecutor.dispatch_save()` and `StrategyRunner.dispatch_save()` for on_save lifecycle dispatch
- Add `InstanceConfig.shutdown_wait_timeout_seconds` and `shutdown_abort_timeout_seconds` for configurable shutdown timeouts
- Add shutdown stubs: `_stop_signals` (TD-013), `_stop_timers` (TD-014), `_wait_terminal` (TD-016), `_deregister` (TD-012)
- Fix import-in-method anti-pattern in [`sequencer.py`](nexus/startup/sequencer.py) by moving structlog to module level
- Add 25 tests covering construction validation, dispatch shutdown, submit actions filtering, dispatch save, persist strategy state, final checkpoint, full shutdown sequence, executor/runner dispatch_save, and config timeout validation (863 total)

## v0.24.0 on 5th of April, 2026

- Add crash-only design: fresh start and crash recovery share same code path with no branching
- Add `_recover_state()` creating initial `InstanceState` when `StateStore.recover()` returns None
- Add `strategy_state_path` parameter to `StartupSequencer` for strategy state restoration directory
- Add `_restore_strategy_state()` loading `.bin` files into strategies via `dispatch_load()` (completes TD-007)
- Add `StateStore.read_events()` to read `STRATEGY_EVENT` entries from WAL
- Add `Strategy.on_event_replay()` optional callback with default no-op for event replay during recovery
- Add `StrategyExecutor.dispatch_event_replay()` and `StrategyRunner.dispatch_event_replay()` methods
- Add `_replay_strategy_events()` dispatching WAL events to strategies for state reconstruction (completes TD-008)
- Add crash-only verification tests proving same code path for fresh start and crash recovery
- Add path traversal validation for strategy_id in state restoration
- Add StartupError wrapping for read_events() failures
- Add 28 tests covering state restoration, event replay, crash-only design verification, and validation (891 total)

## v0.25.0 on 14th of April, 2026

- Add [`lifecycle_result.py`](nexus/core/capital_controller/lifecycle_result.py) with `LifecycleResult` dataclass and `FailureCategory` enum (EXPECTED_MISS, INVARIANT_BREACH) replacing bare bool returns in capital lifecycle methods (TD-003)
- Refactor `CapitalController` lifecycle methods (`release_reservation`, `send_order`, `order_ack`, `order_reject`, `order_fill`, `order_cancel`) to return `LifecycleResult` with classified reason codes
- Convert `OutcomeProcessor` invariant breach paths to raise `RuntimeError` for entry/exit fills on missing positions or overfills
- Enforce UTC-only timestamps across all domain types: `Signal`, `TradeOutcome`, `StrategyEvent`, `Reservation`, `TrackedOrder`, `TradeCommand` (TD-004)
- Reject whitespace-padded `strategy_id` in `StrategySpec.__post_init__` (TD-017)
- Add version-dispatched deserialization to WAL codec with `_decode_state_v1` routing on embedded `_v` field (TD-001)
- Preserve `STRATEGY_EVENT` WAL entries across checkpoint for rolling loss window recovery (TD-002)
- Add [`SensorSpec`](nexus/infrastructure/manifest.py) frozen dataclass for Limen Sensor configuration (experiment_dir, permutation_ids, interval_seconds)
- Replace `StrategySpec.permutation_ids` with `StrategySpec.sensors: tuple[SensorSpec, ...]` and update `load_manifest` YAML parsing
- Add `vaquum_limen` as runtime dependency (installed from `git+https://github.com/Vaquum/Limen`)
- Add `WiredSensor` dataclass and implement `_wire_sensors()` in `StartupSequencer` — calls `Trainer(experiment_dir).train(permutation_ids)` to produce Sensors during startup (TD-009)
- Add [`signal_producer.py`](nexus/strategy/signal_producer.py) with `produce_signal()` for live feature preparation via `limen_manifest.prepare_data()` and predict-to-Signal translation
- Add [`predict_loop.py`](nexus/strategy/predict_loop.py) with `PredictLoop` timer-based signal generation — per-sensor `threading.Timer` at `interval_seconds` calling `produce_signal` → `dispatch_signal`
- Implement `_stop_signals()` in `ShutdownSequencer` via `PredictLoop.stop()` (TD-013)
- Add [`praxis_outbound.py`](nexus/infrastructure/praxis_connector/praxis_outbound.py) with `PraxisOutbound` sync-to-async bridge — `asyncio.run_coroutine_threadsafe` for `submit_command`, `register_account`, `deregister_account`, `pull_positions`
- Add [`praxis_inbound.py`](nexus/infrastructure/praxis_connector/praxis_inbound.py) with `PraxisInbound` queue-based `receive_outcome()` consuming from `queue.Queue[TradeOutcome]`
- Implement `_register_with_trading()` and `_deregister()` via `PraxisOutbound` (TD-005, TD-012)
- Wire `PraxisOutbound` into `_submit_actions()` for shutdown EXIT/ABORT filtering; actual submission pending Action fields (TD-015, TD-023)
- Implement `_wait_terminal()` polling `PraxisInbound` for terminal outcomes with configurable `shutdown_timeout` (TD-016)
- Implement `_reconcile_capital()` pulling Praxis positions, comparing by trade_id, updating `position_notional` (TD-006)
- Split `test_manifest.py` into `test_sensor_spec.py`, `test_strategy_spec.py`, `test_manifest.py`
- Add `docs/TechnicalDebt.md` entries TD-019 through TD-024 for deferred work
- Add 53 tests across new modules (944 total)

## v0.26.0 on 15th of April, 2026

- Add [`TimerSpec`](nexus/infrastructure/manifest.py) frozen dataclass for strategy timer configuration (timer_id, interval_seconds)
- Add optional `timers` field to `StrategySpec` with duplicate timer_id rejection
- Update `load_manifest` YAML parsing for optional `timers` entries
- Add [`timer_loop.py`](nexus/strategy/timer_loop.py) with `TimerLoop` class — per-strategy `threading.Timer` scheduling for `on_timer` callbacks (TD-010)
- Implement `_register_timers()` in `StartupSequencer` — extracts timer specs from manifest (TD-010)
- Implement `_stop_timers()` in `ShutdownSequencer` via `TimerLoop.stop()` (TD-014)
- Add [`health_evaluator.py`](nexus/core/health_evaluator.py) with `HealthEvaluator`, `HealthSnapshot`, and `HealthThresholds` for three-threshold mode determination (warn/breach/halt) (TD-011)
- Implement `_determine_mode()` in `StartupSequencer` — evaluates health snapshot against thresholds, defaults to ACTIVE when no health data available
- Replace O(N) dictionary scan in `_purge_expired` with `heapq`-based expiry tracking (TD-018)
- Replace O(N) dictionary scan in `make_duplicate_order_hook` with `collections.deque`-based chronological expiry (TD-018)
- Add `docs/TechnicalDebt.md` entries TD-025 (thread churn) and TD-026 (health data source)
- Add 28 tests across new modules (972 total)

## v0.27.0 on 18th of April, 2026

- Promote all inline imports across `nexus/` and `tests/` to top-of-file (`health_evaluator.py`, test files for `wal_codec`, `signal_producer`, `wire_sensors`, `instance_state`, `timer_loop`, `startup_sequencer`, `strategy_event_types`, `strategy_base`)
- Add Action trade fields per RFC §Action Parameters in [`action.py`](nexus/strategy/action.py): `direction`, `size`, `execution_mode`, `order_type`, `execution_params`, `deadline`, `trade_id`, `command_id`, `maker_preference`, `reference_price`; per-action_type required-field validation in `__post_init__` via `_validate_action_type_requirements()` (TD-023.1)
- Split Action reference fields per RFC-3001 Stage 1: EXIT requires `trade_id`; MODIFY/ABORT require `command_id` (was `trade_id`) (TD-023.2)
- Mirror Praxis enums in [`order_types.py`](nexus/core/domain/order_types.py): `ExecutionMode`, `OrderType`, `MakerPreference`. Retype `Action.execution_mode/order_type/maker_preference` from free strings to these enums (TD-023.3)
- Extend [`TradeCommand`](nexus/infrastructure/praxis_connector/trade_command.py) with `execution_mode`, `order_type`, `execution_params`, `deadline`, `maker_preference`, `reference_price`; AMEND/CANCEL invariants enforce these are absent. Update `translate_to_trade_command` to take an `Action` and populate these on NEW_ORDER (TD-023.3)
- Wire real fields through [`PraxisOutbound.send_command`](nexus/infrastructure/praxis_connector/praxis_outbound.py): `order_type`, `execution_mode`, `execution_params`, `maker_preference`, `reference_price` flow from the TradeCommand instead of placeholder `None` values (TD-023.4)
- Add `PraxisOutbound.send_abort` wrapping Praxis `Trading.submit_abort` via `asyncio.run_coroutine_threadsafe` (new `submit_abort_fn` parameter)
- Wire [`ShutdownSequencer._submit_actions`](nexus/startup/shutdown_sequencer.py): EXIT actions translate through `translate_to_trade_command` and submit via `send_command`; ABORT actions submit via `send_abort` with reason `'shutdown'`; returned command_ids stored in `_submitted_command_ids`. Optional `config: InstanceConfig` parameter on `ShutdownSequencer` (TD-023.5)
- Wire ABORT escalation in `_wait_terminal`: on first-round timeout, send ABORT for each pending command (reason `'shutdown_escalation'`) and re-poll with half the original `shutdown_timeout` before giving up. Refactor poll loop into `_poll_until_terminal` and abort fan-out into `_escalate_abort_pending` (TD-023.6)
- Import Praxis-only positions during reconciliation: [`StartupSequencer._reconcile_capital`](nexus/startup/sequencer.py) now constructs a Nexus `Position` (strategy_id / symbol / side / size / entry_price) and inserts it into `instance_state.positions` when a Praxis position has no Nexus counterpart, provided required fields are present (TD-024)
- Add `PraxisOutbound.get_health_snapshot(account_id)` wrapping Praxis `Trading.get_health_snapshot` via `asyncio.run_coroutine_threadsafe` (new `get_health_snapshot_fn` parameter) (TD-026.1)
- Add [`health_loop.py`](nexus/core/health_loop.py) with `HealthLoop` — periodic timer pulls a `HealthSnapshot` from a configurable provider, evaluates via `HealthEvaluator`, and updates `instance_state.mode` on transition. Provider/evaluator exceptions are logged-and-swallowed; `start` is idempotent; `tick_once()` exposed for synchronous callers (TD-026.2)
- Rename `_validate_required_fields` to `_validate_action_type_requirements` in `Action` (existing per-action_type checks; the rename reflects that field-shape validation already happened earlier in `__post_init__`)
- Add 62 tests across new and updated modules (1034 total)

## v0.28.0 on 19th of April, 2026

- BREAKING: Move instance identity and capital ceiling into the strategy manifest. `Manifest` gains required `account_id: str` and `allocated_capital: Decimal` fields, with internal validation that `capital_pool ≤ allocated_capital`. Manifest YAML files must now declare `account_id:` and `allocated_capital:` alongside the existing `capital_pool:` and `strategies:` blocks
- BREAKING: Drop the `allocated_capital` parameter from `load_manifest(path)`; the ceiling is sourced from the manifest itself
- BREAKING: Drop `allocated_capital` and `account_id` constructor parameters from [`StartupSequencer`](nexus/startup/sequencer.py); both are derived from the loaded manifest at runtime
- Reorder startup sequence to call `_load_manifest` first (was step 4); `_recover_state`, `_register_with_trading`, and `_reconcile_capital` all read identity and capital from `self._manifest`
- BREAKING: Remove `allocated_capital` field from [`InstanceConfig`](nexus/instance_config.py) — the ceiling no longer belongs in runtime/validator config, it is per-account manifest state
- Rename `InstanceState.from_config(config)` → [`InstanceState.fresh(capital_pool)`](nexus/core/domain/instance_state.py) — the factory seeds `CapitalState.capital_pool` from `Manifest.capital_pool` (the operational allocation), not `Manifest.allocated_capital` (the infrastructure ceiling); drops the unused `InstanceConfig` dependency from `nexus.core.domain`
- Update manifest YAML fixtures, direct `Manifest(...)` constructions, `InstanceConfig(...)` callers, and `InstanceState.fresh(...)` callers across all tests
- Add `load_manifest` validation tests for missing / invalid / non-positive `allocated_capital` and for blank `account_id`

## v0.29.0 on 22nd of April, 2026

- Add [`action_submit.py`](nexus/strategy/action_submit.py) with `submit_actions(actions, *, strategy_id, config, praxis_outbound, validator, build_context, now)` — runtime helper that pushes strategy-emitted actions through `ValidationPipeline` → `translate_to_trade_command` → `PraxisOutbound.send_command` for `ENTER`/`EXIT`/`MODIFY`, and directly through `PraxisOutbound.send_abort` for `ABORT`. Returns per-action `SubmissionOutcome` (`SUBMITTED` / `REJECTED` / `SUBMIT_FAILED` / `INVALID`) so callers can record Praxis-assigned command_ids (PT.1.1)
- Wire [`PredictLoop._tick`](nexus/strategy/predict_loop.py) to forward `dispatch_signal` return value to an injected `action_submit: Callable[[list[Action], str], None] | None` callback. Constructor accepts optional `action_submit=None` (back-compat for tests that do not exercise the submission path); submitter exceptions are logged-and-swallowed so a bad submit does not kill the loop (PT.1.2)
- Wire [`TimerLoop._tick`](nexus/strategy/timer_loop.py) the same way — dispatch_timer return value flows through `action_submit` with identical exception semantics (PT.1.3)
- Add public [`StartupSequencer.instance_state`](nexus/startup/sequencer.py) and `StartupSequencer.manifest` properties returning the live `InstanceState` / loaded `Manifest` (or `None` before `_load_manifest` / `_recover_state` have run). Returns the actual mutable state object — not a copy — so runtime `context_provider` callers observe reservations, position changes, and operational-mode transitions made by validator stages and outcome processing (PT.2.1)
- Add [`outcome_loop.py`](nexus/core/outcome_loop.py) with `OutcomeLoop(runner, praxis_inbound, state, context_provider, resolve_strategy_id, action_submit=None)` — single worker-thread loop polling `PraxisInbound.receive_outcome()`, resolving `command_id` → `strategy_id` via an injected callable, dispatching `on_outcome` through `StrategyRunner`, and forwarding any captured `list[Action]` back through `action_submit`. Idempotent `start()`/`stop()`, `tick_once()` for test harnesses, exception-absorbent worker (PT.3.1)
- Extend [`ShutdownSequencer`](nexus/startup/shutdown_sequencer.py) with optional `outcome_loop: OutcomeLoop | None` constructor parameter and a new `_stop_outcome_loop()` step invoked after `_stop_timers()` and before `_wait_terminal()`. OutcomeLoop and `_wait_terminal` both consume from the same `PraxisInbound` queue, so leaving the loop running during shutdown would steal terminal outcomes out of the shutdown-path poll (PT.3.3)
- Add 11 tests across [`test_outcome_loop.py`](tests/test_outcome_loop.py) (8: empty-queue, known outcome, unresolved strategy skip, action forwarding, no-op dispatch, exception absorption, start/stop idempotency, worker-thread consumption) and new `TestStopOutcomeLoop` class in [`test_shutdown_sequencer.py`](tests/test_shutdown_sequencer.py) (3: stop called, no-loop fallback, call ordering before `_wait_terminal`)

## v0.30.0 on 24th of April, 2026

- Pin `vaquum_limen @ git+https://github.com/Vaquum/Limen@v2.4.3` in `pyproject.toml` so the Nexus and Praxis venvs converge on a single Limen release; without a version constraint the two venvs drifted (`vaquum_limen` 1.52.0 in the Nexus venv vs 2.0.0 in the Praxis venv) and an experiment trained in one could not be loaded by the other's `Trainer` (PT-FIX-4)
- Add optional `process_outcome: Callable[[TradeOutcome], None] | None` parameter to [`OutcomeLoop`](nexus/core/outcome_loop.py); the launcher uses it to apply venue-lifecycle effects to `CapitalController` via `OutcomeProcessor.process(...)` before `runner.dispatch_outcome` runs, so capital state is current when the strategy callback fires. Processor exceptions are caught and logged; the strategy callback still fires (PT-FIX-8)
- Cache one `Trainer` per resolved `experiment_dir` inside [`StartupSequencer._wire_sensors`](nexus/startup/sequencer.py); subsequent `Trainer` constructions for the same directory receive `data=cached._data` so the same frozen slice flows through every permutation reconstructed from that experiment. Avoids fetching the live Hugging Face dataset twice for SensorSpecs that share an experiment dir (PT-FIX-9)
- Add 3 tests in [`test_wire_sensors_data_cache.py`](tests/test_wire_sensors_data_cache.py) verifying shared-dir reuse, distinct-dir isolation, and single-sensor passthrough (1069 total)

## v0.31.0 on 25th of April, 2026

- Add operational-mode enforcement in [`validate_intake_stage`](nexus/core/validator/intake_stage.py); ENTER actions are rejected with reason `INTAKE_MODE_BLOCKS_ENTER` when `state.mode.mode != OperationalMode.ACTIVE`, and EXIT/MODIFY actions are rejected with `INTAKE_MODE_HALTED_BLOCKS_TRADING` when mode is HALTED. CANCEL/ABORT remain available in HALTED so an operator can wind pending orders down without flipping the mode back manually. Closes the gap where `HealthLoop` flipped `state.mode` to REDUCE_ONLY/HALTED on health degradation but no validator stage actually enforced the documented contract (PT-FIX-15)
- Add optional `action_submit: Callable[[list[Action], str], None] | None` constructor parameter and a `_pending_startup_actions` buffer to [`StartupSequencer`](nexus/startup/sequencer.py). `_dispatch_startup` now forwards via the constructor-injected submitter when available; otherwise it stashes per-strategy actions for the launcher to drain via the new public `drain_pending_startup_actions(submitter)` method (idempotent, per-strategy exception-safe). Closes the gap where strategy `on_startup` recovery actions (e.g. closing stale positions left over from a prior crash) were silently dropped (PT-FIX-16)
- Add [`WriteAheadLog.read_safe()`](nexus/infrastructure/wal.py) as a single-pass reader that silently stops at the first short-read or CRC mismatch. Switch [`StateStore.__init__`](nexus/infrastructure/state_store.py), `StateStore.recover`, `StateStore.read_events`, and `WriteAheadLog.truncate_keeping_events` to consume the safe variant so a process killed mid-`append` no longer blocks the next boot or checkpoint. `read_all` retains strict raise-on-corruption semantics for callers that want it. Add `WriteAheadLog.validate_magic()` and call it from `StateStore.__init__` so a non-WAL/garbage file fails loud at boot rather than at the first runtime `append()`. The torn record itself is unrecoverable and stays discarded; the file is cleaned up by the next `append()` via existing self-truncation (PT-FIX-17)
- Soften [`StartupSequencer._wire_sensors`](nexus/startup/sequencer.py) so a single sensor failure no longer takes down the whole account. Each sensor wires inside an isolated try/except; failures log full context (`strategy_id`, `experiment_dir`, `permutation_ids`, `error`) and the loop continues. The account aborts only when **every** attempted sensor failed — running with zero signal sources would be silent dead air (PT-FIX-18)
- Add 24 tests across [`test_validator_intake_stage.py`](tests/test_validator_intake_stage.py) (8 new mode-enforcement cases), [`test_startup_sequencer.py`](tests/test_startup_sequencer.py) (5 new pending-startup cases), [`test_wal_torn_tail.py`](tests/test_wal_torn_tail.py) (9 new torn-tail cases), and [`test_wire_sensors_isolation.py`](tests/test_wire_sensors_isolation.py) (4 new isolation cases) (1098 total)

## v0.32.0 on 26th of April, 2026

- Add `_halt_state_mode` as the first step of [`ShutdownSequencer.shutdown()`](nexus/startup/shutdown_sequencer.py); flips `state.mode` to `OperationalMode.HALTED` before any loop is stopped so any in-flight outcome dispatch's downstream ENTER is rejected by the validator's `_check_operational_mode` stage instead of leaking past `_dispatch_shutdown` to the venue (PT-FIX-25)
- Default `StartupSequencer._determine_mode` to `OperationalMode.REDUCE_ONLY` when no `health_snapshot` is wired at boot, instead of the prior `ACTIVE`. Mirrors the resolved mode into `state.mode` so the validator's `_check_operational_mode` stage sees the same value the sequencer carries into `StrategyContext`. Closes the ~5 s permissive window where strategies' `on_startup` ENTERs reached the venue before the first `HealthLoop` tick had landed (PT-FIX-26)
- Add [`bridge_to_capital(controller, outcome)`](nexus/strategy/action_submit.py) helper that invokes `CapitalController.send_order(reservation_id, command_id)` for SUBMITTED outcomes carrying a reservation. Document the `outcome.command_id` ↔ `CapitalController.send_order(order_id=...)` contract on [`OutcomeProcessor.process`](nexus/infrastructure/praxis_connector/outcome_processor.py) so a name swap is caught at review. Without this wiring, every ACK/FILL outcome returned `INVARIANT_BREACH: order not found` silently (PT-FIX-27)
- Add `outcome_processor: OutcomeProcessor | None` constructor parameter and `_apply_terminal_outcome` helper to [`ShutdownSequencer`](nexus/startup/shutdown_sequencer.py); `_submit_exit` now builds and stores an `OrderContext` per command, and `_poll_until_terminal` routes FILLED outcomes through `OutcomeProcessor.process` so shutdown EXIT fills decrement `state.positions[trade_id].size` correctly. Pre-fix, the OutcomeLoop was already stopped before `_submit_actions`, so shutdown EXIT FILLs were silently dropped at the state level — leaving the next boot to recover stale positions (PT-FIX-31)
- Add `ValidationStage.RISK` to the EXIT/ABORT/CANCEL bypass set in [`_should_bypass_stage`](nexus/core/validator/pipeline_executor.py). Risk gates new exposure, not exit; pre-fix a drawdown-driven RISK denial blocked exits exactly when they mattered most (PT-FIX-32)
- Exempt EXIT/ABORT/CANCEL from the `INTAKE_ORDER_NOTIONAL_ZERO` check in [`validate_intake_stage`](nexus/core/validator/intake_stage.py). Size-only exits with zero notional now pass intake; ENTER and MODIFY still require positive notional (PT-FIX-33)
- Tighten the all-sensors-failed guard in [`StartupSequencer._wire_sensors`](nexus/startup/sequencer.py) so a manifest declaring zero sensor specs raises `StartupError('wire_sensors', 'manifest declared 0 sensor specs across all strategies')` instead of silently booting into a permanently dead account with no signal source (PT-FIX-34)
- Add [`CapitalController.reconcile_at_boot(positions=...)`](nexus/core/capital_controller/capital_controller.py) that resets stranded `reservation_notional` / `in_flight_order_notional` / `working_order_notional` aggregates carried over from a crashed prior boot, and rebuilds `per_strategy_deployed` from the live positions so it sums to `position_notional` exactly. The launcher calls this after constructing `CapitalController`. Fixes the first-ENTER attribution-mismatch denial that would otherwise permanently block new ENTERs on crash-recovery boot (PT-FIX-35, PT-FIX-41)
- Replace the bare `state.positions[action.trade_id]` lookup in [`_build_exit_order_context`](nexus/startup/shutdown_sequencer.py) with `.get()` + explicit `ValueError` so a position removed by a concurrent OutcomeLoop tick during shutdown does not propagate `KeyError` past the surrounding `try/except ValueError` (PT-FIX-36)
- Add `ValidationStage.PRICE` to the EXIT/ABORT/CANCEL bypass set, completing the rule "any stage gating new exposure must not block exits". Pre-fix `PRICE_BOOK_STALE` and `PRICE_SPREAD_LIMIT` denials silently dropped EXITs exactly when stale-orderbook conditions made fast exits critical (PT-FIX-37)
- Route PARTIAL fills (not just FILLED terminals) through `_apply_terminal_outcome` in [`_poll_until_terminal`](nexus/startup/shutdown_sequencer.py) so a partial fill on a shutdown EXIT followed by a CANCELED/EXPIRED terminal still decrements `state.positions[trade_id].size`. Pre-fix the partial-fill amount was silently lost (PT-FIX-38)
- Accept `OrderLifecycleState.WORKING` (in addition to IN_FLIGHT) in [`CapitalController.order_reject`](nexus/core/capital_controller/capital_controller.py) so a venue REJECTED outcome racing past an ACK releases `working_order_notional` instead of leaving capital parked. Pre-fix the WORKING capital was permanently leaked because TTL eviction only covers `_reservations`, not `_orders` (PT-FIX-40)
- Re-check `_running` under `_lock` inside [`HealthLoop._apply_snapshot`](nexus/core/health_loop.py) before writing `state.mode`, so a tick already past the top-level `_running` guard cannot overwrite the PT-FIX-25 HALTED ratchet after `stop()` returned. New `respect_running: bool = True` parameter; `tick_once()` passes `False` for callers driving the loop manually (PT-FIX-42)
- Mirror PT-FIX-40 in [`CapitalController.order_cancel`](nexus/core/capital_controller/capital_controller.py): accept both IN_FLIGHT and WORKING. A venue EXPIRED/CANCELED outcome for an order that never received an ACK now releases `in_flight_order_notional` instead of leaving capital parked (PT-FIX-43)
- Add `non_pending_outcome_handler: Callable[[TradeOutcome], None] | None` constructor parameter to [`ShutdownSequencer`](nexus/startup/shutdown_sequencer.py). `_poll_until_terminal` now routes outcomes whose `command_id` is NOT in the shutdown's pending set through the handler (the launcher passes its `process_outcome` closure) instead of silently discarding them with `continue`. Pre-fix pre-shutdown ENTER FILLEDs that arrived after `_stop_outcome_loop` were lost — position state drifted from venue truth on every subsequent boot (PT-FIX-44)
- Replace the dimensionally-wrong fallback in [`_build_exit_order_context`](nexus/startup/shutdown_sequencer.py) — `approx_notional <= _ZERO` now raises `ValueError` instead of silently assigning `action.size` (base-asset units) into the notional slot (quote-asset units). The branch is unreachable under current `Position` invariants but the prior fallback would have constructed an `OrderContext` with garbage if it ever fired
- Add structured logs to each early-return in [`bridge_to_capital`](nexus/strategy/action_submit.py) — `debug` for routine cases (REJECTED/SUBMIT_FAILED status, no reservation on EXIT/MODIFY), `warning` for the unexpected case (SUBMITTED outcome with no `command_id`). Pre-fix all three early-returns were silent, leaving no breadcrumbs for debugging
- Add 16-case parametrized contract test in [`test_validator_pipeline_executor.py`](tests/test_validator_pipeline_executor.py) (`TestSafetyBypassContract`) asserting every gating stage (RISK / PRICE / CAPITAL / HEALTH / PLATFORM_LIMITS) is in the EXIT/ABORT/CANCEL bypass set, plus an anti-regression that INTAKE is deliberately NOT in the bypass set. A future stage addition that should bypass for exits will be caught by the parametrize list
- Add 84 tests across [`test_shutdown_sequencer.py`](tests/test_shutdown_sequencer.py) (33 new across `TestShutdownSequence`, `TestShutdownExitAppliesToState`, `TestSubmitExitMissingPositionRace`, `TestShutdownExitPartialFill`, `TestNonPendingOutcomeHandler`), [`test_capital_controller.py`](tests/test_capital_controller.py) (16 new across `TestReconcileAtBoot`, `TestReconcileAtBootRebuildsPerStrategyDeployed`, `TestOrderReject`, `TestOrderCancel`), [`test_outcome_processor.py`](tests/test_outcome_processor.py) (2 new in `TestOutcomeProcessorReject` and `TestOutcomeProcessorCancel`), [`test_validator_pipeline_executor.py`](tests/test_validator_pipeline_executor.py) (20 new safety-bypass / PRICE-bypass / `TestSafetyBypassContract` tests), [`test_validator_intake_stage.py`](tests/test_validator_intake_stage.py) (3 new EXIT-with-zero-notional cases), [`test_startup_sequencer.py`](tests/test_startup_sequencer.py) (3 new — wire_sensors zero-spec, determine_mode REDUCE_ONLY default, mirror state.mode), [`test_health_loop.py`](tests/test_health_loop.py) (2 new in-flight-tick race cases), and [`test_action_submit.py`](tests/test_action_submit.py) (4 new in `TestBridgeToCapital`). Total Nexus tests: 1182
- Add [`docs/TechnicalDebt.md`](docs/TechnicalDebt.md) entries TD-027 (bare `assert` in `OutcomeProcessor` fill handlers, fires under `python -O`), TD-028 (loop `.stop()` cancel-but-no-join pattern across OutcomeLoop / PredictLoop / TimerLoop / HealthLoop, with round-9 refinement on shutdown EXIT orphan-context cleanup), TD-029 (`_grow_position` defensive gap on `trade_id=None`), TD-030 (`_poll_until_terminal` misleading double-dispatch structure), TD-031 (`PraxisOutbound` trade_id/command_id contract not asserted), TD-032 (`on_startup` `capital_available` shows gross budget, not net of deployed), TD-033 (multi-process WAL TOCTOU between `StateStore.__init__` and `recover()`)

## v0.33.0 on 29th of April, 2026

- Add required `avg_cost_basis: Decimal` field to [`Position`](nexus/core/domain/position.py) — VWAP-with-fees cost per unit, maintained by [`OutcomeProcessor._grow_position`](nexus/infrastructure/praxis_connector/outcome_processor.py) on every entry FILL. WAL codec encodes the new field; legacy snapshots without it decode to `entry_price` (best-effort default — fees lost). Praxis-imported positions in [`StartupSequencer._import_praxis_position`](nexus/startup/sequencer.py) initialize the same way (BLOCKER-A.1)
- Add [`CapitalController.order_exit(strategy_id, cost_basis_released)`](nexus/core/capital_controller/capital_controller.py) lifecycle method that decrements `position_notional` and `per_strategy_deployed[strategy_id]` by the released cost basis, with `INVARIANT_BREACH` guard against driving `position_notional` negative. Mirrors `order_fill` for the EXIT direction (BLOCKER-A.1)
- Wire EXIT FILL through `order_exit` in [`OutcomeProcessor._handle_fill`](nexus/infrastructure/praxis_connector/outcome_processor.py) — when `not context.is_entry`, call `order_exit` before `_update_position_on_fill()` / `_reduce_position()` so capital aggregates are decremented by `position.avg_cost_basis * fill_size` without mutating positions on `INVARIANT_BREACH`. Closes the round-trip conservation gap that locked accounts after ~7 round-trips at 80% utilization (or ~1.5 round-trips per strategy at the per-strategy budget cap) — eleven audit rounds missed this because EXIT-FILL tests asserted `success` and `realized_pnl` but never `state.capital.position_notional` or `per_strategy_deployed` (BLOCKER-A.3)
- Add `pending_exit` increment in [`submit_actions`](nexus/strategy/action_submit.py) — for EXIT actions with status SUBMITTED, after `praxis_outbound.send_command` returns, `state.positions[action.trade_id].pending_exit += ctx.order_size` (matches the size that `translate_to_trade_command(... size=context.order_size ...)` actually submitted, so the validator's `INTAKE_EXIT_SIZE_EXCEEDS_REMAINING` defense compares like-with-like). Defensive `is None` guards against missing position. The defense at [`intake_stage.py:248-256`](nexus/core/validator/intake_stage.py) was structurally unreachable pre-fix because the field was decremented in 4 sites and incremented nowhere — strategies could submit two overlapping EXITs each sized at `position.size`, the second oversold and crashed `_reduce_position`. Eleven audit rounds missed this because the existing intake-stage test pre-populated `pending_exit=0.2` directly in setup (BLOCKER-C)
- Replace the warn-only "position in Nexus but not in Praxis" loop in [`StartupSequencer._reconcile_capital`](nexus/startup/sequencer.py) with an eviction loop that pops every Nexus-only `trade_id` and logs at WARNING with the strategy_id / size / entry_price. Closes the recovery path that left `per_strategy_deployed` rebuilt from stale entries → `'Per-strategy deployed attribution mismatch for non-flat state'` denial that rejected every ENTER for the rest of the boot. Reachable on every paper-trade boot following any unclean shutdown where positions cycled through the Praxis WS path (BLOCKER-B.3, B.4)
- Capital-aggregate post-conditions on every EXIT-touching test in [`test_outcome_processor.py`](tests/test_outcome_processor.py) (`TestOutcomeProcessorReject` EXIT cases, `TestRiskMetricsRecalculation::test_exit_fill_*`, `TestCancelUsesRemainingSize`). Adds shared `_prime_open_position_capital(ctrl, strategy_id, size, avg_cost_basis)` helper. Closes the lens-gap that hid the EXIT-FILL conservation bug across eleven audit rounds (BLOCKER-A.7)
- Add 22 new tests across [`test_outcome_processor.py`](tests/test_outcome_processor.py) (7 in `TestCapitalConservationOnExit` — full round-trip, partial exit, VWAP-across-two-fills via real CapitalController + pure unit, 8-cycle no-leak proof, INVARIANT_BREACH guard, multi-position cross-strategy attribution isolation), [`test_position.py`](tests/test_position.py) (4 — `avg_cost_basis` invariants), [`test_wal_codec.py`](tests/test_wal_codec.py) (1 round-trip + 1 legacy decode), [`test_startup_sequencer.py`](tests/test_startup_sequencer.py) (3 — Nexus-only eviction, both-present regression, mixed scenario), and [`test_action_submit.py`](tests/test_action_submit.py) (6 in `TestPendingExitIncrement` — increment on SUBMITTED, no-increment on REJECT/SUBMIT_FAILED/ENTER/missing-trade_id, cross-tick overlap denial via real `validate_intake_stage`). Total Nexus tests: 1199
- Replace silent-None sentinel on zero `avg_cost_basis` in [`OutcomeProcessor._reduce_position`](nexus/infrastructure/praxis_connector/outcome_processor.py) with an explicit `if position.avg_cost_basis == _ZERO` check + WARNING log naming the trade_id / fill_size / entry_price. Pre-fix `cost_basis_released if cost_basis_released > _ZERO else None` silently bypassed the capital decrement when avg_cost_basis was zero (legacy snapshot, placeholder reused as real, or invariant break in `_grow_position`); post-fix the operator sees the leak and the underlying invariant break is investigable
- Gate `_compute_exit_cost_basis` on `outcome.fill_size > position.size` — return `None` so `order_exit` is skipped before `_reduce_position` raises `RuntimeError`. Pre-fix capital was already decremented by `position.avg_cost_basis * fill_size` at the time of the raise, leaving `CapitalState` divergent from `positions` (capital missing the closed position's cost basis while the position still held its full size). Inverse-case gap surfaced in PR #47 review
- Gate `_compute_exit_cost_basis` on `position.strategy_id != context.strategy_id` — return `None` and log WARNING. Pre-fix a context wired to a different strategy than the position's owner would decrement the wrong `per_strategy_deployed` bucket → attribution drift / underflow on the owning strategy
- Update `_compute_exit_cost_basis` docstring to distinguish the two return-None classes: cases where the EXIT legitimately should not decrement capital (avg_cost_basis=0, overfill, strategy_id mismatch) versus cases that suppress the capital decrement only because the position layer will still raise (missing trade_id, absent position) (Copilot PR #47 review)
- Update `CapitalController.order_exit` docstring — exit fees are NOT touched here AND are NOT deducted from `realized_pnl` (which is gross PnL `(fill_price - entry_price) * fill_size`); pre-fix wording implied fees appeared in realized_pnl (Copilot PR #47 review)
- In `_reduce_position`, update the `avg_cost_basis == _ZERO` WARNING text to reflect that `reconcile_at_boot` can no longer recover this case (it now rebuilds from `pos.avg_cost_basis` directly), and add `position_size` / `position_avg_cost_basis` to the extra context for incident response (Copilot PR #47 review)
- In `StartupSequencer._reconcile_capital`, set `nexus_pos.size = qty` after the size-mismatch warning — Praxis is the live truth post-WS-applied state. Pre-fix `position_notional` (Praxis qty) and downstream `per_strategy_deployed` (stale Nexus qty) diverged → permanent `'Per-strategy deployed attribution mismatch for non-flat state'` denial of every subsequent ENTER following any reboot from a crash mid-fill where the persisted Nexus snapshot lagged the WS-applied Praxis state
- In `StartupSequencer._reconcile_capital`, fall back from `nexus_pos.avg_cost_basis == _ZERO` to the Praxis `avg_entry_price` (or `nexus_pos.entry_price` if Praxis price is also zero) so `position_notional` stays in the right ballpark and `order_exit` does not fire `INVARIANT_BREACH` on the next EXIT fill for legacy snapshot positions (Copilot PR #47 review)
- Persist the fallback price onto `nexus_pos.avg_cost_basis` in `_reconcile_capital` so downstream `reconcile_at_boot` and `_compute_exit_cost_basis` see a consistent non-zero cost basis (Copilot PR #47 review)
- In [`CapitalController.reconcile_at_boot`](nexus/core/capital_controller/capital_controller.py), apply the same `avg_cost_basis == _ZERO` fallback to `entry_price` (with WARNING) when rebuilding `per_strategy_deployed`, so a Position whose `avg_cost_basis` was never repopulated does not under-attribute deployed capital and immediately re-trigger the attribution-mismatch denial (Copilot PR #47 review)
- Switch `submit_actions` `pending_exit` increment from `action.size` to `ctx.order_size` — the latter is what `translate_to_trade_command(... size=context.order_size ...)` actually sends. If `build_context` rounds or clamps the size away from `action.size`, `pending_exit` now matches what was validated and submitted, keeping the `INTAKE_EXIT_SIZE_EXCEEDS_REMAINING` defense reliable (Copilot PR #47 review)
- Add `per_strategy_deployed[strategy_id]` guard in [`CapitalController.order_exit`](nexus/core/capital_controller/capital_controller.py) — return `INVARIANT_BREACH` when `cost_basis_released` exceeds the per-strategy bucket, before the global `position_notional` check would have allowed the mutation. Pre-fix only `position_notional` was checked; a missing or stale per-strategy entry could be driven negative (or dropped via `_adjust_strategy_deployed`), leaving `position_notional` decremented while attribution was corrupted, and the next `check_and_reserve` would fire the attribution-mismatch denial with no recovery path until reboot (Copilot PR #47 review)
- Add [`docs/TechnicalDebt.md`](docs/TechnicalDebt.md) entries TD-040 (WAL codec back-compat: legacy snapshot decodes `avg_cost_basis` from `entry_price`, losing entry-fee portion of cost basis until positions cycle through fresh ENTER FILLs) and TD-041 (`pending_exit` increment in `submit_actions` lacks lock protection symmetric with the existing unlocked decrement sites in `OutcomeProcessor`; race-direction analysis shows the only failure mode is over-denial which is operationally safe today)

## v0.34.0 on 30th of April, 2026

- Add `strategy_id: str | None = None` field to [`TradeCommand`](nexus/infrastructure/praxis_connector/trade_command.py) — wired through [`PraxisOutbound.send_command`](nexus/infrastructure/praxis_connector/praxis_outbound.py) into Praxis's `Trading.submit_command(strategy_id=...)` kwarg. NEW_ORDER carries the owner; AMEND_ORDER and CANCEL_ORDER reject `strategy_id is not None`. Closes the TD-E missing-kwarg root cause that left every Praxis Position with `strategy_id=None` (MAJOR-O + TD-E)
- Skip `qty * price` contribution to `praxis_total_notional` in [`StartupSequencer._reconcile_capital`](nexus/startup/sequencer.py) when `_import_praxis_position` returns None — pre-fix the position was dropped from `state.positions` but its notional still inflated `position_notional`; downstream `reconcile_at_boot` rebuilt `per_strategy_deployed` only from `state.positions` (excluding the un-importable) → permanent attribution-mismatch denial of every reboot ENTER while TD-E was open. Closes the documented "self-heal" gap for any reconcile path that depends on `_reconcile_capital` post-fix (MAJOR-O)
- Add `positions_lock: threading.Lock | None` kwarg to [`OutcomeProcessor.__init__`](nexus/infrastructure/praxis_connector/outcome_processor.py) — `_grow_position` and `_reduce_position` wrap their `state.positions` field-assignment + `del` blocks under it (`nullcontext` when None for legacy single-threaded test paths). Pre-fix the lock was created by the launcher and threaded into readers (`_build_strategy_context`) and entry-placeholder writers (`_ensure_entry_position`) but never into `OutcomeProcessor`'s writer path — a concurrent PredictLoop iteration of `state.positions.values()` could fire `RuntimeError: dictionary changed size during iteration`, swallowed by PredictLoop's top-level except → silently dropped strategy ticks (MAJOR-J + TD-U)
- Bump `DEFAULT_TTL_SECONDS` from 30 to 60 in [`CapitalController`](nexus/core/capital_controller/capital_controller.py) so the reservation TTL exceeds the `praxis_outbound._DEFAULT_TIMEOUT` (30s). Pre-fix a slow `send_command` returning at `t == ttl` was followed by `send_order`'s `_purge_expired` removing the reservation; the pop returned None → EXPECTED_MISS → OrderContext never registered → capital permanently stuck in `in_flight_order_notional` (MAJOR-K)
- Reorder [`CapitalController.send_order`](nexus/core/capital_controller/capital_controller.py) — `_reservations.pop` now runs FIRST, then an explicit `now > reservation.expires_at` check (with capital release on truly-past), then `_purge_expired` for housekeeping of OTHER reservations. Boundary equality (`now == expires_at`) now succeeds rather than being lost to the heap pop (TD-S folded into MAJOR-K)
- Route shutdown EXIT REJECTED / CANCELED / EXPIRED non-fill terminals through [`OutcomeProcessor.process`](nexus/infrastructure/praxis_connector/outcome_processor.py) in [`ShutdownSequencer._apply_terminal_outcome`](nexus/startup/shutdown_sequencer.py). Pre-fix the early-return on `not is_fill` left `position.pending_exit` non-zero (incremented at `submit_actions` time); the persisted Position then denied the next boot's first EXIT with `INTAKE_EXIT_SIZE_EXCEEDS_REMAINING`. Post-fix `_handle_reject` / `_handle_cancel` (with `context.is_exit=True`) invoke `_clear_pending_exit` (MAJOR-I + TD-N)
- Add `pending_exit += context.order_size` increment in [`ShutdownSequencer._submit_exit`](nexus/startup/shutdown_sequencer.py) after `send_command` succeeds, mirroring `submit_actions`. Pre-fix the asymmetry meant a multi-EXIT shutdown for the same `trade_id` skipped the `INTAKE_EXIT_SIZE_EXCEEDS_REMAINING` defense and `_clear_pending_exit` (now wired via MAJOR-I) had no inflated value to clear (MAJOR-L)
- Reset `position.pending_exit = _ZERO` for every persisted Position in [`CapitalController.reconcile_at_boot`](nexus/core/capital_controller/capital_controller.py) with WARN if non-zero. In-memory `_orders` is empty post-boot so nothing should still be in flight; the pre-fix stuck value would deny future EXITs via `INTAKE_EXIT_SIZE_EXCEEDS_REMAINING`. Defense-in-depth that closes the boot-orphan REJECTED leak path that bypassed the MAJOR-I shutdown-time clearance (MAJOR-R)
- Add `INTAKE_EXIT_STRATEGY_MISMATCH` denial in [`intake_stage`](nexus/core/validator/intake_stage.py) — EXIT context's `strategy_id` must match the position owner's `strategy_id`. Pre-fix intake validated only `trade_id ∈ state.positions`; on cross-strategy EXIT capital was correctly skipped (via `_compute_exit_cost_basis` strategy_id guard) but `_reduce_position` still ran (decrementing position) and `_update_strategy_risk_state` attributed realized P&L to the WRONG strategy bucket — corrupting `rolling_loss_*` and `high_water_mark` on the non-owner (MAJOR-Q)
- Wire `release_reservation` rollback into [`submit_actions`](nexus/strategy/action_submit.py) — accepts optional `capital_controller` kwarg; on translate / send_command exception, `_release_granted_reservation` invokes `controller.release_reservation(decision.reservation.reservation_id)` so capital does not stay parked until the 30s TTL eviction on deterministic-failure retry storms (config bug, malformed symbol). Closes the `release_reservation`-defined-but-unwired gap that the round-12 audit caught (MAJOR-G)
- Add `positions_lock` kwarg to [`ShutdownSequencer.__init__`](nexus/startup/shutdown_sequencer.py); `_dispatch_shutdown` snapshots `state.positions.values()` under it once per call (filtering by `strategy_id` after release). Adds an early WARNING when `outcome_loop.running` indicates the join-timeout fired. Pre-fix a `_stop_outcome_loop` join timeout could leave the OutcomeLoop mutating positions; the unlocked iteration would fire `RuntimeError: dictionary changed size during iteration`, escape the per-strategy try/except, and terminate shutdown before `_persist_strategy_state` / `_final_checkpoint` ran — data loss (MAJOR-S)
- Subtract `state.capital.per_strategy_deployed.get(strategy_id, _ZERO)` from gross budget in [`ShutdownSequencer._dispatch_shutdown`](nexus/startup/shutdown_sequencer.py) so `StrategyContext.capital_available` mirrors runtime's `_build_strategy_context`. Pre-fix shutdown surfaced the gross strategy budget; strategies that branched on `capital_available` in `on_shutdown` saw an overstated value (MAJOR-T)
- Enforce `rolling_loss_24h/7d/30d` limits in [`evaluate_risk_breach`](nexus/core/validator/risk_stage.py) — `RiskStageLimits` gains `max_rolling_loss_24h/7d/30d` fields; `RiskCheckMetrics` extended with the rolling-loss values; `to_risk_check_metrics` populates them. Wire `state_store.refresh_rolling_losses` into [`HealthLoop`](nexus/core/health_loop.py) via optional `rolling_loss_refresher` kwarg so 24h/7d/30d windows decay as old loss events age out. Pre-fix the fields were tracked, persisted via WAL codec, and `refresh_rolling_losses` existed for decay but no validator stage read them and `refresh_rolling_losses` had zero production callers (MAJOR-H)
- Pre-bind `OutcomeProcessor._positions_cm` in `__init__` to either the lock or `nullcontext()` so `_grow_position` and `_reduce_position` use a single readable `with self._positions_cm:` line instead of an inline ternary (Greybeard pre-PR review)
- Add 36 new tests across [`test_action_submit.py`](tests/test_action_submit.py) (4 — release_reservation rollback variants), [`test_capital_controller.py`](tests/test_capital_controller.py) (3 — `TestSendOrderBoundaryRace` + 4 — `TestReconcileAtBootResetsPendingExit` + 3 — `TestOrderExitInvariants`), [`test_health_loop.py`](tests/test_health_loop.py) (2 — refresher invoked + exception swallowed), [`test_outcome_processor.py`](tests/test_outcome_processor.py) (3 — `TestPositionsLockHonoredByWriter`), [`test_praxis_outbound.py`](tests/test_praxis_outbound.py) (1 + 1 — strategy_id pass-through), [`test_shutdown_sequencer.py`](tests/test_shutdown_sequencer.py) (4 — canceled/expired/submit-exit-increment + capital_available subtract + concurrent mutation), [`test_startup_sequencer.py`](tests/test_startup_sequencer.py) (2 — un-importable position invariants), [`test_trade_command.py`](tests/test_trade_command.py) (5 — strategy_id field invariants), [`test_translate.py`](tests/test_translate.py) (3 — strategy_id propagation), [`test_validator_intake_stage.py`](tests/test_validator_intake_stage.py) (2 — INTAKE_EXIT_STRATEGY_MISMATCH), [`test_validator_risk_stage.py`](tests/test_validator_risk_stage.py) (5 — rolling-loss enforcement). Total Nexus tests: 1254
- Add [`docs/TechnicalDebt.md`](docs/TechnicalDebt.md) entries TD-042 through TD-051 — defer round-12/13/14 audit findings that are not paper-trade-fatal (MODIFY validator-bypass set, `_clear_pending_exit` clamp diagnostic, `pending_exit` durability, `_handle_ack` false-positive WARN, `_handle_cancel` `remaining_size=None` over-clear, `_handle_reject` EXIT-reject WARN, `_handle_fill` post-success exception path, `_purge_expired` periodic invocation, `bridge_to_capital` helper unused by production launcher, realized_pnl exit-fee accounting)

## v0.35.0 on 1st of May, 2026

- Add `max_allocation_per_trade_pct: Decimal` keyword-only argument to [`CapitalController.__init__`](nexus/core/capital_controller/capital_controller.py) — defaults to module constant `MAX_ALLOCATION_PER_TRADE_PCT` (`Decimal('0.15')`) so prior callers are unchanged. The per-trade allocation gate at `check_and_reserve` now reads the instance attribute, letting operators with higher-Kelly strategies raise the cap at construction without forking the controller. Constructor validates the kwarg is a finite, positive `Decimal` (rejects non-`Decimal`, non-finite `NaN` / `±Infinity`, zero, and negative values)
- Add ten tests in [`test_capital_controller.py`](tests/test_capital_controller.py) under `TestMaxAllocationPerTradePctOverride` covering default-matches-constant, higher-override-admits, lower-override-denies, denial-reason-carries-override, non-Decimal-raises, zero-raises, negative-raises, NaN-raises, Infinity-raises, negative-Infinity-raises

## v0.36.0 on 1st of May, 2026

- Add `lookback: int = 1` keyword-only argument to [`produce_signal`](nexus/strategy/signal_producer.py) — default 1 preserves prior `tail(1)` behaviour bit-for-bit; `lookback=N` feeds the last N prepared rows into `sensor.predict`. Caller-side validation raises `TypeError` on non-int / `bool` and `ValueError` on values < 1
- Fix dtype preservation in [`_extract_values`](nexus/strategy/signal_producer.py) — multi-element-array branch previously cast `val[-1]` to `float` unconditionally, demoting `_preds` from `int` to `float` for arrays of length > 1. Now the last element's dtype is preserved regardless of array length, and zero-length arrays are skipped instead of indexing into them
- Add `TestLookback` class (7 tests) and three `TestExtractValues` tests in [`test_signal_producer.py`](tests/test_signal_producer.py); update existing `test_multi_element_array_takes_last` to assert int dtype
## v0.37.0 on 1st of May, 2026

### Add

- Add [`CapitalController.recover_orphaned_order`](nexus/core/capital_controller/capital_controller.py) — defense-in-depth helper for FINAL-MAJOR-01 cross-repo: when the launcher's `process_outcome` hits the no-OrderContext terminal cleanup branch, the helper releases the orphan order's capital aggregates (mirrors `order_reject` / `order_cancel` per-state release without requiring `OrderContext`). Idempotent. Position growth from FILL outcomes is intentionally NOT performed; aggregate release is the priority and the next boot's `_reconcile_capital` adopts Praxis truth for position state. No production caller yet — Praxis follow-up tracked as TD-067
- Add [`CapitalController.lock_cm`](nexus/core/capital_controller/capital_controller.py) returning the internal `_lock` so callers like [`ShutdownSequencer._final_checkpoint`](nexus/startup/shutdown_sequencer.py) can serialise reads of `state.capital.per_strategy_deployed` and the aggregate fields atomically with respect to in-flight controller mutations (FINAL-MAJOR-05)
- Add [`RiskState.lock`](nexus/core/domain/risk_state.py) dataclass field (`init=False`, `repr=False`, `compare=False`) and [`RiskState.lock_cm`](nexus/core/domain/risk_state.py) helper that returns the lock as a context manager (or `nullcontext()` when unset) so the OutcomeProcessor writer, HealthLoop refresher, and validator reader coordinate on the same lock instance, consistent with the `dataclasses.replace()` behavior covered by tests (FINAL-MAJOR-02)
- Add [`derive_strategy_realized_pnl`](nexus/infrastructure/loss_derivation.py) that sums signed cumulative `realized_pnl` per strategy from WAL events; called by `state_store.recover()` so a crash between `append_event` and `append_mutation` does not leave `strategy_realized_pnl` permanently understated and drawdown gates stale (FINAL-TD-01)
- Add `_dedup_by_outcome_id` filter helper in [`loss_derivation.py`](nexus/infrastructure/loss_derivation.py) shared by `derive_rolling_losses` and `derive_strategy_realized_pnl` so a Praxis re-delivery of an already-emitted terminal outcome does not double-count via duplicate STRATEGY_EVENT entries (FINAL-TD-02)
- Add `outcome_id: str = ''` field to [`StrategyEvent`](nexus/infrastructure/strategy_event.py); WAL event codec bumped to v2 with the new field; v1 payloads decode to `outcome_id=''` and are NOT deduped (legacy WAL contract). Versions are now named constants (`_CODEC_VERSION_1`, `_CODEC_VERSION_LATEST`, `_EVENT_CODEC_VERSION_1`, `_EVENT_CODEC_VERSION_2`, `_EVENT_CODEC_VERSION_LATEST`)
- Add `_SUB_ULP_TOLERANCE = Decimal('1E-12')` constant in [`capital_controller.py`](nexus/core/capital_controller/capital_controller.py) shared by `order_exit` (FINAL-MAJOR-06) and `order_fill` (FINAL-MAJOR-09); absorbs sub-ULP rounding noise without weakening the underlying invariants
- Add `_wal_lock` to [`StateStore`](nexus/infrastructure/state_store.py) wrapping `append_mutation`, `append_event`, AND `checkpoint` (snapshot+truncate) as one atomic critical section so concurrent writers from OutcomeLoop and shutdown thread cannot produce duplicate `_sequence` records or torn appends (FINAL-MAJOR-04)
- Add `positions_lock` parameter to [`submit_actions`](nexus/strategy/action_submit.py) so the EXIT `position.pending_exit += ctx.order_size` write at line ~250 runs under the lock; closes the lost-update race with the OutcomeProcessor decrementer (FINAL-MAJOR-03)
- Add `capital_controller` parameter to [`ShutdownSequencer.__init__`](nexus/startup/shutdown_sequencer.py); `_final_checkpoint` now wraps the checkpoint call in `positions_lock + CapitalController.lock_cm()` so a still-alive OutcomeLoop after `_stop_outcome_loop` join_timeout cannot race the snapshot serializer's iteration of `state.positions`, `state.risk.per_strategy`, or `state.capital.per_strategy_deployed` (FINAL-MAJOR-05)
- Add new test classes covering the FINAL-MAJOR-01..10 + FINAL-TD-01/02 fixes: `TestRecoverOrphanedOrder`, `TestFinalMajor02RiskLockCoverage`, `TestFinalMajor03PendingExitLockCoverage`, `TestFinalMajor04WalAppendAtomicity`, `TestFinalMajor05CheckpointLockCoverage`, `TestFinalMajor06ExitBoundaryTolerance`, `TestFinalMajor08RealizedPnlIsNetOfFees`, `TestFinalMajor09OrderFillAttributionLockstep`, `TestFinalMajor10RecoverDecaysRollingLossesEvenWithEmptyEvents`, `TestFinalTd01RecoverDerivesStrategyRealizedPnl`, `TestFinalTd02OutcomeIdDedup`. The FINAL-MAJOR-06 sweep test exercises the full 200×19 = 3800-pair `(notional, size)` grid and asserts every triggering pair (>100 pairs) succeeds post-fix
- Add 15 deferred-finding entries (TD-052..TD-066) to [`docs/TechnicalDebt.md`](docs/TechnicalDebt.md); subsequently pruned TD-052 and TD-053 (resolved by FINAL-TD-01 / FINAL-TD-02 within the same release) and corrected TD-057 / TD-058 / TD-066 closure-claims that overstated transitive closure by FINAL-MAJOR-02
- Add 4 pre-PR review TD entries (TD-067, TD-069, TD-070, TD-071) to [`docs/TechnicalDebt.md`](docs/TechnicalDebt.md) covering the dead-code helper, empty-string sentinel, decoder duplication, and unlocked recover writes. TD-068 (dataclass-bypass attribute) was opened then resolved within this release by promoting `RiskState.lock` to a proper dataclass field

### Fix

- Fix `state.risk.per_strategy` cross-thread access. Pre-fix the OutcomeLoop writer, HealthLoop refresher, validator's `to_risk_check_metrics` reader, and the WAL serializer iterated/mutated the dict without coordination — a fresh-strategy insert during property iteration raised `RuntimeError: dictionary changed size during iteration`, silently swallowed by HealthLoop and breaking rolling-loss decay. Post-fix all four call sites coordinate on `state.risk.lock` (FINAL-MAJOR-02)
- Fix `position.pending_exit +=` lost-update race on both shutdown EXIT (`ShutdownSequencer._submit_exit`) and runtime EXIT submit paths (`submit_actions`). The OutcomeProcessor decrementer (`_reduce_position` / `_clear_pending_exit`) reads, modifies, and writes the same field — concurrent writers could lose the update via torn read-modify-write and undermine the validator's `INTAKE_EXIT_SIZE_EXCEEDS_REMAINING` defense within the same tick (FINAL-MAJOR-03)
- Fix WAL append atomicity. Pre-fix `_sequence += 1` (a 2-bytecode read-modify-write) and `WAL.append` (open + `_find_valid_end` + truncate + write + fsync — a TOCTOU on file size) were unprotected. Concurrent appenders from OutcomeLoop and shutdown thread could produce duplicate sequence numbers and torn appends. `checkpoint` now also runs under the same lock so snapshot+truncate is atomic with appends (FINAL-MAJOR-04)
- Fix `_final_checkpoint` serializer iteration race on shutdown thread. Pre-fix `serialize_state` iterated `state.positions`, `state.risk.per_strategy`, `state.capital.per_strategy_deployed`, and read `CapitalState` aggregates without any lock — a still-alive OutcomeLoop worker after `_stop_outcome_loop` join_timeout could trigger `RuntimeError: dictionary changed size during iteration` and lose the persisted shutdown HALTED mode. Post-fix wraps the call in `positions_lock + CapitalController.lock_cm()` (FINAL-MAJOR-05)
- Fix `order_exit` strict `>` boundary trip on sub-ULP overshoots from `_compute_exit_cost_basis`'s `(N/q) * q` round-trip. Verified single-fill repro `(q=Decimal('3'), N=Decimal('5'))` produces `5.000...001 > 5` at default Decimal precision; pre-fix returned `INVARIANT_BREACH` and silently dropped the EXIT FILL on the capital side. Post-fix uses `_SUB_ULP_TOLERANCE` (1E-12 quote) on the comparison and clamps the actual aggregate decrement via `min()` so `position_notional` and `per_strategy_deployed` never go negative (FINAL-MAJOR-06)
- Fix `_reduce_position` to subtract `outcome.actual_fees` from `realized_pnl`. Pre-fix returned GROSS realized_pnl that flowed verbatim into `strategy_realized_pnl`, `cumulative_realized_pnl`, equity, equity_hwm, and the rolling-loss windows; rolling-loss / drawdown gates fired LATER than they should by the cumulative fee total. On testnet `fee_rate=0` so unobserved at MMVP today; mainnet-fatal once `fee_rate` flips non-zero. Post-fix the entire risk derivation chain reflects NET PnL (FINAL-MAJOR-08)
- Harden `order_fill` fee-deficit check against sub-ULP precision drift between `proportional_estimated` (computed via subtraction round-trip) and `actual_fees` (computed via direct division). On awkward notional/fee ratios (e.g. notional=7, fee=1) the deficit can accumulate sub-ULP and trip `EXPECTED_MISS` cosmetically. Post-fix uses `_SUB_ULP_TOLERANCE` on the deficit check; real deficits (>1E-12 quote) still hard-fail. NOTE: the audit finding labeled FINAL-MAJOR-09 (cumulative attribution drift via `pre_fill_remaining - updated.remaining_total` feeding `working_order_notional` / `per_strategy_deployed`) was empirically disproven at default Decimal precision (the round-trip subtraction has cancellation properties that keep attribution in lockstep with total_deployed across realistic multi-partial scale-ins; the strict equality denial does not fire). The actual reachable failure mode the audit's R17-C MAJOR-ND analysis pointed at is the `order_exit` boundary trip closed by FINAL-MAJOR-06's `_EXIT_BOUNDARY_TOLERANCE` clamp. `TestFinalMajor09OrderFillAttributionLockstep` PINS the empirical lockstep so a future arithmetic refactor cannot regress it (FINAL-MAJOR-09)
- Fix `state_store.recover()` to always re-derive `rolling_loss_*` against current time — pre-fix early-returned when WAL had zero events, leaving snapshot's stale `rolling_loss_*` for the validator to read for ~5s after boot before HealthLoop's first refresh. Combined with FINAL-MAJOR-02 (where a silent dict-resize race could fail the first refresh), the staleness extended indefinitely (FINAL-MAJOR-10)
- Fix `state_store.recover()` to also re-derive `strategy_realized_pnl`, `cumulative_realized_pnl`, and `high_water_mark` from STRATEGY_EVENT entries — pre-fix only `rolling_loss_*` was re-derived; on a crash between `append_event` and `append_mutation` the per-strategy realized PnL delta from the lost STATE_MUTATION was permanently dropped, leaving drawdown gates LATER than they should by the missing delta (FINAL-TD-01)
- Fix `derive_rolling_losses` and `derive_strategy_realized_pnl` to deduplicate events by `outcome_id` — pre-fix a Praxis re-delivery of a terminal outcome that was already emitted pre-crash would produce duplicate STRATEGY_EVENT entries and double-count rolling losses. Empty `outcome_id` (legacy v1-codec events) is NOT deduped — preserves the legacy WAL contract (FINAL-TD-02)

### Update

- Update [`CapitalController.order_exit`](nexus/core/capital_controller/capital_controller.py) docstring to reflect the new net-of-fee `realized_pnl` semantics: exit fees ARE deducted from `realized_pnl` at the source in `_reduce_position`, so `strategy_realized_pnl`, `cumulative_realized_pnl`, equity, and rolling-loss windows all reflect NET PnL
- Update [`docs/TechnicalDebt.md`](docs/TechnicalDebt.md) to reflect actual deferred status of TD-057, TD-058, TD-066 — initial entries claimed transitive closure by FINAL-MAJOR-02, but the M02 implementation scoped `state.risk.lock` to the risk dict only and did not extend `positions_lock` coverage to the cited call sites
- Update `pyproject.toml` version `0.36.0` → `0.37.0`

## v0.38.0 on 3rd of May, 2026

### Add

- Add `_processed_outcome_ids` set + `_dedup_lock` to [`OutcomeProcessor`](nexus/infrastructure/praxis_connector/outcome_processor.py); `process(outcome, context)` returns success no-op on a repeated `outcome.outcome_id` so Praxis's planned delivery retry / boot replay-from-spine paths cannot double-mutate capital or position state. In-memory dedup; cross-restart safety lives on the Praxis side via `OutcomeAcked` filtering at boot (round-18 MAJOR-004)
- Add 4 deferred-finding entries (TD-083, TD-084, TD-085, TD-086) to [`docs/TechnicalDebt.md`](docs/TechnicalDebt.md) covering the `OutcomeProcessor` dedup lock TOCTOU contract and the unbounded `_processed_outcome_ids` set growth surfaced during pre-PR review, the `submit_actions` validator-exception leak path raised in PR #57 review, and the dedup-after-mutation defect in `_handle_fill` (capital/position mutated before `append_event`; a raise leaves the dedup set empty so a retry double-mutates) raised by Copilot in PR #57 review
- Add 7 round-18 audit deferred-finding entries (TD-076..TD-082) to [`docs/TechnicalDebt.md`](docs/TechnicalDebt.md) covering: missing end-to-end rolling-loss enforcement test (TD-076); persisted HALTED mode overwritten by HealthLoop after restart (TD-077); boot reconciliation keeps stale `avg_cost_basis` (TD-078); no architectural guard against direct `PraxisOutbound.send_command` callers (TD-079); shutdown does not guarantee `_final_checkpoint` if an earlier step raises (TD-080); `_persist_strategy_state` and `_final_checkpoint` block on disk fsync without timeout (TD-081); validator's `platform_limits_stage` lacks Binance symbol-filter checks (TD-082)
- Add new test classes covering the round-18 fixes: `TestOutcomeIdIdempotency` (round-18 MAJOR-004 part A — dedup happy path, double-mutation guard, failed-attempt does not poison dedup, dedup is per-outcome_id not per-command_id); 2 new tests appended to `TestRecoverOrphanedOrder` (round-18 MAJOR-003 — explicit double-call idempotency, capital restored for new reservation after orphan release); `TestReleaseReservationOnLateValidatorDeny` (round-18 MAJOR-006 — HEALTH/PLATFORM_LIMITS deny after CAPITAL grant releases reservation, pre-CAPITAL deny does not call release, ABORT bypasses validator)

### Fix

- Fix [`submit_actions`](nexus/strategy/action_submit.py) REJECTED branch leaking the granted `Reservation` when a post-CAPITAL validator stage (HEALTH or PLATFORM_LIMITS) denies the action. Validator stage order is `INTAKE → RISK → PRICE → CAPITAL → HEALTH → PLATFORM_LIMITS`; `Pipeline.validate` re-attaches the granted reservation to the denied decision so the caller can release. Pre-fix the REJECTED branch returned without calling `_release_granted_reservation`, leaving the reservation parked in `_reservations` until 30s TTL eviction. Repeated late-stage denies (e.g., spread limit on a degraded venue, headroom failure during sustained rate-limit pressure) starved available capital. Helper docstring expanded to document the third call site alongside the existing translate / send_command exception sites (round-18 MAJOR-006)

## v0.39.0 on 4th of May, 2026

### Add

- Add [`examples/strategies/logreg_binary_evsfd.py`](examples/strategies/logreg_binary_evsfd.py) — primitive long-only `Strategy` driven by a binary classifier signal: ENTER on `_preds == 1`, EXIT on `_preds == 0`, single concurrent position. MARKET orders via `ExecutionMode.SINGLE_SHOT`, 10% capital fraction per ENTER, 60s execution deadline. All other lifecycle hooks are no-ops. Reference price is read from `signal.get('close')`; the strategy returns no action when the signal payload lacks a price or the price is not finite-positive. `_preds` payloads are validated as int 0/1 (no float floor, no bool→int coercion); invalid payloads are logged at WARNING and skipped (issue #33)
- Add [`examples/logreg_binary_evsfd.yaml`](examples/logreg_binary_evsfd.yaml) — operator-deployable manifest pinning a single sensor at `permutation_ids: [21]` from a `BtcLogRegEVSFD` 5000-permutation Limen run with `interval_seconds: 7200` and `capital_pct: 100`. Manifest references the strategy file as `strategies/logreg_binary_evsfd.py` (relative to the manifest's parent directory, which `load_manifest` validates against). Placeholder `account_id`, `allocated_capital`, `capital_pool`, and `sensors[].experiment` path for operator substitution at deploy time
- Add [`examples/README.md`](examples/README.md) — deploy guide for the example bundle: prerequisites for the upstream Limen experiment directory (`metadata.json`, `round_data.jsonl`, `results.csv`), the `experiment_dir=` + `search_strategy=` constructor requirement on `UniversalExperimentLoop`, the importable-SFD-module requirement (`metadata.json[sfd_module]` is reloaded by `Trainer` via `importlib.import_module()` so the SFD class must not live in `__main__`), and the 3-step deploy procedure for the launcher's `MANIFESTS_DIR` and `STRATEGIES_BASE_PATH`

## v0.40.0 on 5th of May, 2026

### Add

- Add [`tests/test_limen_trainer_contract.py`](tests/test_limen_trainer_contract.py) — 10 tests pinning the Limen `Trainer` integration contract Nexus depends on. `TestTrainerInitContract`: parameter names, `experiment_dir` is required + accepts positional, `data` defaults to None + accepts keyword. `TestTrainerTrainContract`: parameter names, `permutation_ids` accepts positional, return type via `typing.get_type_hints` (robust to `from __future__ import annotations`). `TestTrainerPrivateAttributeContract`: `Trainer.__init__` source contains `self._data` and `self._manifest` assignments — pins the private-attribute reads at `nexus/startup/sequencer.py:634, 655`. A future `vaquum_limen` bump that reshapes any of these now fails CI loudly instead of breaking at deploy time

### Update

- Bump `vaquum_limen` git+https pin from `v2.4.3` to `v3.0.1`. Limen v3 introduced a Manifest API rename (RFC-1004) and a class-based `limen.targets` module replacing the v2 function-based target builders; Nexus production code only imports `from limen.experiment.trainer.trainer import Trainer` (`nexus/startup/sequencer.py:15`), and the `Trainer.__init__(experiment_dir, data=None)` + `Trainer.train(permutation_ids: list[int])` signatures are unchanged in v3.0.1, so no Nexus code change is required. SFD modules feeding the Trainer (e.g. the `BtcLogRegEVSFD` example) must be re-trained against v3 limen via `experiment_runner` before deploy; the bundle re-train is upstream and decoupled from this pin bump
- Update `vaquum_limen 2.4.3` → `vaquum_limen 3.0.1` in two `nexus/startup/sequencer.py` comments (lines 631, 651) flagging private-attribute reads (`cached_trainer._data`, `trainer._manifest`); both private attributes still exist on the Limen v3.0.1 `Trainer` class (verified via `inspect.getsource(Trainer.__init__)`) so the re-validate-on-upgrade contract is satisfied

## v0.41.0 on 6th of May, 2026

### Add

- Add [`PredictLoop.tick_once(wired)`](nexus/strategy/predict_loop.py) — synchronous single-shot entry point for schedule-driven callers (e.g. a deterministic backtest replay). Runs one predict cycle (market_data fetch → `produce_signal` → `dispatch_signal` → optional `action_submit`) without requiring `start()` and without scheduling a follow-up `threading.Timer`. The `_running` guard takes `_lock` so the check is atomic with `start()`/`stop()` (and raises `RuntimeError` when the Timer-driven loop is active); the chain body runs without the lock, so callers must not invoke `start()` mid-tick. Propagates exceptions from the chain to the caller instead of swallowing them as `_tick` does — schedule drivers decide how to handle failures rather than relying on a worker log. Mirrors the `tick_once()` shape already exposed by [`OutcomeLoop`](nexus/core/outcome_loop.py) and [`HealthLoop`](nexus/core/health_loop.py). Pure addition: no existing line in `predict_loop.py` was modified, all 9 existing `test_predict_loop.py` tests still pass, and Timer-driven production callers (Praxis runtime) see no behaviour change
- Add [`tests/test_predict_loop_tick_once.py`](tests/test_predict_loop_tick_once.py) — 14 tests covering: synchronous dispatch without `start()`, no follow-up Timer scheduling, `RuntimeError` guard against concurrent Timer-loop use, empty-market-data short-circuit, action_submit forwarding (called/not-called/exception-propagated branches), exception propagation from market_data_provider and dispatch_signal, repeated independent calls, runtime call ordering, a static-source drift sentinel (`test_chain_markers_present_and_ordered_in_both_paths`) that asserts both `_tick` and `tick_once` invoke the chain steps `_extract_kline_size` → `_market_data_provider` → `produce_signal` → `_context_provider` → `dispatch_signal` → `_action_submit` in that order via `inspect.getsource`, and a real runtime parity test (`test_chain_invoked_in_same_order_as_tick`) that drives both `_tick` (with `_running` flipped manually and `_schedule_locked` patched to a no-op so it does not reschedule) and `tick_once` against identical tracking providers, asserting their recorded event lists match byte-for-byte

## v0.42.0 on 6th of May, 2026

### Update

- Bump `vaquum_limen` git+https pin from `v3.0.1` to `v3.0.3`. Limen v3.0.3 ([Vaquum/Limen#500](https://github.com/Vaquum/Limen/pull/500)) makes [`Trainer.__init__`](https://github.com/Vaquum/Limen/blob/v3.0.3/limen/experiment/trainer/trainer.py) hermetic per-experiment: the SFD module named by `metadata.json["sfd_module"]` is now loaded by preferring a `<sfd_module>.py` file inside `experiment_dir` (via `importlib.util.spec_from_file_location` + `module_from_spec`, no `sys.path` mutation) before falling back to `importlib.import_module`. Nexus production code only imports `from limen.experiment.trainer.trainer import Trainer` ([`nexus/startup/sequencer.py:15`](nexus/startup/sequencer.py)), and the public `Trainer.__init__(experiment_dir, data=None)` + `Trainer.train(permutation_ids: list[int])` signatures are unchanged in v3.0.3 (verified by [`tests/test_limen_trainer_contract.py`](tests/test_limen_trainer_contract.py) — all 10 contract tests pass against v3.0.3 unmodified). Unblocks the Praxis paper-trade `BtcLogRegEVSFD` deploy where the SFD `.py` produced by `trainer_prep.py` lives inside the staged `experiment_dir` but is referenced by a bare module name in `metadata.json` — under v3.0.1 this path raised `ModuleNotFoundError` because the experiment_dir was not on `sys.path`; under v3.0.3 the local-file branch loads it directly with no operator-side `PYTHONPATH` wiring required
- Update [`examples/README.md`](examples/README.md) SFD-module guidance to describe Limen v3.0.3's two-stage load (local `<sfd_module>.py` inside `experiment_dir` is now the preferred path; PYTHONPATH-importable module remains supported as the fallback). Pre-v3.0.3 the README only described the PYTHONPATH path, which would mislead operators of `BtcLogRegEVSFD`-style experiment_dir bundles into unnecessary deploy-time `PYTHONPATH` wiring
- Update `vaquum_limen 3.0.1` → `vaquum_limen 3.0.3` in two [`nexus/startup/sequencer.py`](nexus/startup/sequencer.py) comments (lines 631, 651) flagging the private-attribute reads (`cached_trainer._data`, `trainer._manifest`); both private attributes still exist on the Limen v3.0.3 `Trainer` class (verified via `inspect.getsource(Trainer.__init__)`) so the re-validate-on-upgrade contract is satisfied

## v0.43.0 on 7th of May, 2026

### Update

- Bump `vaquum_limen` git+https pin from `v3.0.3` to `v3.0.5` ([Vaquum/Limen#505](https://github.com/Vaquum/Limen/pull/505)). Limen v3.0.5 fixes the experiment-local SFD load to register the loaded module in `sys.modules` (with rollback on `exec_module` failure) so downstream `importlib.import_module(architecture_function.__module__)` lookups (notably Limen's own `Trainer._resolve_model_class`) resolve to the same module instance. Without this, self-contained bundles (e.g. `BtcLogRegEVSFD`-style experiment_dirs whose architecture function is defined inside the SFD file) reached `Trainer.__init__` cleanly but failed at `train()` → `_resolve_model_class()` with `ModuleNotFoundError`. Nexus production code only imports `from limen.experiment.trainer.trainer import Trainer` ([`nexus/startup/sequencer.py:15`](nexus/startup/sequencer.py)); the public `Trainer.__init__(experiment_dir, data=None)` + `Trainer.train(permutation_ids: list[int])` signatures are unchanged in v3.0.5 (verified by [`tests/test_limen_trainer_contract.py`](tests/test_limen_trainer_contract.py) — all 10 contract tests pass against v3.0.5 unmodified). Unblocks the Praxis paper-trade `BtcLogRegEVSFD` deploy at the `_resolve_model_class` path; the cp-to-`manifests/` operator workaround introduced in the v0.42.0 deploy is no longer required
- Update `vaquum_limen 3.0.3` → `vaquum_limen 3.0.5` in two [`nexus/startup/sequencer.py`](nexus/startup/sequencer.py) comments (lines 631, 651) flagging the private-attribute reads (`cached_trainer._data`, `trainer._manifest`); both private attributes still exist on the Limen v3.0.5 `Trainer` class so the re-validate-on-upgrade contract is satisfied

## v0.44.0 on 9th of May, 2026

### Fix

- Fix [`produce_signal`](nexus/strategy/signal_producer.py) to pass the last `lookback` rows to `sensor.predict` as a `polars.DataFrame` instead of `numpy.ndarray`. Previously [`signal_producer.py`](nexus/strategy/signal_producer.py) called `x_train.tail(lookback).to_numpy()` which discarded column names. SFDs that defend against feature-shape drift by selecting `_model_columns` from the live frame at predict time (e.g. the `BtcLogRegEVSFD` bundle, which records `self.model_cols` at fit time and calls `frame.select(self.model_cols)` in `_raw_probs`) then fell into a brittle index-based fallback that crashed when the predict-time frame had columns the training-time frame did not. Specifically: HF dataset training produces 17 input columns, binancial trade-aggregation live-fetch produces 19 (extra: `median`, `iqr`); after the bundle's feature pipeline this lands as 80 predict-time x_train columns vs 78 at training, and the SFD's numpy-branch shape check `x.shape[1] == len(self.training_feature_columns)` (`80 != 78`) skipped the index-based slice and forwarded all 80 columns to `LogisticRegression.predict_proba` which expected 77, raising `ValueError: X has 80 features, but LogisticRegression is expecting 77 features as input` on every sensor tick. Passing the polars DataFrame keeps column names intact so the SFD's `pl.DataFrame` branch (`x = _frame_to_numpy(frame, self.model_cols)`) selects the trained 77 columns by name and ignores extras, which is the contract `_model_columns` was designed for. The `Sensor.predict` callers downstream of `produce_signal` already accept polars DataFrames at training time (the Limen reference architectures' `evaluate(...)` paths feed `data['x_test']` as `pl.DataFrame` from `prepare_data`), so the change harmonises the live and training paths on the same input shape

### Add

- Add [`tests/test_signal_producer.py::test_predict_receives_polars_dataframe_preserving_column_names`](tests/test_signal_producer.py) — pins the Sensor.predict input contract: `data['x_test']` must arrive as a `polars.DataFrame` with the actual training-feature column names intact, not a `numpy.ndarray` and not a polars reconstruction with default `column_<i>` labels. Captures `type(data['x_test'])` and the full column list inside a wrapper around `wired.sensor.predict` and asserts (1) the type is `pl.DataFrame`, (2) at least one column is present, (3) no column name starts with `column_` (polars' default for unnamed columns — a regression like `pl.DataFrame(x_train.tail(...).to_numpy())` would silently strip real names while still passing a naive type+count check), and (4) the captured column list matches `manifest.with_params_override(split_config=(1,0,0)).prepare_data(market_data, round_params)['x_train'].columns` exactly (pins that the captured frame is the actual one passed through, not a reconstructed one). Defends the fix against future re-introduction of `to_numpy()` or any other column-name-stripping transform in the predict path

## v0.45.0 on 10th of May, 2026

### Update

- Bump `vaquum_limen` git+https pin from `v3.0.5` to `v3.0.6` ([Vaquum/Limen#507](https://github.com/Vaquum/Limen/pull/507)). Limen v3.0.6 adds `end_date_limit` and `row_count_limit` (canonical name; legacy `n_rows` retained as alias) parameters to [`HistoricalData.get_spot_klines`](https://github.com/Vaquum/Limen/blob/v3.0.6/limen/data/historical_data.py) and `HistoricalData.get_any_file`. With both `start_date_limit` and `end_date_limit` set the data window is fully closed and reproducible across daily HF dataset snapshot growth — unblocks Praxis paper-trade deploys where `Trainer.train()`'s Pass 1 strict reconstruction check refits with whatever `latest.json` resolves to at run time, and pre-3.0.6 the daily HF snapshot drift made `bars_total`/`backtest_*` metrics deviate from the stored `results.csv` (manifest had only `start_date_limit`, so the window grew with the dataset), tripping `ReconstructionError` at sensor-wiring time. Nexus production code only imports `from limen.experiment.trainer.trainer import Trainer` ([`nexus/startup/sequencer.py:15`](nexus/startup/sequencer.py)); the public `Trainer.__init__(experiment_dir, data=None)` + `Trainer.train(permutation_ids: list[int])` signatures are unchanged in v3.0.6 (verified by [`tests/test_limen_trainer_contract.py`](tests/test_limen_trainer_contract.py) — all 10 contract tests pass against v3.0.6 unmodified). The `end_date_limit` / `row_count_limit` parameters land on the `HistoricalData` data-source method, not on `Trainer` directly, so SFDs adopt them by setting `set_data_source(method=HistoricalData.get_spot_klines, params={..., 'end_date_limit': '<date>'})` in their `manifest()`
- Update `vaquum_limen 3.0.5` → `vaquum_limen 3.0.6` in two [`nexus/startup/sequencer.py`](nexus/startup/sequencer.py) comments (lines 631, 651) flagging the private-attribute reads (`cached_trainer._data`, `trainer._manifest`); both private attributes still exist on the Limen v3.0.6 `Trainer` class so the re-validate-on-upgrade contract is satisfied

## v0.46.0 on 10th of May, 2026

### Fix

- Fix [`produce_signal`](nexus/strategy/signal_producer.py) to support `RuleBasedManifest` SFDs alongside `MLManifest`. Previously the predict path was hard-coded against the `MLManifest.prepare_data` output shape — `data_dict.get('x_train')` for the live train frame and `wired.sensor.predict({'x_test': ...})` for the predict call. `RuleBasedManifest.prepare_data` returns a different shape: `{'train', 'val', 'test', '_alignment', 'strategy'}` with no `x_train` / `x_test` keys (per [`limen/experiment/manifest_core.py`](https://github.com/Vaquum/Limen/blob/v3.0.6/limen/experiment/manifest_core.py)'s `RuleBasedManifest.prepare_data` → `_finalize_rule_based_data` → `split_data_to_rule_based_prep_output`), so every rule-based sensor tick raised `ValueError: prepare_data returned no x_train for sensor <id>` on the empty-train guard, breaking the predict loop for the entire rule-based-SFD class. Post-fix, `produce_signal` branches on `isinstance(wired.limen_manifest, RuleBasedManifest)` and pulls `train` / passes `test` for that path. ML-SFDs are unaffected — they keep using `x_train` / `x_test`. Surfaced when the operator manifest YAML staged a `RuleBasedManifest`-backed stub-strategy bundle alongside the existing `BtcLogRegEVSFD` (`MLManifest`) bundle and four sensors crashlooped on the empty-train guard while the ML one ticked cleanly

### Add

- Add [`tests/test_signal_producer.py::test_predict_called_with_test_key_for_rule_based_manifest`](tests/test_signal_producer.py) and the `_make_wired_rule_based_sensor` fixture that drives the foundational `limen.sfd.foundational_sfd.rule_based` SFD through `Trainer.train([1])` to produce a `WiredSensor` whose `limen_manifest` is a real `RuleBasedManifest`. The test wraps `wired.sensor.predict` with a key-capturing shim, runs `produce_signal`, and asserts (1) the captured `data` dict contains key `'test'` (the rule-based contract) and (2) does NOT contain `'x_test'` — the negative assertion guards against a regression that would silently fall back to the ML key and crash inside the rule-based reference architecture's `predict(data)` which reads `data['test']`. Pinning both halves keeps the manifest-type branching honest

## v0.47.0 on 13th of May, 2026

- Fix [`configure_logging`](nexus/infrastructure/observability.py) to add `structlog.stdlib.ExtraAdder()` to `ProcessorFormatter.foreign_pre_chain` so stdlib `_log.info('msg', extra={...})` payloads survive into JSON output instead of being silently dropped (mirrors [Vaquum/Praxis#105](https://github.com/Vaquum/Praxis/pull/105))
- Add `bound_context(**kwargs)` context manager to [`nexus/infrastructure/observability.py`](nexus/infrastructure/observability.py) wrapping `structlog.contextvars.bound_contextvars`
- Wrap [`submit_actions`](nexus/strategy/action_submit.py) per-iteration body in `with bound_context(strategy_id, action_type, trade_id?, command_id?)` so downstream emits carry per-action correlation via contextvars
- Add `test_stdlib_extras_appear_in_json`, `test_bound_context_binds_and_unbinds`, `test_bound_context_resets_on_exception` in [`tests/test_observability.py`](tests/test_observability.py); add `TestPerActionBoundContext` (4 cases) in [`tests/test_action_submit.py`](tests/test_action_submit.py)

## v0.48.0 on 17th of May, 2026

- Add INFO-level `signal produced` log in [`nexus/strategy/predict_loop.py`](nexus/strategy/predict_loop.py) immediately after every `produce_signal()` call in `tick_once` and `_tick`, BEFORE the strategy runner dispatches the signal. Captures `strategy_id`, `sensor_id`, `predictor_fn_id`, and `values` so operators can see the predictor's actual output on every tick — without this a HOLD-only strategy (every prediction maps to no-action) is indistinguishable from a broken predict path because the strategy itself logs nothing on HOLD. Production-motivated: a logreg sensor in Praxis ran for 22h with zero commands and there was no way to tell from logs whether the model was predicting 0 or the predict path was silently broken
- Add `_values_for_log` helper that coerces every entry to a JSON-safe form for the configured orjson renderer (no `default=` callback, no `OPT_SERIALIZE_NUMPY` flag) so the structured log line cannot drop. `Decimal` → `str`; `numpy.generic` → native via `.item()`; `numpy.ndarray` / `polars.Series` → `.tolist()` if size ≤ 16, else summary string `<sequence type=X size=N>`; any other `__len__`-bearing object over the threshold → `<sequence type=X len=N>`; any remaining non-JSON-native value → `repr(val)`
- Pin behaviors in [`tests/test_predict_loop_tick_once.py`](tests/test_predict_loop_tick_once.py): `test_logs_signal_before_dispatch` asserts the log record carries the expected extras AND was emitted before `dispatch_signal` ran (ordering pinned via `dispatch_signal` side-effect snapshot); `test_values_for_log_passes_native_primitives_through`, `test_values_for_log_coerces_json_unsafe_types`, `test_values_for_log_summarizes_long_series_and_ndarrays`, `test_values_for_log_truncates_long_sequences`, and `test_values_for_log_output_is_orjson_serializable` cover every coerced type and round-trip the helper's output through `orjson.dumps`
