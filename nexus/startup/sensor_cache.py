'''Disk cache and process-pool helpers for sensor reconstruction.

`_wire_sensors` reconstructs every Limen `Sensor` declared in the
manifest. Each reconstruction is two CPU-bound sklearn fits, so a
large manifest costs hours and repeats in full on every restart. This
module supplies two facilities used by the sequencer:

- A reconstruct-once disk cache keyed by the bundle's `metadata.json`
  (and `manifest.yml` when present), so an unchanged bundle is loaded
  from a pickled `Sensor` instead of refit.
- Process-pool machinery (`ProcessPoolExecutor` initializer plus a
  worker entry point) that reconstructs cache misses across cores,
  loading each experiment bundle's frozen `_data` once per worker.

The data load (`Trainer._data`, ~160MB) is passed to workers via the
pool initializer rather than per-task arguments; only the small,
picklable `Sensor` crosses the process boundary on the way back.
'''

from __future__ import annotations

import hashlib
import os
import pickle
from pathlib import Path
from typing import Any

import structlog

from limen.experiment.trainer.trainer import Trainer

_log = structlog.get_logger()

__all__ = [
    'CACHE_DIR_ENV',
    'MAX_WORKERS_ENV',
    'bundle_id_for',
    'cache_path_for',
    'default_max_workers',
    'reconstruct_sensor',
    'write_sensor_atomic',
]

CACHE_DIR_ENV = 'NEXUS_SENSOR_CACHE_DIR'
MAX_WORKERS_ENV = 'NEXUS_WIRE_MAX_WORKERS'

_BUNDLE_ID_LENGTH = 16
_WORKER_CAP = 16
_HASH_FILES = ('metadata.json', 'manifest.yml')

_worker_data: dict[str, Any] = {}


def default_max_workers() -> int:
    '''Return the configured reconstruction worker count.

    Reads `NEXUS_WIRE_MAX_WORKERS` when set and parseable as a positive
    integer; otherwise defaults to `min(os.cpu_count() or 1, 16)`. A
    value of `1` selects the inline (single-process) reconstruction
    path in the sequencer.

    Returns:
        The number of worker processes to use for reconstruction.
    '''

    raw = os.environ.get(MAX_WORKERS_ENV)
    if raw is not None:
        try:
            parsed = int(raw)
        except ValueError:
            _log.warning('invalid NEXUS_WIRE_MAX_WORKERS, using default', value=raw)
        else:
            if parsed >= 1:
                return parsed
            _log.warning('non-positive NEXUS_WIRE_MAX_WORKERS, using default', value=raw)

    return min(os.cpu_count() or 1, _WORKER_CAP)


def bundle_id_for(experiment_dir: Path) -> str:
    '''Return the cache bundle id for an experiment directory.

    The id is the first 16 hex chars of a SHA-256 over the bytes of the
    bundle's `metadata.json` and `manifest.yml`, hashing only the files
    that exist. Because the digest covers the bundle's `limen_version`
    and data-window metadata, the id changes — and the cache invalidates
    — whenever those inputs change.

    Args:
        experiment_dir: Resolved path to the Limen experiment directory.

    Returns:
        A 16-character hex bundle id.
    '''

    digest = hashlib.sha256()
    for name in _HASH_FILES:
        candidate = experiment_dir / name
        if candidate.is_file():
            digest.update(candidate.read_bytes())

    return digest.hexdigest()[:_BUNDLE_ID_LENGTH]


def cache_path_for(cache_dir: Path, bundle_id: str, permutation_id: int) -> Path:
    '''Return the pickle path for one cached sensor.

    Args:
        cache_dir: Root cache directory (`NEXUS_SENSOR_CACHE_DIR`).
        bundle_id: Bundle id from `bundle_id_for`.
        permutation_id: Permutation (round) id of the sensor.

    Returns:
        Path to `<cache_dir>/<bundle_id>/<permutation_id>.pkl`.
    '''

    return cache_dir / bundle_id / f'{permutation_id}.pkl'


def write_sensor_atomic(path: Path, sensor: Any) -> None:
    '''Pickle a sensor to `path` atomically.

    Writes to a temporary file in the same directory and `os.replace`s
    it into place so a concurrent reader never observes a partial file.

    Args:
        path: Destination pickle path.
        sensor: The picklable Limen `Sensor` to persist.
    '''

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    with tmp_path.open('wb') as handle:
        pickle.dump(sensor, handle)

    tmp_path.replace(path)


def _init_worker(data_by_dir: dict[str, Any]) -> None:
    '''Populate the worker-global bundle-data map (pool initializer).

    Runs once per worker process. Stores each experiment directory's
    frozen `Trainer._data` so per-task reconstruction reuses the bundle
    load instead of refetching it.

    Args:
        data_by_dir: Mapping of resolved experiment-dir string to its
            frozen `_data` slice.
    '''

    _worker_data.clear()
    _worker_data.update(data_by_dir)


def reconstruct_sensor(experiment_dir_str: str, permutation_id: int) -> Any:
    '''Reconstruct one Limen `Sensor` inside a worker process.

    Builds `Trainer(experiment_dir, data=<worker-global _data>)` and
    returns `train([permutation_id])[0]`. The bundle data comes from the
    worker-global map populated by `_init_worker`, so the heavy slice
    never crosses the process boundary as a task argument.

    Args:
        experiment_dir_str: Resolved experiment directory as a string.
        permutation_id: Permutation (round) id to reconstruct.

    Returns:
        The reconstructed, picklable Limen `Sensor`.
    '''

    data = _worker_data[experiment_dir_str]
    trainer = Trainer(Path(experiment_dir_str), data=data)

    return trainer.train([permutation_id])[0]
