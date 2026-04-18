'''Tests for StartupSequencer._wire_sensors with real Limen Trainer.'''

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nexus.infrastructure.state_store import StateStore
from nexus.startup import StartupError, StartupSequencer

limen = pytest.importorskip('limen')
np = pytest.importorskip('numpy')


def _find_limen_root() -> Path | None:
    root = Path(limen.__file__).parent.parent
    if (root / 'datasets').is_dir():
        return root
    return None


_LIMEN_ROOT = _find_limen_root()
_HAS_LIMEN_DATA = _LIMEN_ROOT is not None

pytestmark = pytest.mark.skipif(
    not _HAS_LIMEN_DATA,
    reason='Limen datasets not found adjacent to installed limen package',
)


@pytest.fixture(autouse=True)
def _limen_cwd() -> None:
    '''Run tests from Limen root so Trainer can find datasets.'''

    original = Path.cwd()
    os.chdir(_LIMEN_ROOT)
    os.environ['LOOP_ENV'] = 'test'
    try:
        yield
    finally:
        os.chdir(original)
        os.environ.pop('LOOP_ENV', None)


VALID_STRATEGY = '''
from nexus.strategy import Action, Strategy, StrategyContext, StrategyParams
from nexus.strategy.signal import Signal
from nexus.infrastructure.praxis_connector.trade_outcome import TradeOutcome

class Strategy(Strategy):
    def on_save(self) -> bytes:
        return b""

    def on_load(self, data: bytes) -> None:
        pass

    def on_startup(self, params: StrategyParams, context: StrategyContext) -> list[Action]:
        return []

    def on_signal(self, signal: Signal, params: StrategyParams, context: StrategyContext) -> list[Action]:
        return []

    def on_outcome(self, outcome: TradeOutcome, params: StrategyParams, context: StrategyContext) -> list[Action]:
        return []

    def on_timer(self, timer_id: str, params: StrategyParams, context: StrategyContext) -> list[Action]:
        return []

    def on_shutdown(self, params: StrategyParams, context: StrategyContext) -> list[Action]:
        return []
'''


def _make_experiment_dir(tmp_path: Path, name: str = 'experiment') -> Path:
    '''Create a minimal Limen experiment directory with random_binary SFD.'''

    exp_dir = tmp_path / name
    exp_dir.mkdir()

    metadata = {
        'sfd_module': 'limen.sfd.foundational_sfd.random_binary',
        'limen_version': '1.52.0',
        'created_at': '2026-01-01T00:00:00+00:00',
    }
    (exp_dir / 'metadata.json').write_text(json.dumps(metadata))

    round_entry = {
        'round_id': 1,
        'round_params': {
            'random_weights': 0.5,
            'breakout_threshold': 0.1,
            'shift': -1,
        },
    }
    (exp_dir / 'round_data.jsonl').write_text(json.dumps(round_entry) + '\n')

    return exp_dir


def _make_mock_state_store() -> MagicMock:
    mock = MagicMock(spec=StateStore)
    mock.recover.return_value = None
    mock.read_events.return_value = []
    return mock


def _make_manifest_yaml(
    tmp_path: Path,
    exp_dir: Path,
    permutation_ids: list[int] | None = None,
) -> Path:
    pids = permutation_ids or [1]
    manifest_path = tmp_path / 'manifest.yaml'
    strategy_file = tmp_path / 'strat.py'
    strategy_file.write_text(VALID_STRATEGY)

    pid_str = ', '.join(str(p) for p in pids)
    manifest_path.write_text(
        f'capital_pool: 10000\n'
        f'strategies:\n'
        f'  - id: test_strat\n'
        f'    file: strat.py\n'
        f'    sensors:\n'
        f'      - experiment: {exp_dir}\n'
        f'        permutation_ids: [{pid_str}]\n'
        f'        interval_seconds: 60\n'
        f'    capital_pct: 50\n'
    )
    return manifest_path


