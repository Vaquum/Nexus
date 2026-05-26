'''Tests for the reconstruct-once disk cache in `_wire_sensors`.

The disk cache is opt-in via the `NEXUS_SENSOR_CACHE_DIR` environment
variable. When set, each reconstructed Limen `Sensor` is pickled to
`<cache_dir>/<bundle_id>/<permutation_id>.pkl`, keyed by a SHA-256 of
the bundle's `metadata.json` (and `manifest.yml` when present). On a
subsequent boot a cache HIT loads the pickled `Sensor` and skips the
expensive `Trainer.train()` reconstruction entirely.

These tests force the inline reconstruction path (worker count of 1)
so the patched `nexus.startup.sequencer.Trainer` mock applies in the
current process; a `ProcessPoolExecutor` would not see the patch.
'''

from __future__ import annotations

import json
import pickle
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nexus.startup.sensor_cache import bundle_id_for
from nexus.startup.sequencer import StartupSequencer


def _make_bundle(tmp_path: Path, name: str, sfd_module: str = 'limen.sfd.foundational_sfd.random_binary') -> Path:
    bundle = tmp_path / name
    bundle.mkdir()
    (bundle / 'metadata.json').write_text(
        json.dumps({'sfd_module': sfd_module}),
    )

    return bundle


def _build_single_sensor_sequencer(
    bundle: Path,
    permutation_id: int = 1,
) -> StartupSequencer:
    sensor_spec = MagicMock()
    sensor_spec.experiment_dir = bundle
    sensor_spec.permutation_ids = (permutation_id,)
    sensor_spec.interval_seconds = 60

    strat = MagicMock()
    strat.strategy_id = 'strat'
    strat.sensors = [sensor_spec]

    sequencer = StartupSequencer.__new__(StartupSequencer)
    sequencer._wired_sensors = []
    sequencer._manifest = MagicMock()
    sequencer._manifest.strategies = [strat]
    sequencer._sensor_wire_max_workers = 1

    return sequencer


class _StubSensor:
    '''Minimal picklable stand-in for a Limen Sensor.'''

    def __init__(self, permutation_id: int) -> None:
        self.permutation_id = permutation_id
        self.round_params: dict[str, object] = {}

    def predict(self, _data: dict) -> dict:
        return {}


class _NoPredictSensor:
    '''Sensor-shaped object lacking the callable `predict` the predict loop needs.'''

    def __init__(self, permutation_id: int) -> None:
        self.permutation_id = permutation_id
        self.round_params: dict[str, object] = {}


class _BadRoundParamsSensor:
    '''Sensor-shaped object whose `round_params` is not a dict.'''

    def __init__(self, permutation_id: int) -> None:
        self.permutation_id = permutation_id
        self.round_params = 'not-a-dict'

    def predict(self, _data: dict) -> dict:
        return {}


def _make_trainer_factory(
    calls: list[dict[str, object]],
    permutation_id: int = 1,
) -> object:
    def fake_trainer_factory(*args: object, **kwargs: object) -> MagicMock:
        calls.append({'args': args, 'kwargs': dict(kwargs)})
        instance = MagicMock()
        instance._manifest = MagicMock()
        instance._data = MagicMock()
        instance.train.return_value = [_StubSensor(permutation_id)]
        return instance

    return fake_trainer_factory


def test_cache_hit_skips_reconstruction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    '''A pre-seeded pkl is loaded and `train()` is never called for that perm.'''

    cache_dir = tmp_path / 'cache'
    bundle = _make_bundle(tmp_path, 'bundle')
    monkeypatch.setenv('NEXUS_SENSOR_CACHE_DIR', str(cache_dir))

    bundle_id = bundle_id_for(bundle)
    perm_dir = cache_dir / bundle_id
    perm_dir.mkdir(parents=True)
    (perm_dir / '1.pkl').write_bytes(pickle.dumps(_StubSensor(1)))

    sequencer = _build_single_sensor_sequencer(bundle)

    factory_instance = MagicMock()
    factory_instance._manifest = MagicMock()
    factory_instance._data = MagicMock()

    with patch(
        'nexus.startup.sequencer.Trainer',
        return_value=factory_instance,
    ):
        sequencer._wire_sensors()

    factory_instance.train.assert_not_called()
    assert len(sequencer.wired_sensors) == 1
    assert sequencer.wired_sensors[0].sensor.permutation_id == 1


