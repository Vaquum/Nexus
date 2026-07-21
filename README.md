<div align="center">
  <br />
  <a href="https://github.com/Vaquum"><img src="https://github.com/Vaquum/Home/raw/main/assets/Logo.png" alt="Vaquum" width="150" /></a>
  <br />
</div>
<br />
<div align="center"><b>Vaquum Nexus turns prediction signals into validated trading decisions, controlled capital deployment, and recoverable manager state.</b></div>

<div align="center">
  <a href="#nexus">Nexus</a> •
  <a href="#what-nexus-is-not">What Nexus Is Not</a> •
  <a href="#capabilities">Capabilities</a> •
  <a href="#first-strategy">First Strategy</a> •
  <a href="#learn-more">Learn More</a>
</div>
<br />
<div align="center">
  <a href="https://github.com/Vaquum/Nexus/tree/main/docs"><img src="https://img.shields.io/badge/docs-nexus-blue" alt="Nexus docs" /></a>
  <a href="https://github.com/Vaquum/Nexus/actions/workflows/pr_checks_tests.yml"><img src="https://github.com/Vaquum/Nexus/actions/workflows/pr_checks_tests.yml/badge.svg" alt="PR tests" /></a>
  <a href="https://github.com/Vaquum/Nexus/actions/workflows/pr_checks_ruff.yml"><img src="https://github.com/Vaquum/Nexus/actions/workflows/pr_checks_ruff.yml/badge.svg" alt="Ruff" /></a>
  <a href="https://github.com/Vaquum/Nexus/actions/workflows/pr_checks_mypy.yml"><img src="https://github.com/Vaquum/Nexus/actions/workflows/pr_checks_mypy.yml/badge.svg" alt="Mypy" /></a>
  <a href="https://github.com/Vaquum/Nexus/actions/workflows/pr_checks_codeql.yml"><img src="https://github.com/Vaquum/Nexus/actions/workflows/pr_checks_codeql.yml/badge.svg" alt="CodeQL" /></a>
</div>

<hr />

<a id="nexus"></a>

# Nexus — Decision layer

*Manifest-driven decision layer between signal generation and trade execution that turns prediction signals into validated trading decisions, controlled capital deployment, and recoverable manager state.*

Nexus consumes prediction series, validates every proposed trade through a six-stage pipeline, and reserves capital atomically before an order leaves the process. Every state mutation is journaled to a CRC-checked write-ahead log with periodic snapshots, so an instance recovers its exact state after a crash. One Nexus instance manages one trading account.

## What Nexus Is Not

Nexus is not:

- a signal generator or model research engine
- a trade execution system or exchange connector
- a market data or backtesting platform

In the wider Vaquum architecture, Origo sits upstream as the data layer and Limen sits upstream as the research engine. Nexus consumes prediction series as Furnace Conduit Arrow frames from any producer — Limen is one such producer, not a dependency. Praxis sits downstream for execution, and Veritas for oversight.

## Capabilities

- Six-stage validation pipeline — intake, risk, price, capital, health, and platform limits — with strict ordering and fail-fast denial
- Atomic check-and-reserve capital control guarded by a single lock, closing TOCTOU races between competing strategies
- Per-trade allocation and total utilization caps with per-strategy capital budgets inside one shared pool
- Three operational modes — ACTIVE, REDUCE_ONLY, HALTED — arbitrated by a sole-writer mode controller with sticky manual, risk, and shutdown holds
- Risk circuit breakers for daily loss and drawdown evaluated on the health tick
- Signal intake from Furnace Conduit prediction series in Arrow format, independent of the producer
- Manifest-driven strategy binding with validation of structure, capital arithmetic, and strategy file syntax
- Strategy contract of seven lifecycle hooks covering persistence, startup, signals, outcomes, timers, and shutdown
- Mark-to-market and outcome loops maintaining equity, drawdown, and rolling 24h/7d/30d per-strategy loss windows
- Ordered startup and shutdown sequencing with boot-time capital reconciliation against Praxis that fails closed on divergence
- Durable persistence through a CRC32-checked write-ahead log, atomic snapshots, and crash recovery by snapshot-plus-WAL replay
- Durable outcome deduplication so a crash replay cannot double-apply a trade outcome

## First Strategy

The first runnable path is a YAML manifest that binds a strategy file to a Furnace Conduit prediction series, validated through the Python API.

1. Install the package from the repository:

```bash
pip install "vaquum-nexus @ git+https://github.com/Vaquum/Nexus.git"
```

Nexus requires Python `>=3.10` and is not published on PyPI — install it straight from the repository. There are no optional extras; the runtime dependencies are `structlog`, `orjson`, `msgpack`, `pyyaml`, `polars`, and `numpy`. Live signal consumption additionally reads the Conduit serving manifest from `/opt/conduit` and OHLCV Arrow frames from `/opt/arrow` (both paths configurable); neither mount is needed for the steps below. Report security issues through the routes in the Vulnerabilities section of this README.

2. Fetch the runnable example:

```bash
git clone https://github.com/Vaquum/Nexus.git
cd Nexus/examples
```

