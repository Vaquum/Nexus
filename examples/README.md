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
| `logreg_binary_evsfd.yaml`           | Operator-deployable manifest |
| `strategies/logreg_binary_evsfd.py`  | Strategy file referenced by the manifest |

Manifest and strategy file live under the same parent (`examples/`); the
manifest's `file:` field is `strategies/logreg_binary_evsfd.py` (relative
to the manifest's directory). `nexus.infrastructure.manifest.load_manifest`
validates the strategy file path under the manifest's parent directory and
forbids `..` escapes, so the manifest and strategy must remain colocated
under a single base path. At deploy time, copy `examples/` (or the
manifest + `strategies/` subdir) into a directory served as both
`MANIFESTS_DIR` and `STRATEGIES_BASE_PATH` (or two paths pointing at the
same on-disk tree).

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
> startup. A `metadata.json`-emitting run is configured as
> `UniversalExperimentLoop(experiment_dir='...', search_strategy=GridStrategy(), ...)`.

> **The SFD must be loadable by name at sensor-load time.**
> Limen records `sfd_module = sfd.__class__.__module__` into
> `metadata.json` at training time. From `vaquum_limen` v3.0.5 (Nexus
> 0.43.0+) Trainer resolves the recorded name in two stages: it first
> looks for a `<sfd_module>.py` file inside `experiment_dir` and loads
> it via `importlib.util.spec_from_file_location` + `module_from_spec`
> (no `sys.path` mutation, with the loaded module registered in
> `sys.modules` so downstream `_resolve_model_class` can find it),
> and only falls back to `importlib.import_module` against the running
> interpreter's `sys.path` when no such file is present. Two deploy
> shapes both work end-to-end (the v3.0.3-v3.0.4 era's local-file
> branch reached `Trainer.__init__` cleanly but failed at
> `train()` → `_resolve_model_class` with `ModuleNotFoundError` when
> the architecture function was defined inside the SFD file; v3.0.5
> closed that gap):
>
> 1. **Self-contained experiment_dir (preferred for paper-trade
>    bundles).** Place the SFD `.py` next to `metadata.json` /
>    `round_data.jsonl` inside the staged `experiment_dir`. The
>    launcher needs no `PYTHONPATH` wiring; the local-file branch
>    loads the module hermetically. This is the path produced by
>    Praxis's `trainer_prep.py` for `BtcLogRegEVSFD`-style bundles.
> 2. **PYTHONPATH-importable module (legacy, still supported).**
>    Define the SFD in a regular module reachable on the launcher's
>    `sys.path` (e.g. `my_sfds/round3_sfd.py`) and import it from the
>    training script. The fallback branch resolves it via
>    `importlib.import_module`.
>
> If the SFD subclass is defined inline in a one-off training script
> it resolves to `__main__`, which neither branch can reach — Trainer
> will raise `ModuleNotFoundError` at sensor-wiring. Use one of the
> two shapes above instead.

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

1. Edit `logreg_binary_evsfd.yaml`:
   - Set `account_id` to the target Praxis account.
   - Set `allocated_capital` and `capital_pool` to the operational
     ceiling and operational allocation respectively.
   - Replace `sensors[].experiment` with the absolute path to your
     persisted Limen experiment directory.
2. Copy `logreg_binary_evsfd.yaml` and the `strategies/` subdir into
   the launcher's `MANIFESTS_DIR` mount (which must also be served
   as `STRATEGIES_BASE_PATH`, or `STRATEGIES_BASE_PATH` must point at
   the same on-disk tree). The manifest's `file:` is resolved relative
   to the manifest's parent directory, so the `strategies/` subdir
   must remain a sibling of the manifest.
3. The strategy itself is symbol-agnostic; the launcher's
   `_DEFAULT_SYMBOL` (`BTCUSDT`) drives execution.