def test_cache_miss_reconstructs_and_writes_pkl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    '''A miss reconstructs the Sensor and persists it to the cache path.'''

    cache_dir = tmp_path / 'cache'
    bundle = _make_bundle(tmp_path, 'bundle')
    monkeypatch.setenv('NEXUS_SENSOR_CACHE_DIR', str(cache_dir))

    sequencer = _build_single_sensor_sequencer(bundle)

    calls: list[dict[str, object]] = []
    with patch(
        'nexus.startup.sequencer.Trainer',
        side_effect=_make_trainer_factory(calls),
    ):
        sequencer._wire_sensors()

    bundle_id = bundle_id_for(bundle)
    pkl_path = cache_dir / bundle_id / '1.pkl'

    assert pkl_path.is_file()
    assert len(sequencer.wired_sensors) == 1


def test_corrupt_cache_file_falls_back_to_reconstruction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    '''An unpicklable cache file is treated as a miss; boot does not crash.'''

    cache_dir = tmp_path / 'cache'
    bundle = _make_bundle(tmp_path, 'bundle')
    monkeypatch.setenv('NEXUS_SENSOR_CACHE_DIR', str(cache_dir))

    bundle_id = bundle_id_for(bundle)
    perm_dir = cache_dir / bundle_id
    perm_dir.mkdir(parents=True)
    (perm_dir / '1.pkl').write_bytes(b'not a valid pickle stream')

    sequencer = _build_single_sensor_sequencer(bundle)

    calls: list[dict[str, object]] = []
    with patch(
        'nexus.startup.sequencer.Trainer',
        side_effect=_make_trainer_factory(calls),
    ):
        sequencer._wire_sensors()

    assert len(calls) >= 1
    assert len(sequencer.wired_sensors) == 1


def test_distinct_bundles_get_distinct_cache_dirs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    '''A different metadata.json yields a different bundle_id (cache miss).'''

    cache_dir = tmp_path / 'cache'
    bundle_one = _make_bundle(tmp_path, 'bundle_one', sfd_module='limen.sfd.a')
    bundle_two = _make_bundle(tmp_path, 'bundle_two', sfd_module='limen.sfd.b')
    monkeypatch.setenv('NEXUS_SENSOR_CACHE_DIR', str(cache_dir))

    assert bundle_id_for(bundle_one) != bundle_id_for(bundle_two)


def test_cache_off_never_touches_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    '''With NEXUS_SENSOR_CACHE_DIR unset, no cache directory is created.'''

    cache_dir = tmp_path / 'cache'
    bundle = _make_bundle(tmp_path, 'bundle')
    monkeypatch.delenv('NEXUS_SENSOR_CACHE_DIR', raising=False)

    sequencer = _build_single_sensor_sequencer(bundle)

    calls: list[dict[str, object]] = []
    with patch(
        'nexus.startup.sequencer.Trainer',
        side_effect=_make_trainer_factory(calls),
    ):
        sequencer._wire_sensors()

    assert not cache_dir.exists()
    assert len(calls) >= 1
    assert len(sequencer.wired_sensors) == 1


def test_manifest_yml_participates_in_bundle_id(tmp_path: Path) -> None:
    '''Adding/altering manifest.yml changes the bundle_id (auto-invalidation).'''

    bundle = _make_bundle(tmp_path, 'bundle')
    without_manifest = bundle_id_for(bundle)

    (bundle / 'manifest.yml').write_text('version: "1.0"\n')
    with_manifest = bundle_id_for(bundle)

    assert without_manifest != with_manifest


def test_reconstruct_sensor_returns_none_on_empty_train() -> None:
    '''The pooled worker returns None (not IndexError) when train() yields no Sensor.'''

    from nexus.startup import sensor_cache

    sensor_cache._init_worker({'/bundle': MagicMock()})
    instance = MagicMock()
    instance.train.return_value = []

    with patch('nexus.startup.sensor_cache.Trainer', return_value=instance):
        result = sensor_cache.reconstruct_sensor('/bundle', 1)

    assert result is None


def test_non_sensor_cache_entry_treated_as_miss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    '''A cache file that unpickles to a non-Sensor object is reconstructed, not aborted on.'''

    cache_dir = tmp_path / 'cache'
    bundle = _make_bundle(tmp_path, 'bundle')
    monkeypatch.setenv('NEXUS_SENSOR_CACHE_DIR', str(cache_dir))

    perm_dir = cache_dir / bundle_id_for(bundle)
    perm_dir.mkdir(parents=True)
    (perm_dir / '1.pkl').write_bytes(pickle.dumps({'not': 'a sensor'}))

    sequencer = _build_single_sensor_sequencer(bundle)

    calls: list[dict[str, object]] = []
    with patch(
        'nexus.startup.sequencer.Trainer',
        side_effect=_make_trainer_factory(calls),
    ):
        sequencer._wire_sensors()

    assert len(calls) >= 1
    assert len(sequencer.wired_sensors) == 1
    assert sequencer.wired_sensors[0].sensor.permutation_id == 1


