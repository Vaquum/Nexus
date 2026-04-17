# Manifest

This guide explains how Nexus loads strategy configuration, what the manifest schema looks like, and which invariants are enforced before a Manager instance starts.

## What This Guide Covers

- the manifest structure Nexus accepts today
- how strategy files, sensors, timers, and capital allocation are validated
- how YAML becomes immutable runtime configuration

## Why The Manifest Matters

The manifest is the public entry point for most Nexus runtime behavior.

It tells Nexus:

- which strategies to load
- which Limen experiment outputs to wire as Sensors
- how often each Sensor predicts
- which timers each strategy receives
- how the account capital pool is divided across strategies

Nexus treats that configuration as immutable after load. Once startup succeeds, runtime behavior is driven by validated dataclasses rather than ad hoc YAML reads.

## Current Schema

The current loader lives in `nexus/infrastructure/manifest.py`.

A minimal manifest looks like this:

```yaml
capital_pool: 10000

strategies:
  - id: momentum_1
    file: strategies/momentum.py
    capital_pct: 50
    sensors:
      - experiment: ../experiments/btc_2h
        permutation_ids: [3, 8]
        interval_seconds: 900
    timers:
      - id: rebalance
        interval_seconds: 3600
```

## What Gets Built

The loader turns YAML into these immutable structures:

- `Manifest`
- `StrategySpec`
- `SensorSpec`
- `TimerSpec`

That means invalid configuration is rejected before strategy code runs.

## Validation Rules

### Capital Pool

- `capital_pool` must be a finite positive `Decimal`
- it must not exceed the Manager instance `allocated_capital`

This is the first account-level capital boundary.

### Strategies

Each strategy entry must provide:

- `id`
- `file`
- `capital_pct`
- at least one `sensor`

Nexus rejects:

- duplicate strategy ids
- ids with surrounding whitespace
- total `capital_pct` above `100`

### Strategy Files

Strategy files are validated before startup continues.

Nexus requires:

- a relative path, not an absolute path
- a path that stays within the manifest base directory
- an existing file
- valid Python syntax

That check prevents config from pointing outside the intended strategy tree and catches syntax failures before runtime wiring.

### Sensors

Each sensor must provide:

- `experiment`
- `permutation_ids`
- `interval_seconds`

Nexus requires:

- an existing experiment directory
- a non-empty tuple of integer permutation ids
- a positive prediction interval

Relative experiment paths are resolved from the manifest file location.

### Timers

Timers are optional, but when present each timer must provide:

- `id`
- `interval_seconds`

Timer ids must be unique within a strategy and must not carry surrounding whitespace.

## What Happens After Load

Successful manifest load does not yet start strategies. It only creates validated runtime specifications.

Startup then uses those specs to:

1. instantiate strategy classes
2. wire Limen Sensors through `Trainer`
3. register timer specs
4. build the StrategyRunner

That next step is covered in [Startup And Shutdown](Startup-And-Shutdown.md).

## Example Flow

For one strategy entry, the runtime path is:

1. YAML strategy entry is parsed.
2. `StrategySpec` validates the basic shape.
3. `_validate_strategy_files()` confirms the file is safe and syntactically valid.
4. Startup instantiates the strategy from `file`.
5. Each `SensorSpec` is trained into one or more `WiredSensor` entries.
6. Each `TimerSpec` becomes part of the timer registration set.

## Current Scope Notes

The manifest already supports:

- multiple strategies
- multiple sensors per strategy
- optional timers
- per-strategy capital percentages

The manifest does not yet expose every future runtime feature. For example, Cohort-based sensor wiring and stronger experiment-directory sandboxing are still tracked as follow-up work in [Technical Debt](TechnicalDebt.md).

## Read Next

- Continue to [Signal And Strategy Flow](Signal-And-Strategy-Flow.md) for how wired Sensors become live Signals
- Continue to [Validation Pipeline](Validation-Pipeline.md) for how strategy intent is checked before execution
- Continue to [Startup And Shutdown](Startup-And-Shutdown.md) for the full startup sequence around manifest load
