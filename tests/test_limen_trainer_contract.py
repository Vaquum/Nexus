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
defaults — kind checks use explicit allow-lists so a switch to
`*args` / `**kwargs` is rejected, not silently accepted),
`typing.get_type_hints` + `typing.get_origin` / `get_args` (return
type, robust to `from __future__ import annotations` and to
equivalent annotation spellings such as `typing.List[Sensor]`), and
AST-walked `ast.Assign` / `ast.AnnAssign` checks of the dedented
`__init__` source (private attributes set inside `__init__`, which
`hasattr(Trainer, ...)` cannot see without instantiation) makes a
future Limen version bump that reshapes any of the above fail loudly
in CI instead of at deploy time.
'''

from __future__ import annotations

import ast
import inspect
import textwrap
import typing

from limen.experiment.trainer.sensor import Sensor
from limen.experiment.trainer.trainer import Trainer


def _init_assigns_self_attribute(attr_name: str) -> bool:
    src = textwrap.dedent(inspect.getsource(Trainer.__init__))
    init_node = ast.parse(src).body[0]
    for node in ast.walk(init_node):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == 'self'
                    and target.attr == attr_name
                ):
                    return True
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            if (
                node.value is not None
                and isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == 'self'
                and target.attr == attr_name
            ):
                return True
    return False


class TestTrainerInitContract:

    def test_init_parameter_names(self) -> None:
        params = inspect.signature(Trainer.__init__).parameters
        assert list(params.keys()) == ['self', 'experiment_dir', 'data']

    def test_experiment_dir_is_required(self) -> None:
        params = inspect.signature(Trainer.__init__).parameters
        assert params['experiment_dir'].default is inspect.Parameter.empty

    def test_experiment_dir_accepts_positional(self) -> None:
        params = inspect.signature(Trainer.__init__).parameters
        assert params['experiment_dir'].kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )

    def test_data_defaults_to_none(self) -> None:
        params = inspect.signature(Trainer.__init__).parameters
        assert params['data'].default is None

    def test_data_accepts_keyword(self) -> None:
        params = inspect.signature(Trainer.__init__).parameters
        assert params['data'].kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
            inspect.Parameter.VAR_KEYWORD,
        )


class TestTrainerTrainContract:

    def test_train_parameter_names(self) -> None:
        params = inspect.signature(Trainer.train).parameters
        assert list(params.keys()) == ['self', 'permutation_ids']

    def test_permutation_ids_accepts_positional(self) -> None:
        params = inspect.signature(Trainer.train).parameters
        assert params['permutation_ids'].kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )

    def test_train_returns_list_of_sensor(self) -> None:
        hints = typing.get_type_hints(Trainer.train)
        return_hint = hints['return']
        assert typing.get_origin(return_hint) is list
        assert typing.get_args(return_hint) == (Sensor,)


class TestTrainerPrivateAttributeContract:

    def test_init_assigns_data_attribute(self) -> None:
        assert _init_assigns_self_attribute('_data')

    def test_init_assigns_manifest_attribute(self) -> None:
        assert _init_assigns_self_attribute('_manifest')
