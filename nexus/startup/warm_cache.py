'''Standalone sensor-cache warmer, run as a pre-launch subprocess.

Reconstructs every Limen `Sensor` declared across one or more manifests
into a single `ProcessPoolExecutor` run, writes the reconstruct-once disk
cache, then exits. The launcher then wires sensors inline against the
warm cache.

Why a separate process: `_wire_sensors` runs inside the launcher, which
owns Polars' global rayon thread pool (market-data cache). Creating a
worker pool in that process degrades the rayon pool and a later large
Polars merge segfaults (observed: rayon worker stack overflow, process
crash-loop). Reconstruction therefore runs here, in a process that never
imports Polars and exits before the launcher starts, keeping the
CPU-bound pool and the trading runtime in separate processes.

A deployment has one or more manifest files — the launcher enumerates
every `*.yaml`/`*.yml` under `MANIFESTS_DIR`, and a manifest may declare
one strategy or many (an account can span multiple manifests). The
warmer takes the same directory (and/or explicit files) and collects the
union of `(experiment_dir, permutation_id)` pairs across all of them,
warming shared bundles once — the cache is keyed by
`(bundle_id, permutation_id)`, independent of how strategies and
accounts map to files — in a single pool.

The pool uses the `spawn` start method (fresh interpreters, no inherited
locks) and pins BLAS/OpenMP to one thread per worker so `N` workers use
`N` cores total rather than `N x cores` (oversubscription thrash).
'''

from __future__ import annotations

import argparse
import multiprocessing
import os
import sys
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import structlog

from limen.experiment.trainer.trainer import Trainer

from nexus.infrastructure.manifest import Manifest, load_manifest
from nexus.startup.sensor_cache import (
    CACHE_DIR_ENV,
    bundle_id_for,
    cache_path_for,
    default_max_workers,
    init_worker,
    reconstruct_sensor,
    write_sensor_atomic,
)

_log = structlog.get_logger()

__all__ = ['main', 'warm_cache']

_BLAS_THREAD_VARS = (
    'OMP_NUM_THREADS',
    'OPENBLAS_NUM_THREADS',
    'MKL_NUM_THREADS',
    'NUMEXPR_NUM_THREADS',
)


def _enumerate_manifests(manifests_dir: Path) -> list[Path]:
    '''Return the sorted `*.yaml`/`*.yml` manifest paths in a directory.

    Mirrors the launcher's `MANIFESTS_DIR` enumeration so the warmer
    covers exactly the manifests the launcher will load.

    Args:
        manifests_dir: Directory holding per-account manifest files.

    Returns:
        Sorted list of manifest file paths.
    '''

    if not manifests_dir.is_dir():
        msg = f'manifests dir not a directory: {manifests_dir}'
        raise NotADirectoryError(msg)

    return sorted([*manifests_dir.glob('*.yaml'), *manifests_dir.glob('*.yml')])


def _collect_unique_tasks(manifests: Iterable[Manifest]) -> list[tuple[Path, int]]:
    '''Return the unique `(experiment_dir, permutation_id)` pairs to reconstruct.

    Deduplicates across every strategy of every manifest: a permutation
    referenced more than once (within or across manifests) is
    reconstructed once, since the cache is keyed by
    `(bundle_id, permutation_id)` and is strategy/manifest-agnostic.

    Args:
        manifests: Loaded strategy manifests.

    Returns:
        Deduplicated list of `(resolved_experiment_dir, permutation_id)`.
    '''

    seen: dict[tuple[Path, int], tuple[Path, int]] = {}
    for manifest in manifests:
        for spec in manifest.strategies:
            for sensor_spec in spec.sensors:
                resolved_dir = sensor_spec.experiment_dir.resolve()

                for permutation_id in sensor_spec.permutation_ids:
                    key = (resolved_dir, permutation_id)
                    seen.setdefault(key, key)

    return list(seen.values())


