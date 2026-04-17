# Signal And Strategy Flow

This guide explains how live market data becomes a Nexus `Signal`, how strategies receive it, and what the current callback surface looks like.

## What This Guide Covers

- how Limen Sensors are wired into Nexus
- how `PredictLoop` produces Signals from live market data
- which strategy callbacks exist today
- where the current runtime is still intentionally incomplete

## The Flow In One Pass

The current signal path is:

1. Startup wires Limen experiment outputs into `WiredSensor` entries.
2. `PredictLoop` schedules one timer per wired sensor.
3. The loop asks the market-data provider for bars using the sensor's `kline_size`.
4. `produce_signal()` runs Limen feature preparation and prediction.
5. Nexus builds a `Signal` from the prediction output.
6. `StrategyRunner` dispatches that Signal to the target strategy.
7. The strategy returns zero or more `Action` values.

## Wired Sensors

`StartupSequencer._wire_sensors()` uses Limen `Trainer(experiment_dir).train(permutation_ids)` to produce trained sensor callables.

Each resulting `WiredSensor` contains:

- `sensor_id`
- `sensor`
- `limen_manifest`
- `round_params`
- `strategy_id`
- `interval_seconds`

That structure is the bridge between offline Limen experiments and live Nexus runtime.

## Market Data To Signal

The current signal producer lives in `nexus/strategy/signal_producer.py`.

For each predict tick it:

1. verifies market data is not empty
2. applies a Limen params override for a single live split
3. calls `prepare_data(...)`
4. extracts the most recent prepared row
5. calls `sensor.predict(...)`
6. converts the output into a `Signal`

The important design choice is that live prediction uses the same Limen preparation pipeline as training, but only the last row is used for the real-time decision.

## PredictLoop

`PredictLoop` is timer-based and synchronous inside the Manager thread model.

Each sensor has its own cadence. On each tick the loop:

- extracts `kline_size` from the Limen manifest
- fetches rolling market data from the provider
- produces a `Signal`
- asks the context provider for the current `StrategyContext`
- dispatches `on_signal(...)`

If prediction fails, the loop logs the exception and reschedules the next tick rather than stopping the whole runtime.

## Strategy Callback Surface

Strategies inherit from `nexus/strategy/base.py` and implement:

- `on_save`
- `on_load`
- `on_startup`
- `on_signal`
- `on_outcome`
- `on_timer`
- `on_shutdown`

This gives Nexus one control plane for both live decisioning and lifecycle management.

## Current Action Surface

Today the public `Action` dataclass only carries `action_type`.

That means strategies can express category-level intent such as:

- `ENTER`
- `EXIT`
- `MODIFY`
- `ABORT`

But they cannot yet express the full trade payload needed for live end-to-end order submission. The missing action fields are already tracked in [Technical Debt](TechnicalDebt.md) as the key gap between callback intent and fully tradeable decisions.

## Current Strategy Context Limits

The strategy callback surface exists, but the context delivered to strategies is not yet the final product surface.

The current startup and predict paths still have limitations such as:

- partial or placeholder position context in some flows
- capital context derived from manifest budget rather than fully live deployment state in every path
- health mode defaulting to `ACTIVE` when no live health feed is wired

Those are design constraints the docs should state clearly rather than smooth over.

## Why This Layer Exists

Nexus deliberately keeps signal production separate from downstream execution.

Limen owns:

- research
- feature construction
- model training

Nexus owns:

- signal intake
- strategy callback dispatch
- decision validation
- capital-aware control flow

Praxis owns:

- execution
- venue rules
- outcomes

## Read Next

- Continue to [Validation Pipeline](Validation-Pipeline.md) for what happens after a strategy returns actions
- Continue to [Startup And Shutdown](Startup-And-Shutdown.md) for how Sensors and strategies are wired at runtime
- Continue to [Reference Architecture](Reference-Architecture.md) for the wider system boundary