3. Read the manifest — it binds a strategy file to a Conduit series and splits the capital pool:

```yaml
account_id: example_acct
allocated_capital: 10000
capital_pool: 10000
strategies:
  - id: logreg_binary_evsfd
    file: strategies/logreg_binary_evsfd.py
    signal:
      series: time_15m
      interval_seconds: 900
      stale_policy: skip
      name: BTCUSDT 15m up early
    capital_pct: 100
```

4. Read the strategy's decision hook — the loader requires a class named `Strategy`, and `on_save`, `on_load`, `on_startup`, `on_outcome`, `on_timer`, and `on_shutdown` are also required abstract methods (this snippet is adapted from [`logreg_binary_evsfd.py`](https://github.com/Vaquum/Nexus/blob/main/examples/strategies/logreg_binary_evsfd.py)):

```python
class Strategy(Strategy):

    def on_signal(
        self,
        signal: Signal,
        params: StrategyParams,
        context: StrategyContext,
    ) -> list[Action]:
        prediction = signal.get('_preds')

        if prediction == 1 and not context.positions:
            return self._enter(signal, context)

        if prediction == 0 and context.positions:
            return self._exit(context)

        return []
```

5. Validate the manifest and configure the instance identity:

```python
from decimal import Decimal
from pathlib import Path

from nexus import InstanceConfig
from nexus.infrastructure.manifest import load_manifest

manifest = load_manifest(Path('logreg_binary_evsfd.yaml'))
config = InstanceConfig(
    account_id='example_acct',
    venue='binance_spot',
    capital_pct={'logreg_binary_evsfd': Decimal('100')},
)
```

Beyond validation, a live instance is wired by a launcher: `StartupSequencer` recovers state from snapshot and WAL, `PredictLoop` polls Conduit for predictions, and the Praxis connector routes orders out and trade outcomes back. Nexus runs standalone for strategy development and manifest validation; live order flow requires a Praxis deployment.

## Risk Boundary

Nexus is research software. Validation, capital-control, and operational-mode outputs are not investment advice, trading advice, execution simulation, regulatory approval, or a promise of future performance. Past performance is not predictive, and trading digital assets can result in total loss of capital.

## Learn more

- Start with the [docs tree](https://github.com/Vaquum/Nexus/tree/main/docs) — Nexus has no published docs site yet
- See the full release narrative in [CHANGELOG.md](https://github.com/Vaquum/Nexus/blob/main/CHANGELOG.md)
- Run the complete example in [logreg_binary_evsfd.yaml](https://github.com/Vaquum/Nexus/blob/main/examples/logreg_binary_evsfd.yaml) and [logreg_binary_evsfd.py](https://github.com/Vaquum/Nexus/blob/main/examples/strategies/logreg_binary_evsfd.py)
- Read the validation contract in [nexus/core/validator](https://github.com/Vaquum/Nexus/tree/main/nexus/core/validator)
- Follow capital reservation and the order lifecycle in [nexus/core/capital_controller](https://github.com/Vaquum/Nexus/tree/main/nexus/core/capital_controller)
- Trace persistence and recovery through [wal.py](https://github.com/Vaquum/Nexus/blob/main/nexus/infrastructure/wal.py), [snapshot.py](https://github.com/Vaquum/Nexus/blob/main/nexus/infrastructure/snapshot.py), and [state_store.py](https://github.com/Vaquum/Nexus/blob/main/nexus/infrastructure/state_store.py)
- See mode arbitration and risk breakers in [mode_controller.py](https://github.com/Vaquum/Nexus/blob/main/nexus/core/mode_controller.py)
- Inspect the order and outcome flow in [nexus/infrastructure/praxis_connector](https://github.com/Vaquum/Nexus/tree/main/nexus/infrastructure/praxis_connector)
- Review open debt in [TechnicalDebt.md](https://github.com/Vaquum/Nexus/blob/main/docs/TechnicalDebt.md)
- Contribute through [open issues](https://github.com/Vaquum/Nexus/issues) and the [Developer docs](https://github.com/Vaquum/dev-docs/blob/main/src/README.md)

## Contributing

Contribution starts through [open issues](https://github.com/Vaquum/Nexus/issues), [docs changes](https://github.com/Vaquum/Nexus/tree/main/docs), or pull requests against `main` — Nexus has no dedicated CONTRIBUTING.md yet. Before contributing, start with the [Developer docs](https://github.com/Vaquum/dev-docs/blob/main/src/README.md).

## Support

Use [GitHub issues](https://github.com/Vaquum/Nexus/issues) for support requests and scope questions — Nexus has no dedicated SUPPORT.md yet.

## Vulnerabilities

Report vulnerabilities privately through [GitHub Security Advisories](https://github.com/Vaquum/Nexus/security/advisories/new). Do not report vulnerabilities through public issues.

## Citations

Published work should cite:

Vaquum Nexus [Computer software]. (2026). Retrieved from [GitHub](https://github.com/Vaquum/Nexus).

## License

[MIT License](https://github.com/Vaquum/Nexus/blob/main/LICENSE).
