# Changelog

## v0.9.0 on 21st of March, 2026

- Add instance-level drawdown state fields to `RiskState`: starting capital, cumulative realized/unrealized P&L, equity/HWMs, current drawdowns, and max drawdown metrics
- Add deterministic recompute and update triggers for drawdown metrics via `recompute_drawdown_metrics`, `update_cumulative_realized_pnl`, and `update_unrealized_pnl`
- Extend WAL risk-state codec to persist and recover all drawdown metrics with backward-compatible defaults for older payloads
- Expose drawdown views for validator and diagnostics consumers via `to_risk_check_metrics()` and `to_drawdown_diagnostics()`
- Add comprehensive drawdown tests for formula correctness, monotonic HWM/max drawdown behavior, and codec round-trip coverage (304 total)

## v0.8.0 on 20th of March, 2026

- Add warning log on reservation TTL expiry with reservation_id, strategy_id, total, held duration
- Add 2 tests for expiry logging (288 total)

## v0.7.0 on 20th of March, 2026

- Add [`tracked_order.py`](nexus/core/capital_controller/tracked_order.py) with `OrderLifecycleState` enum (IN_FLIGHT, WORKING) and frozen `TrackedOrder` dataclass
- Add `CapitalController.send_order()` to convert reservation into in-flight order
- Add `CapitalController.order_ack()` to transition in-flight → working
- Add `CapitalController.order_reject()` to release in-flight order capital
- Add `CapitalController.order_fill()` to handle partial/full fills with proportional fee allocation
- Add `CapitalController.order_cancel()` to release working order remaining capital
- Add 45 tests covering TrackedOrder validation, all lifecycle transitions, and concurrency (281 total)

## v0.6.0 on 19th of March, 2026

- Add [`reservation.py`](nexus/core/capital_controller/reservation.py) with frozen `Reservation` dataclass (reservation_id, strategy_id, notional, estimated_fees, created_at, expires_at) and `ReservationResult` outcome type
- Add [`capital_controller.py`](nexus/core/capital_controller/capital_controller.py) with thread-safe `CapitalController` guarding `CapitalState` behind `threading.Lock`
- Add `check_and_reserve()` with 4 ordered atomic checks: per-trade allocation, strategy budget, available capital, total utilization
- Add `release_reservation()` returning locked capital to the available pool
- Add constants `MAX_ALLOCATION_PER_TRADE_PCT` (0.15) and `MAX_CAPITAL_UTILIZATION_PCT` (0.80)
- Add 39 tests covering reservation validation, all check failure paths, release lifecycle, and 10-thread concurrency contention

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

## v0.5.0 on 19th of March, 2026

- Add [`strategy_event.py`](nexus/infrastructure/strategy_event.py) with frozen `StrategyEvent` dataclass (strategy_id, event_type, realized_pnl, timestamp)
- Add `serialize_event` / `deserialize_event` to [`wal_codec.py`](nexus/infrastructure/wal_codec.py) with versioned msgpack format, Decimal-as-string precision
- Add `StateStore.append_event()` for writing `STRATEGY_EVENT` WAL entries alongside `STATE_MUTATION` entries
- Add [`loss_derivation.py`](nexus/infrastructure/loss_derivation.py) with `derive_rolling_losses()` pure function — scans strategy events by 24h/7d/30d windows, sums negative realized P&L per strategy
- Enhance `StateStore.recover()` with two-pass recovery: (1) snapshot + STATE_MUTATION replay, (2) STRATEGY_EVENT scan to re-derive and overwrite rolling loss counters
- Add 44 tests covering strategy event construction/validation, event codec round-trip, loss derivation window boundaries, and enhanced recovery with checkpoint boundary handling

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