def warm_cache(manifest_paths: Iterable[Path], cache_dir: Path, max_workers: int) -> None:
    '''Reconstruct every sensor across the given manifests and persist the cache.

    Pins BLAS thread counts to one before spawning workers so the pool
    does not oversubscribe cores, collects the union of tasks across all
    manifests, skips entries already present in the cache, and
    reconstructs the remainder across a `spawn` `ProcessPoolExecutor`,
    writing each reconstructed `Sensor` to its cache path. Per-sensor
    failures (including the expected Limen `ReconstructionError`
    metric-jitter rejections) are logged and skipped; the warmer never
    raises on a single bad permutation.

    Args:
        manifest_paths: Manifest YAML paths to warm.
        cache_dir: Root sensor cache directory (`NEXUS_SENSOR_CACHE_DIR`).
        max_workers: Reconstruction worker-process count.
    '''

    for blas_var in _BLAS_THREAD_VARS:
        os.environ.setdefault(blas_var, '1')

    manifests = [load_manifest(path) for path in manifest_paths]
    tasks = _collect_unique_tasks(manifests)

    bundle_ids: dict[Path, str] = {}
    for resolved_dir in dict.fromkeys(resolved_dir for resolved_dir, _ in tasks):
        try:
            bundle_ids[resolved_dir] = bundle_id_for(resolved_dir)
        except Exception:  # noqa: BLE001 - per-bundle isolation
            _log.exception('bundle id unavailable; skipping dir', experiment_dir=str(resolved_dir))

    misses = [
        (resolved_dir, permutation_id)
        for resolved_dir, permutation_id in tasks
        if resolved_dir in bundle_ids
        and not cache_path_for(cache_dir, bundle_ids[resolved_dir], permutation_id).is_file()
    ]
    if not misses:
        _log.info('sensor cache already warm', total=len(tasks))

        return

    # Load the frozen Trainer data only for dirs with misses, so a fully
    # warm boot loads no Trainer at all. loader._data is a private
    # attribute on Limen Trainer; no public accessor exists as of
    # vaquum_limen 4.0.1.
    loaders: dict[Path, Trainer] = {}
    for resolved_dir in {resolved_dir for resolved_dir, _ in misses}:
        try:
            loaders[resolved_dir] = Trainer(resolved_dir)
        except Exception:  # noqa: BLE001 - per-bundle isolation
            _log.exception('bundle load failed; skipping dir', experiment_dir=str(resolved_dir))

    misses = [
        (resolved_dir, permutation_id)
        for resolved_dir, permutation_id in misses
        if resolved_dir in loaders
    ]
    if not misses:
        _log.info('no loadable sensor misses to warm', total=len(tasks))

        return

    data_by_dir = {
        str(resolved_dir): loaders[resolved_dir]._data
        for resolved_dir in {resolved_dir for resolved_dir, _ in misses}
    }

    workers = min(max_workers, len(misses))
    written = 0
    failed = 0

    with ProcessPoolExecutor(
        max_workers=workers,
        mp_context=multiprocessing.get_context('spawn'),
        initializer=init_worker,
        initargs=(data_by_dir,),
    ) as pool:
        futures = {
            pool.submit(reconstruct_sensor, str(resolved_dir), permutation_id): (resolved_dir, permutation_id)
            for resolved_dir, permutation_id in misses
        }

        for future, (resolved_dir, permutation_id) in futures.items():
            try:
                sensor = future.result()
            except Exception:  # noqa: BLE001 - per-sensor isolation
                failed += 1
                _log.exception(
                    'sensor reconstruction failed',
                    experiment_dir=str(resolved_dir),
                    permutation_id=permutation_id,
                )
                continue

            if sensor is None:
                failed += 1
                continue

            write_sensor_atomic(
                cache_path_for(cache_dir, bundle_ids[resolved_dir], permutation_id),
                sensor,
            )
            written += 1

    _log.info(
        'sensor cache warmed',
        manifests=len(manifests),
        written=written,
        failed=failed,
        total=len(tasks),
    )


def main(argv: list[str] | None = None) -> int:
    '''CLI entry point: warm the sensor cache for one or more manifests.

    Resolves manifests from `--manifests-dir` (every `*.yaml`/`*.yml`,
    mirroring the launcher's `MANIFESTS_DIR`) and/or repeated `--manifest`
    paths. Reads the cache directory from `NEXUS_SENSOR_CACHE_DIR` and the
    worker count from `--max-workers` (falling back to
    `NEXUS_WIRE_MAX_WORKERS` via `default_max_workers`). Returns a
    non-zero exit code when no manifests are given or the cache directory
    is unset; per-sensor failures do not fail the run.

    Args:
        argv: Optional argument vector for testing; defaults to `sys.argv`.

    Returns:
        Process exit code.
    '''

    parser = argparse.ArgumentParser(
        description='Warm the Nexus sensor disk cache before launch.',
    )
    parser.add_argument(
        '--manifests-dir',
        type=Path,
        default=None,
        help='Directory of manifest YAMLs to warm (mirrors MANIFESTS_DIR).',
    )
    parser.add_argument(
        '--manifest',
        type=Path,
        action='append',
        default=None,
        dest='manifests',
        help='Explicit manifest YAML path; may be repeated.',
    )
    parser.add_argument(
        '--max-workers',
        type=int,
        default=None,
        help='Reconstruction worker processes (default: NEXUS_WIRE_MAX_WORKERS or 1).',
    )
    args = parser.parse_args(argv)

    manifest_paths: list[Path] = []
    if args.manifests_dir is not None:
        manifest_paths.extend(_enumerate_manifests(args.manifests_dir))

    if args.manifests:
        manifest_paths.extend(args.manifests)

    if not manifest_paths:
        _log.error('no manifests given; pass --manifests-dir and/or --manifest')

        return 2

    cache_dir = os.environ.get(CACHE_DIR_ENV)
    if not cache_dir:
        _log.error('NEXUS_SENSOR_CACHE_DIR unset; refusing to warm with no cache target')

        return 1

    max_workers = args.max_workers if args.max_workers is not None else default_max_workers()
    warm_cache(manifest_paths, Path(cache_dir), max_workers)

    return 0


if __name__ == '__main__':
    sys.exit(main())