class TestWireSensors:

    def test_trains_sensor_from_experiment(self, tmp_path: Path) -> None:
        '''_wire_sensors trains a Sensor from a valid experiment directory.'''

        exp_dir = _make_experiment_dir(tmp_path)
        manifest_path = _make_manifest_yaml(tmp_path, exp_dir)

        sequencer = StartupSequencer(
            state_store=_make_mock_state_store(),
            manifest_path=manifest_path,
            strategies_base_path=tmp_path,
            allocated_capital=Decimal('10000'),
        )

        sequencer._recover_state()
        sequencer._load_manifest()
        sequencer._instantiate_strategies()
        sequencer._wire_sensors()

        assert len(sequencer.wired_sensors) == 1
        wired = sequencer.wired_sensors[0]
        assert wired.strategy_id == 'test_strat'
        assert wired.interval_seconds == 60
        assert wired.sensor is not None
        assert ':1' in wired.sensor_id
        assert len(wired.sensor_id.split(':')[0]) == 12

    def test_sensor_is_callable(self, tmp_path: Path) -> None:
        '''Trained Sensor can call predict().'''

        exp_dir = _make_experiment_dir(tmp_path)
        manifest_path = _make_manifest_yaml(tmp_path, exp_dir)

        sequencer = StartupSequencer(
            state_store=_make_mock_state_store(),
            manifest_path=manifest_path,
            strategies_base_path=tmp_path,
            allocated_capital=Decimal('10000'),
        )

        sequencer._recover_state()
        sequencer._load_manifest()
        sequencer._instantiate_strategies()
        sequencer._wire_sensors()

        wired = sequencer.wired_sensors[0]
        result = wired.sensor.predict({'x_test': np.array([[1, 2, 3]])})

        assert '_preds' in result

    def test_invalid_experiment_dir_raises(self, tmp_path: Path) -> None:
        '''Experiment directory without Limen artifacts raises StartupError.'''

        fake_dir = tmp_path / 'empty_experiment'
        fake_dir.mkdir()

        manifest_path = _make_manifest_yaml(tmp_path, fake_dir)

        sequencer = StartupSequencer(
            state_store=_make_mock_state_store(),
            manifest_path=manifest_path,
            strategies_base_path=tmp_path,
            allocated_capital=Decimal('10000'),
        )

        sequencer._recover_state()
        sequencer._load_manifest()
        sequencer._instantiate_strategies()

        with pytest.raises(StartupError, match='wire_sensors'):
            sequencer._wire_sensors()

    def test_invalid_permutation_id_raises(self, tmp_path: Path) -> None:
        '''Permutation ID not in round_data raises StartupError.'''

        exp_dir = _make_experiment_dir(tmp_path)
        manifest_path = _make_manifest_yaml(tmp_path, exp_dir, permutation_ids=[999])

        sequencer = StartupSequencer(
            state_store=_make_mock_state_store(),
            manifest_path=manifest_path,
            strategies_base_path=tmp_path,
            allocated_capital=Decimal('10000'),
        )

        sequencer._recover_state()
        sequencer._load_manifest()
        sequencer._instantiate_strategies()

        with pytest.raises(StartupError, match='wire_sensors'):
            sequencer._wire_sensors()

    def test_stores_limen_manifest_and_round_params(self, tmp_path: Path) -> None:
        '''WiredSensor contains limen_manifest and round_params.'''

        exp_dir = _make_experiment_dir(tmp_path)
        manifest_path = _make_manifest_yaml(tmp_path, exp_dir)

        sequencer = StartupSequencer(
            state_store=_make_mock_state_store(),
            manifest_path=manifest_path,
            strategies_base_path=tmp_path,
            allocated_capital=Decimal('10000'),
        )

        sequencer._recover_state()
        sequencer._load_manifest()
        sequencer._instantiate_strategies()
        sequencer._wire_sensors()

        wired = sequencer.wired_sensors[0]
        assert wired.limen_manifest is not None
        assert isinstance(wired.round_params, dict)
        assert 'random_weights' in wired.round_params
