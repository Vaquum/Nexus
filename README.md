<div align="center">
  <br />
  <a href="https://github.com/Vaquum"><img src="https://github.com/Vaquum/Home/raw/main/assets/Logo.png" alt="Vaquum" width="150" /></a>
  <br />
</div>
<br />
<div align="center"><strong>Vaquum Nexus turns signals and strategy intent into execution-safe trading decisions, controlled capital deployment, and recoverable Manager state.</strong></div>

<div align="center">
  <a href="#nexus">Nexus</a> •
  <a href="#what-nexus-is-not">What Nexus Is Not</a> •
  <a href="#capabilities">Capabilities</a> •
  <a href="#learn-more">Learn More</a>
</div>
<br />
<div align="center">
  <a href="https://scorecard.dev/viewer/?uri=github.com/Vaquum/Nexus"><img src="https://img.shields.io/ossf-scorecard/github.com/Vaquum/Nexus?label=openssf+scorecard&amp;style=flat" alt="OpenSSF Scorecard" /></a>
</div>

<hr />

# Nexus

Nexus is the decision-making layer between Limen and Praxis. It separates user-defined decision logic from non-negotiable controls for capital, risk, price, health, and platform safety.

Each Nexus instance manages one trading account with durable state, deterministic validation, and crash-safe recovery. In the wider Vaquum architecture, Limen sits upstream for research and signal generation, while Praxis sits downstream for execution.

## What Nexus Is Not

Nexus is not:

- a market-data research engine
- a generic strategy backtesting platform
- a venue execution adapter
- a monitoring or oversight layer

Its role is narrower and stricter: take strategy actions, validate them against account and platform constraints, and translate only admissible decisions into execution commands.

## Capabilities

- Per-account Manager instances with isolated venue identity, capital limits, and runtime state
- Multi-stage validation pipeline for intake, risk, price, capital, health, and platform limits
- Atomic capital reservation and order lifecycle control to prevent over-deployment and race conditions
- Strategy-level capital budgets and deployed-capital accounting within a shared account pool
- Instance-level drawdown, realized and unrealized P&L, and rolling loss tracking
- Safety modes for active, reduce-only, and halted operation
- Deterministic handling of enter, exit, modify, abort, and cancel actions through one control plane
- Translation of validated actions into Praxis trade commands, including self-trade-prevention controls
- Durable persistence with write-ahead logging, snapshots, replay, and crash recovery

## Learn More

- Start with the developer docs in [docs/Developer/README.md](docs/Developer/README.md)
- Review current cleanup and follow-up items in [docs/TechnicalDebt.md](docs/TechnicalDebt.md)

## Contributing

The most useful early contributions are documentation improvements, focused bug fixes, and clarifications to runtime and decisioning behavior.

Before contributing, start with [docs/Developer/README.md](docs/Developer/README.md).

## Vulnerabilities

Report vulnerabilities privately through [GitHub Security Advisories](https://github.com/Vaquum/Nexus/security/advisories/new).

## Citations

If you use Nexus for published work, please cite:

Vaquum Nexus [Computer software]. (2026). Retrieved from https://github.com/Vaquum/Nexus.

## License

[MIT License](https://github.com/Vaquum/Nexus/blob/main/LICENSE).
