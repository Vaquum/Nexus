'''Tests for SensorSpec dataclass.'''

from __future__ import annotations

from pathlib import Path

import pytest

from nexus.infrastructure.manifest import SensorSpec


class TestSensorSpec:

    def test_valid_spec(self, tmp_path: Path) -> None:
        '''Valid SensorSpec creates successfully.'''

        exp_dir = tmp_path / 'experiment'
        exp_dir.mkdir()

        spec = SensorSpec(
            experiment_dir=exp_dir,
            permutation_ids=(42, 107),
            interval_seconds=60,
        )

        assert spec.experiment_dir == exp_dir
        assert spec.permutation_ids == (42, 107)
        assert spec.interval_seconds == 60

    def test_frozen(self, tmp_path: Path) -> None:
        '''SensorSpec is immutable.'''

        exp_dir = tmp_path / 'experiment'
        exp_dir.mkdir()

        spec = SensorSpec(
            experiment_dir=exp_dir,
            permutation_ids=(1,),
            interval_seconds=60,
        )

        with pytest.raises(AttributeError):
            spec.interval_seconds = 30  # type: ignore[misc]

    def test_missing_experiment_dir_raises(self, tmp_path: Path) -> None:
        '''Non-existent experiment_dir raises ValueError.'''

        with pytest.raises(ValueError, match='experiment_dir not found'):
            SensorSpec(
                experiment_dir=tmp_path / 'nonexistent',
                permutation_ids=(1,),
                interval_seconds=60,
            )

    def test_experiment_dir_not_path_raises(self) -> None:
        '''Non-Path experiment_dir raises ValueError.'''

        with pytest.raises(ValueError, match='experiment_dir must be a Path'):
            SensorSpec(
                experiment_dir='/some/string/path',  # type: ignore[arg-type]
                permutation_ids=(1,),
                interval_seconds=60,
            )

    def test_empty_permutation_ids_raises(self, tmp_path: Path) -> None:
        '''Empty permutation_ids raises ValueError.'''

        exp_dir = tmp_path / 'experiment'
        exp_dir.mkdir()

        with pytest.raises(ValueError, match='permutation_ids must be a non-empty tuple'):
            SensorSpec(
                experiment_dir=exp_dir,
                permutation_ids=(),
                interval_seconds=60,
            )

    def test_permutation_ids_not_tuple_raises(self, tmp_path: Path) -> None:
        '''Non-tuple permutation_ids raises ValueError.'''

        exp_dir = tmp_path / 'experiment'
        exp_dir.mkdir()

        with pytest.raises(ValueError, match='permutation_ids must be a non-empty tuple'):
            SensorSpec(
                experiment_dir=exp_dir,
                permutation_ids=[1, 2],  # type: ignore[arg-type]
                interval_seconds=60,
            )

    def test_permutation_ids_with_non_int_raises(self, tmp_path: Path) -> None:
        '''permutation_ids containing non-int raises ValueError.'''

        exp_dir = tmp_path / 'experiment'
        exp_dir.mkdir()

        with pytest.raises(ValueError, match='permutation_ids must contain ints'):
            SensorSpec(
                experiment_dir=exp_dir,
                permutation_ids=(1, 'two'),  # type: ignore[arg-type]
                interval_seconds=60,
            )

    def test_permutation_ids_with_bool_raises(self, tmp_path: Path) -> None:
        '''permutation_ids containing bool raises ValueError.'''

        exp_dir = tmp_path / 'experiment'
        exp_dir.mkdir()

        with pytest.raises(ValueError, match='permutation_ids must contain ints'):
            SensorSpec(
                experiment_dir=exp_dir,
                permutation_ids=(True,),  # type: ignore[arg-type]
                interval_seconds=60,
            )

    def test_zero_interval_raises(self, tmp_path: Path) -> None:
        '''Zero interval_seconds raises ValueError.'''

        exp_dir = tmp_path / 'experiment'
        exp_dir.mkdir()

        with pytest.raises(ValueError, match='interval_seconds must be positive'):
            SensorSpec(
                experiment_dir=exp_dir,
                permutation_ids=(1,),
                interval_seconds=0,
            )

    def test_negative_interval_raises(self, tmp_path: Path) -> None:
        '''Negative interval_seconds raises ValueError.'''

        exp_dir = tmp_path / 'experiment'
        exp_dir.mkdir()

        with pytest.raises(ValueError, match='interval_seconds must be positive'):
            SensorSpec(
                experiment_dir=exp_dir,
                permutation_ids=(1,),
                interval_seconds=-10,
            )

    def test_bool_interval_raises(self, tmp_path: Path) -> None:
        '''Bool interval_seconds raises ValueError.'''

        exp_dir = tmp_path / 'experiment'
        exp_dir.mkdir()

        with pytest.raises(ValueError, match='interval_seconds must be an int'):
            SensorSpec(
                experiment_dir=exp_dir,
                permutation_ids=(1,),
                interval_seconds=True,  # type: ignore[arg-type]
            )

    def test_single_permutation_id(self, tmp_path: Path) -> None:
        '''Single permutation_id is allowed.'''

        exp_dir = tmp_path / 'experiment'
        exp_dir.mkdir()

        spec = SensorSpec(
            experiment_dir=exp_dir,
            permutation_ids=(42,),
            interval_seconds=300,
        )

        assert spec.permutation_ids == (42,)

    def test_multiple_permutation_ids(self, tmp_path: Path) -> None:
        '''Multiple permutation_ids are allowed.'''

        exp_dir = tmp_path / 'experiment'
        exp_dir.mkdir()

        spec = SensorSpec(
            experiment_dir=exp_dir,
            permutation_ids=(1, 2, 3),
            interval_seconds=60,
        )

        assert spec.permutation_ids == (1, 2, 3)
