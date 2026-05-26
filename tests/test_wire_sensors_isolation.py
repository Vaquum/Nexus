'''Tests for PT-FIX-18: per-sensor failure isolation in `_wire_sensors`.

Pre-fix: any single sensor's `Trainer(...).train(...)` exception bubbled
out as `StartupError`, killing the entire account. With multiple
sensors per strategy and multiple strategies per manifest, one
`ReconstructionError` (e.g. from PT-FIX-9 territory) brought down the
whole instance even when the other sensors would have wired cleanly.

Post-fix: each sensor wires inside an isolated `try/except`. Failures
are logged with full context and the loop continues. The account
still aborts only when **all** sensors fail — running with zero
signal sources would be silent dead air.
'''

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nexus.startup.error import StartupError
from nexus.startup.sequencer import StartupSequencer


def _build_sequencer_with_two_sensor_specs(
    tmp_path: Path,
) -> StartupSequencer:
    exp_a = tmp_path / 'exp_a'
    exp_a.mkdir()
    (exp_a / 'metadata.json').write_text(
        json.dumps({'sfd_module': 'limen.sfd.foundational_sfd.random_binary'}),
    )

    exp_b = tmp_path / 'exp_b'
    exp_b.mkdir()
    (exp_b / 'metadata.json').write_text(
        json.dumps({'sfd_module': 'limen.sfd.foundational_sfd.random_binary'}),
    )

    sensor_a = MagicMock()
    sensor_a.experiment_dir = exp_a
    sensor_a.permutation_ids = (1,)
    sensor_a.interval_seconds = 60

    sensor_b = MagicMock()
    sensor_b.experiment_dir = exp_b
    sensor_b.permutation_ids = (2,)
    sensor_b.interval_seconds = 60

    strat = MagicMock()
    strat.strategy_id = 'strat-a'
    strat.sensors = [sensor_a, sensor_b]

    sequencer = StartupSequencer.__new__(StartupSequencer)
    sequencer._wired_sensors = []
    sequencer._manifest = MagicMock()
    sequencer._manifest.strategies = [strat]
    sequencer._sensor_wire_max_workers = 1

    return sequencer


def test_one_sensor_failure_does_not_abort_remaining(tmp_path: Path) -> None:

    sequencer = _build_sequencer_with_two_sensor_specs(tmp_path)

    call_count = 0

    def fake_trainer_factory(*_args: object, **_kwargs: object) -> MagicMock:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError('Pass 1 reconstruction mismatch')

        instance = MagicMock()
        instance._manifest = MagicMock()
        instance._data = MagicMock()
        sensor_obj = MagicMock()
        sensor_obj.permutation_id = 2
        sensor_obj.round_params = {}
        instance.train.return_value = [sensor_obj]
        return instance

    with patch(
        'nexus.startup.sequencer.Trainer',
        side_effect=fake_trainer_factory,
    ):
        sequencer._wire_sensors()

    assert len(sequencer.wired_sensors) == 1
    assert sequencer.wired_sensors[0].sensor.permutation_id == 2


def test_all_sensors_failing_raises_startup_error(tmp_path: Path) -> None:
    '''Every Trainer raised → message attributes the failure to "raised", not "returned no Sensors".'''

    sequencer = _build_sequencer_with_two_sensor_specs(tmp_path)

    def always_fails(*_args: object, **_kwargs: object) -> MagicMock:
        raise RuntimeError('Pass 1 reconstruction mismatch')

    with patch(
        'nexus.startup.sequencer.Trainer',
        side_effect=always_fails,
    ), pytest.raises(StartupError, match='no wired sensors') as exc_info:
        sequencer._wire_sensors()

    reason = exc_info.value.reason
    assert 'no signal source' in reason
    assert '2 raised' in reason
    assert '0 returned no Sensors' in reason
    assert sequencer.wired_sensors == []


def test_train_call_returning_no_sensors_trips_all_failed_safeguard(
    tmp_path: Path,
) -> None:
    '''Trainer succeeding but returning no Sensors is still zero signal sources.

    `train()` returning an empty list means no `WiredSensor` ends up in
    `self._wired_sensors`. The safeguard exists to refuse a boot that
    would run with zero signal sources; "the Trainer didn't raise" is
    not a useful distinction when the operational outcome is silent
    dead air. Both fully-raising and empty-return paths must trip
    `StartupError`.
    '''

    sequencer = _build_sequencer_with_two_sensor_specs(tmp_path)

    def fake_trainer_factory(*_args: object, **_kwargs: object) -> MagicMock:
        instance = MagicMock()
        instance._manifest = MagicMock()
        instance._data = MagicMock()
        instance.train.return_value = []
        return instance

    with patch(
        'nexus.startup.sequencer.Trainer',
        side_effect=fake_trainer_factory,
    ), pytest.raises(StartupError, match='no wired sensors') as exc_info:
        sequencer._wire_sensors()

    reason = exc_info.value.reason
    assert 'no signal source' in reason
    assert '0 raised' in reason
    assert '2 returned no Sensors' in reason
    assert sequencer.wired_sensors == []
