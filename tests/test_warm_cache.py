'''Tests for the standalone pre-launch sensor-cache warmer.'''

from __future__ import annotations

import os
import types
from concurrent.futures import Future
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from nexus.startup import warm_cache as wc


class _StubSensor:
    '''Minimal picklable stand-in for a Limen Sensor.'''

    def __init__(self, permutation_id: int) -> None:
        self.permutation_id = permutation_id
        self.round_params: dict[str, object] = {}

    def predict(self, _data: dict) -> dict:
        return {}


def _sensor_spec(experiment_dir: Path, permutation_ids: tuple[int, ...]) -> Any:
    return types.SimpleNamespace(experiment_dir=experiment_dir, permutation_ids=permutation_ids)


def _manifest_with(*sensor_specs: Any) -> Any:
    strategy = types.SimpleNamespace(sensors=list(sensor_specs))

    return types.SimpleNamespace(strategies=[strategy])


def test_collect_unique_tasks_dedups_within_and_across_manifests(tmp_path: Path) -> None:
    '''A (dir, perm) shared across strategies/manifests collapses to one task.'''

    bundle = tmp_path / 'bundle'
    bundle.mkdir()

    manifest_one = _manifest_with(_sensor_spec(bundle, (1, 2)))
    manifest_two = _manifest_with(_sensor_spec(bundle, (2, 3)))

    tasks = wc._collect_unique_tasks([manifest_one, manifest_two])

    assert sorted(permutation_id for _, permutation_id in tasks) == [1, 2, 3]
    assert all(resolved_dir == bundle.resolve() for resolved_dir, _ in tasks)


def test_warm_cache_uses_spawn_pins_blas_and_writes_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    '''warm_cache builds a spawn pool, pins BLAS to one thread, and writes one pkl per sensor.'''

    for blas_var in wc._BLAS_THREAD_VARS:
        monkeypatch.delenv(blas_var, raising=False)

    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    cache_dir = tmp_path / 'cache'

    manifest = _manifest_with(_sensor_spec(bundle, (1, 2)))
    monkeypatch.setattr(wc, 'load_manifest', lambda _path: manifest)

    loader = MagicMock()
    loader._data = MagicMock()
    monkeypatch.setattr(wc, 'Trainer', MagicMock(return_value=loader))
    monkeypatch.setattr(wc, 'bundle_id_for', lambda _resolved_dir: 'bid')

    captured: dict[str, object] = {}

    class _FakePool:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def __enter__(self) -> _FakePool:
            return self

        def __exit__(self, *_exc: object) -> bool:
            return False

        def submit(self, _fn: object, _resolved_dir: str, permutation_id: int) -> Future:
            future: Future = Future()
            future.set_result(_StubSensor(permutation_id))

            return future

    monkeypatch.setattr(wc, 'ProcessPoolExecutor', _FakePool)

    wc.warm_cache([Path('manifest.yaml')], cache_dir, max_workers=4)

    assert captured['mp_context'].get_start_method() == 'spawn'  # type: ignore[attr-defined]
    assert os.environ['OMP_NUM_THREADS'] == '1'
    assert (cache_dir / 'bid' / '1.pkl').is_file()
    assert (cache_dir / 'bid' / '2.pkl').is_file()
