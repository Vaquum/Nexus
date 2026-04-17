# Nexus Docs

This page is the routing hub for the Nexus docs. Use it to choose the right path based on what you are trying to do.

## Nexus In One Page

Nexus is the decision layer between Limen and Praxis. It accepts strategy actions, applies deterministic validation and capital controls, translates admissible actions into execution commands, and keeps per-account Manager state recoverable through WAL, snapshots, replay, and startup recovery.

Nexus does not do upstream market-data research or downstream venue execution. In the wider Vaquum architecture, Limen sits upstream for research and signal generation, Praxis sits downstream for execution, and Veritas sits alongside the stack for oversight and audit.

## Start Here

### If You Are New To Nexus

1. Read the [product home page](../README.md)
2. Read the [developer docs entry](Developer/README.md)
3. Review the documentation contract in [Developer/Documentation-System.md](Developer/Documentation-System.md)
4. Review current implementation limits and follow-up work in [Technical Debt](TechnicalDebt.md)

### If You Want To Understand The System Boundary

1. Start with the [product home page](../README.md)
2. Continue to [Developer/Documentation-System.md](Developer/Documentation-System.md)
3. Review [Technical Debt](TechnicalDebt.md) to see what is already implemented versus still stubbed or deferred

### If You Want To Contribute Or Maintain

1. Start with [Developer/README.md](Developer/README.md)
2. Read the docs contract in [Developer/Documentation-System.md](Developer/Documentation-System.md)
3. Review current known code and integration gaps in [Technical Debt](TechnicalDebt.md)
4. Use the shared [Vaquum Developer Docs](https://github.com/Vaquum/dev-docs/blob/main/src/README.md) for organization-wide conventions

## How Nexus Flows

1. Upstream systems such as Limen produce signals or strategy intent.
2. A Nexus Manager instance accepts actions for one trading account.
3. Nexus applies intake, risk, price, capital, health, and platform validation.
4. Allowed actions reserve capital, update tracked lifecycle state, and translate into Praxis trade commands.
5. Praxis returns outcomes that update capital, positions, and risk state.
6. Nexus persists state through write-ahead logging and snapshots, then rebuilds state through replay during recovery.
7. Startup, reconciliation, and shutdown sequencing keep the instance operationally safe.

## Docs Map

- `Overview`: [Product Home](../README.md), [this docs hub](README.md)
- `Developer`: [Developer Guidelines](Developer/README.md), [Documentation System](Developer/Documentation-System.md), [Technical Debt](TechnicalDebt.md), plus external [Vaquum Developer Docs](https://github.com/Vaquum/dev-docs/blob/main/src/README.md), [Making Release](https://github.com/Vaquum/dev-docs/blob/main/src/Making-Release.md), and [Semantic Versioning](https://github.com/Vaquum/dev-docs/blob/main/src/Semantic-Versioning.md)
- `Guides`, `Reference`, and `Packages`: planned as the next documentation slices described in [Developer/Documentation-System.md](Developer/Documentation-System.md)

## Product Boundary

### Nexus Owns

- per-account Manager runtime state
- deterministic action validation and safety gating
- capital reservation, order lifecycle tracking, and strategy budget enforcement
- action translation into Praxis trade commands
- state persistence, replay, and crash recovery
- startup, reconciliation, and shutdown sequencing inside the decision layer

### Nexus Does Not Own

- upstream market-data collection and alpha research
- strategy experimentation and backtesting
- downstream venue adapters and order execution infrastructure
- system-wide oversight, audit, and governance

## Read Next

- For the product boundary and capability summary, continue to the [product home page](../README.md)
- For contribution and maintenance workflow, continue to [Developer/README.md](Developer/README.md)
- For the docs architecture and rewrite plan, continue to [Developer/Documentation-System.md](Developer/Documentation-System.md)
- For current implementation gaps, continue to [Technical Debt](TechnicalDebt.md)
