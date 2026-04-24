'''Tests for PT-FIX-9: per-experiment_dir Trainer data cache.

Pre-fix: `_wire_sensors` constructed a fresh `Trainer(experiment_dir)`
for every `SensorSpec`. Each construction calls
`Trainer._manifest.fetch_data_for_env()` which hits the live Hugging
Face dataset. Two `SensorSpec`s sharing the same `experiment_dir`
fetched data twice (potentially yielding different snapshots) and
multiplied the live-fetch cost by `N_sensors`.

Post-fix: `_wire_sensors` caches the first `Trainer` per resolved
`experiment_dir` and constructs subsequent `Trainer`s with
`data=cached._data` so the same frozen slice flows through every
permutation reconstructed from that experiment directory.

These tests exercise the cache by patching
`limen.experiment.trainer.trainer.Trainer` so they do not depend on
the live Hugging Face dataset.
'''

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from nexus.startup.sequencer import StartupSequencer


def _build_sequencer_with_two_sensor_specs(
    tmp_path: Path,
    *,
    shared_experiment_dir: bool,
) -> tuple[StartupSequencer, Path, Path, list[MagicMock]]:
    exp_dir_a = tmp_path / 'exp_a'
    exp_dir_a.mkdir()
    (exp_dir_a / 'metadata.json').write_text(
        json.dumps({'sfd_module': 'limen.sfd.foundational_sfd.random_binary'}),
    )

    if shared_experiment_dir:
        exp_dir_b = exp_dir_a
    else:
        exp_dir_b = tmp_path / 'exp_b'
        exp_dir_b.mkdir()
        (exp_dir_b / 'metadata.json').write_text(
            json.dumps({'sfd_module': 'limen.sfd.foundational_sfd.random_binary'}),
        )

    sensor_a = MagicMock()
    sensor_a.experiment_dir = exp_dir_a
    sensor_a.permutation_ids = (1,)
    sensor_a.interval_seconds = 60

    sensor_b = MagicMock()
    sensor_b.experiment_dir = exp_dir_b
    sensor_b.permutation_ids = (2,)
    sensor_b.interval_seconds = 60

    strat_a = MagicMock()
    strat_a.strategy_id = 'strat-a'
    strat_a.sensors = [sensor_a]

    strat_b = MagicMock()
    strat_b.strategy_id = 'strat-b'
    strat_b.sensors = [sensor_b]

    sequencer = StartupSequencer.__new__(StartupSequencer)
    sequencer._wired_sensors = []
    sequencer._manifest = MagicMock()
    sequencer._manifest.strategies = [strat_a, strat_b]

    return sequencer, exp_dir_a, exp_dir_b, [sensor_a, sensor_b]


def test_shared_experiment_dir_reuses_cached_trainer_data(
    tmp_path: Path,
) -> None:
    '''Two SensorSpecs sharing one experiment_dir → second Trainer ctor
    receives the cached _data via the `data=` kwarg.'''

    sequencer, exp_dir, _, _ = _build_sequencer_with_two_sensor_specs(
        tmp_path,
        shared_experiment_dir=True,
    )

    cached_data = MagicMock(name='frozen_dataframe')

    constructor_calls: list[dict[str, object]] = []

    def fake_trainer_factory(*args: object, **kwargs: object) -> MagicMock:
        constructor_calls.append({'args': args, 'kwargs': dict(kwargs)})
        instance = MagicMock()
        instance._manifest = MagicMock()
        if 'data' not in kwargs or kwargs['data'] is None:
            instance._data = cached_data
        else:
            instance._data = kwargs['data']
        sensor_obj = MagicMock()
        sensor_obj.permutation_id = 1 if len(constructor_calls) == 1 else 2
        sensor_obj.round_params = {}
        instance.train.return_value = [sensor_obj]
        return instance

    with patch(
        'nexus.startup.sequencer.Trainer',
        side_effect=fake_trainer_factory,
    ):
        sequencer._wire_sensors()

    assert len(constructor_calls) == 2
    assert 'data' not in constructor_calls[0]['kwargs']
    assert constructor_calls[1]['kwargs'].get('data') is cached_data
    assert len(sequencer.wired_sensors) == 2


def test_distinct_experiment_dirs_each_get_fresh_fetch(
    tmp_path: Path,
) -> None:
    '''Distinct experiment_dirs build separate Trainer instances; neither
    receives a `data=` kwarg.'''

    sequencer, _, _, _ = _build_sequencer_with_two_sensor_specs(
        tmp_path,
        shared_experiment_dir=False,
    )

    constructor_calls: list[dict[str, object]] = []

    def fake_trainer_factory(*args: object, **kwargs: object) -> MagicMock:
        constructor_calls.append({'args': args, 'kwargs': dict(kwargs)})
        instance = MagicMock()
        instance._manifest = MagicMock()
        instance._data = MagicMock()
        sensor_obj = MagicMock()
        sensor_obj.permutation_id = len(constructor_calls)
        sensor_obj.round_params = {}
        instance.train.return_value = [sensor_obj]
        return instance

    with patch(
        'nexus.startup.sequencer.Trainer',
        side_effect=fake_trainer_factory,
    ):
        sequencer._wire_sensors()

    assert len(constructor_calls) == 2
    assert 'data' not in constructor_calls[0]['kwargs']
    assert 'data' not in constructor_calls[1]['kwargs']


def test_single_sensor_does_not_use_cache_kwarg(tmp_path: Path) -> None:
    '''With one SensorSpec, Trainer is constructed exactly once without `data=`.'''

    exp_dir = tmp_path / 'exp_only'
    exp_dir.mkdir()

    sensor_spec = MagicMock()
    sensor_spec.experiment_dir = exp_dir
    sensor_spec.permutation_ids = (1,)
    sensor_spec.interval_seconds = 60

    strat = MagicMock()
    strat.strategy_id = 'strat'
    strat.sensors = [sensor_spec]

    sequencer = StartupSequencer.__new__(StartupSequencer)
    sequencer._wired_sensors = []
    sequencer._manifest = MagicMock()
    sequencer._manifest.strategies = [strat]

    constructor_calls: list[dict[str, object]] = []

    def fake_trainer_factory(*args: object, **kwargs: object) -> MagicMock:
        constructor_calls.append({'args': args, 'kwargs': dict(kwargs)})
        instance = MagicMock()
        instance._manifest = MagicMock()
        instance._data = MagicMock()
        sensor_obj = MagicMock()
        sensor_obj.permutation_id = 1
        sensor_obj.round_params = {}
        instance.train.return_value = [sensor_obj]
        return instance

    with patch(
        'nexus.startup.sequencer.Trainer',
        side_effect=fake_trainer_factory,
    ):
        sequencer._wire_sensors()

    assert len(constructor_calls) == 1
    assert 'data' not in constructor_calls[0]['kwargs']
