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
