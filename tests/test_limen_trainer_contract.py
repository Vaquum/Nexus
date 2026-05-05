'''Pin the Limen Trainer integration contract Nexus depends on.

Nexus production code uses `from limen.experiment.trainer.trainer import
Trainer` in `nexus/startup/sequencer.py:15` and depends on the
`__init__(experiment_dir, data=None)` and `train(permutation_ids) ->
list[Sensor]` signatures. Pinning the contract here makes a future Limen
version bump that reshapes either signature fail loudly in CI instead of
at deploy time.
'''

from __future__ import annotations

import inspect

from limen.experiment.trainer.sensor import Sensor
from limen.experiment.trainer.trainer import Trainer


class TestTrainerInitContract:

    def test_init_parameter_names(self) -> None:
        params = inspect.signature(Trainer.__init__).parameters
        assert list(params.keys()) == ['self', 'experiment_dir', 'data']

    def test_experiment_dir_is_required(self) -> None:
        params = inspect.signature(Trainer.__init__).parameters
        assert params['experiment_dir'].default is inspect.Parameter.empty

    def test_data_defaults_to_none(self) -> None:
        params = inspect.signature(Trainer.__init__).parameters
        assert params['data'].default is None


class TestTrainerTrainContract:

    def test_train_parameter_names(self) -> None:
        params = inspect.signature(Trainer.train).parameters
        assert list(params.keys()) == ['self', 'permutation_ids']

    def test_train_return_annotation_is_list_of_sensor(self) -> None:
        annotation = inspect.signature(Trainer.train).return_annotation
        assert annotation == list[Sensor]
