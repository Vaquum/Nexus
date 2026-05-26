'''Tests for PT-FIX-9: bundle data fetched once per experiment_dir.

Pre-fix: `_wire_sensors` constructed a fresh `Trainer(experiment_dir)`
for every `SensorSpec`. Each construction calls
`Trainer._manifest.fetch_data_for_env()` which hits the live Hugging
Face dataset. Two `SensorSpec`s sharing the same `experiment_dir`
fetched data twice (potentially yielding different snapshots) and
multiplied the live-fetch cost by `N_sensors`.

Post-fix (parallel-reconstruction refactor): `_wire_sensors`
constructs exactly one loader `Trainer(dir)` per unique resolved
`experiment_dir` (WITHOUT a `data=` kwarg) to obtain that bundle's
frozen `_data`, then reconstructs every permutation from that one
slice by passing `data=loader._data` to each per-sensor `Trainer`.
The intent is unchanged: the bundle data is fetched exactly once per
unique `experiment_dir`, and every sensor from that dir reconstructs
from the same frozen slice.

These tests force the inline reconstruction path (worker count of 1)
with the disk cache OFF so the patched `nexus.startup.sequencer.Trainer`
mock applies in the current process; a `ProcessPoolExecutor` would not
see the patch.
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
    sequencer._sensor_wire_max_workers = 1

    return sequencer, exp_dir_a, exp_dir_b, [sensor_a, sensor_b]


def test_shared_experiment_dir_fetches_data_once(tmp_path: Path) -> None:
    '''Two SensorSpecs sharing one experiment_dir → exactly one loader
    Trainer (no `data=`) and both reconstructions receive that frozen
    `_data` via the `data=` kwarg.'''

    sequencer, _exp_dir, _, _ = _build_sequencer_with_two_sensor_specs(
        tmp_path,
        shared_experiment_dir=True,
    )

    frozen_data = MagicMock(name='frozen_dataframe')

    constructor_calls: list[dict[str, object]] = []

    def fake_trainer_factory(*args: object, **kwargs: object) -> MagicMock:
        constructor_calls.append({'args': args, 'kwargs': dict(kwargs)})
        instance = MagicMock()
        instance._manifest = MagicMock()
        if 'data' not in kwargs or kwargs['data'] is None:
            instance._data = frozen_data
        else:
            instance._data = kwargs['data']
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

    loader_calls = [c for c in constructor_calls if 'data' not in c['kwargs']]
    reconstruct_calls = [c for c in constructor_calls if 'data' in c['kwargs']]

    assert len(loader_calls) == 1
    assert len(reconstruct_calls) == 2
    assert all(c['kwargs']['data'] is frozen_data for c in reconstruct_calls)
    assert len(sequencer.wired_sensors) == 2


def test_distinct_experiment_dirs_each_fetch_once(tmp_path: Path) -> None:
    '''Distinct experiment_dirs build one loader Trainer each (no `data=`)
    plus one `data=`-bearing reconstruction per sensor.'''

    sequencer, _, _, _ = _build_sequencer_with_two_sensor_specs(
        tmp_path,
        shared_experiment_dir=False,
    )

    constructor_calls: list[dict[str, object]] = []

    def fake_trainer_factory(*args: object, **kwargs: object) -> MagicMock:
        constructor_calls.append({'args': args, 'kwargs': dict(kwargs)})
        instance = MagicMock()
        instance._manifest = MagicMock()
        instance._data = kwargs.get('data', MagicMock())
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

    loader_calls = [c for c in constructor_calls if 'data' not in c['kwargs']]
    reconstruct_calls = [c for c in constructor_calls if 'data' in c['kwargs']]

    assert len(loader_calls) == 2
    assert len(reconstruct_calls) == 2
    assert len(sequencer.wired_sensors) == 2


def test_single_sensor_uses_one_loader_then_data_reconstruct(tmp_path: Path) -> None:
    '''One SensorSpec → one loader Trainer (no `data=`) then one
    reconstruction with `data=loader._data`.'''

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
    sequencer._sensor_wire_max_workers = 1

    frozen_data = MagicMock(name='frozen_dataframe')

    constructor_calls: list[dict[str, object]] = []

    def fake_trainer_factory(*args: object, **kwargs: object) -> MagicMock:
        constructor_calls.append({'args': args, 'kwargs': dict(kwargs)})
        instance = MagicMock()
        instance._manifest = MagicMock()
        if 'data' not in kwargs or kwargs['data'] is None:
            instance._data = frozen_data
        else:
            instance._data = kwargs['data']
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

    loader_calls = [c for c in constructor_calls if 'data' not in c['kwargs']]
    reconstruct_calls = [c for c in constructor_calls if 'data' in c['kwargs']]

    assert len(loader_calls) == 1
    assert len(reconstruct_calls) == 1
    assert reconstruct_calls[0]['kwargs']['data'] is frozen_data
    assert len(sequencer.wired_sensors) == 1
