'''Pin the Limen Trainer integration contract Nexus depends on.

Nexus production code uses `from limen.experiment.trainer.trainer import
Trainer` in `nexus/startup/sequencer.py:15` and depends on:

* `Trainer.__init__(experiment_dir, data=None)` — called positionally
  for `experiment_dir` (`sequencer.py:626, 632`) and with `data=` by
  keyword (`sequencer.py:634`)
* `Trainer.train(permutation_ids) -> list[Sensor]` — called positionally
  (`sequencer.py:636`)
* `Trainer._data` private attribute — read to warm a cached trainer
  (`sequencer.py:634`)
* `Trainer._manifest` private attribute — passed into every
  `WiredSensor` (`sequencer.py:655`), then consumed by
  `signal_producer.py` (`.with_params_override`, `.prepare_data`) and
  `predict_loop.py` (`.data_source_config`)

Pinning these via `inspect.signature` (parameter names, kinds,
defaults), `typing.get_type_hints` (return type, robust to
`from __future__ import annotations`), and `inspect.getsource`
string-presence checks (private attributes set inside `__init__`,
which `hasattr(Trainer, ...)` cannot see without instantiation)
makes a future Limen version bump that reshapes any of the above
fail loudly in CI instead of at deploy time.
'''

from __future__ import annotations

import inspect
import typing

from limen.experiment.trainer.sensor import Sensor
from limen.experiment.trainer.trainer import Trainer


class TestTrainerInitContract:

    def test_init_parameter_names(self) -> None:
        params = inspect.signature(Trainer.__init__).parameters
        assert list(params.keys()) == ['self', 'experiment_dir', 'data']

    def test_experiment_dir_is_required(self) -> None:
        params = inspect.signature(Trainer.__init__).parameters
        assert params['experiment_dir'].default is inspect.Parameter.empty

    def test_experiment_dir_accepts_positional(self) -> None:
        params = inspect.signature(Trainer.__init__).parameters
        assert params['experiment_dir'].kind != inspect.Parameter.KEYWORD_ONLY

    def test_data_defaults_to_none(self) -> None:
        params = inspect.signature(Trainer.__init__).parameters
        assert params['data'].default is None

    def test_data_accepts_keyword(self) -> None:
        params = inspect.signature(Trainer.__init__).parameters
        assert params['data'].kind != inspect.Parameter.POSITIONAL_ONLY


class TestTrainerTrainContract:

    def test_train_parameter_names(self) -> None:
        params = inspect.signature(Trainer.train).parameters
        assert list(params.keys()) == ['self', 'permutation_ids']

    def test_permutation_ids_accepts_positional(self) -> None:
        params = inspect.signature(Trainer.train).parameters
        assert params['permutation_ids'].kind != inspect.Parameter.KEYWORD_ONLY

    def test_train_returns_list_of_sensor(self) -> None:
        hints = typing.get_type_hints(Trainer.train)
        assert hints['return'] == list[Sensor]


class TestTrainerPrivateAttributeContract:

    def test_init_assigns_data_attribute(self) -> None:
        src = inspect.getsource(Trainer.__init__)
        assert 'self._data' in src

    def test_init_assigns_manifest_attribute(self) -> None:
        src = inspect.getsource(Trainer.__init__)
        assert 'self._manifest' in src