def test_cache_permutation_mismatch_treated_as_miss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    '''A cached Sensor whose permutation_id != the task's is rejected and reconstructed.'''

    cache_dir = tmp_path / 'cache'
    bundle = _make_bundle(tmp_path, 'bundle')
    monkeypatch.setenv('NEXUS_SENSOR_CACHE_DIR', str(cache_dir))

    perm_dir = cache_dir / bundle_id_for(bundle)
    perm_dir.mkdir(parents=True)
    (perm_dir / '1.pkl').write_bytes(pickle.dumps(_StubSensor(99)))

    sequencer = _build_single_sensor_sequencer(bundle, permutation_id=1)

    calls: list[dict[str, object]] = []
    with patch(
        'nexus.startup.sequencer.Trainer',
        side_effect=_make_trainer_factory(calls, permutation_id=1),
    ):
        sequencer._wire_sensors()

    assert len(calls) >= 1
    assert len(sequencer.wired_sensors) == 1
    assert sequencer.wired_sensors[0].sensor.permutation_id == 1


def test_bundle_id_error_disables_cache_not_startup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    '''An unreadable bundle file (bundle_id_for raises) disables the cache for that dir without aborting wiring.'''

    cache_dir = tmp_path / 'cache'
    bundle = _make_bundle(tmp_path, 'bundle')
    monkeypatch.setenv('NEXUS_SENSOR_CACHE_DIR', str(cache_dir))

    sequencer = _build_single_sensor_sequencer(bundle)

    calls: list[dict[str, object]] = []
    with patch(
        'nexus.startup.sequencer.bundle_id_for',
        side_effect=OSError('unreadable bundle file'),
    ), patch(
        'nexus.startup.sequencer.Trainer',
        side_effect=_make_trainer_factory(calls),
    ):
        sequencer._wire_sensors()

    assert len(calls) >= 1
    assert len(sequencer.wired_sensors) == 1
    assert not cache_dir.exists()


def test_cache_entry_without_predict_treated_as_miss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    '''A cached object with the right permutation_id but no callable predict is reconstructed.'''

    cache_dir = tmp_path / 'cache'
    bundle = _make_bundle(tmp_path, 'bundle')
    monkeypatch.setenv('NEXUS_SENSOR_CACHE_DIR', str(cache_dir))

    perm_dir = cache_dir / bundle_id_for(bundle)
    perm_dir.mkdir(parents=True)
    (perm_dir / '1.pkl').write_bytes(pickle.dumps(_NoPredictSensor(1)))

    sequencer = _build_single_sensor_sequencer(bundle, permutation_id=1)

    calls: list[dict[str, object]] = []
    with patch(
        'nexus.startup.sequencer.Trainer',
        side_effect=_make_trainer_factory(calls, permutation_id=1),
    ):
        sequencer._wire_sensors()

    assert len(calls) >= 1
    assert len(sequencer.wired_sensors) == 1
    assert sequencer.wired_sensors[0].sensor.permutation_id == 1


def test_cache_entry_with_non_dict_round_params_treated_as_miss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    '''A cached object whose round_params is not a dict is rejected and reconstructed.'''

    cache_dir = tmp_path / 'cache'
    bundle = _make_bundle(tmp_path, 'bundle')
    monkeypatch.setenv('NEXUS_SENSOR_CACHE_DIR', str(cache_dir))

    perm_dir = cache_dir / bundle_id_for(bundle)
    perm_dir.mkdir(parents=True)
    (perm_dir / '1.pkl').write_bytes(pickle.dumps(_BadRoundParamsSensor(1)))

    sequencer = _build_single_sensor_sequencer(bundle, permutation_id=1)

    calls: list[dict[str, object]] = []
    with patch(
        'nexus.startup.sequencer.Trainer',
        side_effect=_make_trainer_factory(calls, permutation_id=1),
    ):
        sequencer._wire_sensors()

    assert len(calls) >= 1
    assert len(sequencer.wired_sensors) == 1
    assert sequencer.wired_sensors[0].sensor.permutation_id == 1
