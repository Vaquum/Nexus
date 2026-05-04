# Nexus Examples

Reference manifests + strategies for first-boot deployment, tracking
[#33](https://github.com/Vaquum/Nexus/issues/33).

## `logreg_binary_evsfd`

Primitive long-only strategy driven by a `logreg_binary` foundational
SFD trained on `BtcLogRegEVSFD`. Enters when the binary signal is
`1`, exits when it turns to `0`, holds otherwise. Single concurrent
position per strategy.

### Files

| Path | Purpose |
|------|---------|
| `manifests/logreg_binary_evsfd.yaml`   | Operator-deployable manifest |
| `strategies/logreg_binary_evsfd.py`    | Strategy file referenced by the manifest |

### Prerequisites

The manifest references a Limen experiment directory via
`sensors[].experiment`. That directory is produced upstream by the
user's own [Limen](https://github.com/Vaquum/Limen)
`UniversalExperimentLoop` run with `experiment_dir=<path>` set, and
must contain `metadata.json`, `round_data.jsonl`, and `results.csv`.
Nexus consumes it at startup via
`Trainer(experiment_dir).train(permutation_ids)` — it does not
produce experiments itself (RFC-3001 §X.1.2).

> **Both `experiment_dir=` and `search_strategy=` are required on the
> `UniversalExperimentLoop` constructor.** Without `search_strategy=`
> (e.g. `GridStrategy()` or `RandomStrategy()`) the legacy `run()`
> path writes only `<experiment_name>.csv` to the working directory
> and never emits `metadata.json` or `round_data.jsonl`. Trainer then
> raises `FileNotFoundError` on `<experiment_dir>/metadata.json` at
> startup. The example permutation `62404` was produced by a UEL run
> configured as
> `UniversalExperimentLoop(experiment_dir='...', search_strategy=GridStrategy(), ...)`.

> **The SFD class must live in a real importable Python module.**
> Limen records `sfd_module = sfd.__class__.__module__` into
> `metadata.json` at training time, and Trainer reloads the SFD via
> `importlib.import_module(sfd_module)` at sensor-load time. If the
> SFD subclass is defined inline in a one-off training script it
> resolves to `__main__`, which is not importable from the Praxis
> launcher process — Trainer will raise `ModuleNotFoundError` when
> the launcher boots. Define the SFD in a regular module on
> PYTHONPATH (e.g. `my_sfds/round3_sfd.py`) and import it from the
> training script.

The example pins `permutation_ids: [21]`, the best permutation by
`backtest_total_return_net_pct` from a 5000-permutation
`BtcLogRegEVSFD` run after filtering for `backtest_trades_count >= 50`
and `backtest_max_drawdown_pct > -20` (net_pct=32.8, max_drawdown=-7.1,
trades=96, sharpe_per_bar=0.06, mean_kelly_pct=27.6, expectancy=0.31,
win_rate=60.4, calmar≈4.6). 99 permutations were tied at the top on
every backtest metric; the lowest-numbered tied id was selected for
deterministic reproducibility. Substitute your own permutation_id
when reusing the example for a different experiment.

### Deploy

1. Edit `manifests/logreg_binary_evsfd.yaml`:
   - Set `account_id` to the target Praxis account.
   - Set `allocated_capital` and `capital_pool` to the operational
     ceiling and operational allocation respectively.
   - Replace `sensors[].experiment` with the absolute path to your
     persisted Limen experiment directory.
2. Copy the manifest into the launcher's `MANIFESTS_DIR` mount and
   `strategies/logreg_binary_evsfd.py` into `STRATEGIES_BASE_PATH`.
3. The strategy itself is symbol-agnostic; the launcher's
   `_DEFAULT_SYMBOL` (`BTCUSDT`) drives execution.
