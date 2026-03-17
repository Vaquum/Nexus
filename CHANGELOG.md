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

- Add [`enums.py`](nexus/core/domain/enums.py) with `OperationalMode`, `OrderSide`, `BreachLevel`
- Add [`position.py`](nexus/core/domain/position.py) with mutable `Position` dataclass (trade_id, strategy_id, size, entry_price, pending_exit)
- Add [`capital_state.py`](nexus/core/domain/capital_state.py) with mutable `CapitalState` dataclass and derived `available` property
- Add [`risk_state.py`](nexus/core/domain/risk_state.py) with `RiskState` and `StrategyRiskState` (instance-level losses derived from per-strategy state)
- Add [`operational_mode.py`](nexus/core/domain/operational_mode.py) with `ModeState` and `StrategyModeState` (composes `ModeState`)
- Add [`instance_state.py`](nexus/core/domain/instance_state.py) composing all state with `from_config` factory
- Wire [`nexus/core/domain/__init__.py`](nexus/core/domain/__init__.py) re-exports
- Add 34 tests covering enums, position, capital state, risk state, operational mode, and instance state composition
