'''Tests for signal_producer with real Limen Sensors.'''

from __future__ import annotations

import json
import os
from datetime import timezone
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from limen.experiment.trainer.trainer import Trainer

from nexus.startup.sequencer import WiredSensor
from nexus.strategy.signal_producer import _extract_values, produce_signal


def _find_limen_root() -> Path | None:
    try:
        import limen
        root = Path(limen.__file__).parent.parent
        if (root / 'datasets').is_dir():
            return root
    except ImportError:
        pass
    return None


_LIMEN_ROOT = _find_limen_root()
_HAS_LIMEN_DATA = _LIMEN_ROOT is not None

pytestmark = pytest.mark.skipif(
    not _HAS_LIMEN_DATA,
    reason='Limen datasets not available',
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


def _make_wired_sensor(tmp_path: Path) -> tuple[WiredSensor, pl.DataFrame]:
    '''Create a WiredSensor from real Limen Trainer and return with market data.'''

    exp_dir = tmp_path / 'experiment'
    exp_dir.mkdir()

    metadata = {
        'sfd_module': 'limen.sfd.foundational_sfd.random_binary',
        'limen_version': '1.52.0',
        'created_at': '2026-01-01T00:00:00+00:00',
    }
    (exp_dir / 'metadata.json').write_text(json.dumps(metadata))

    round_params = {
        'random_weights': 0.5,
        'breakout_threshold': 0.1,
        'shift': -1,
    }
    (exp_dir / 'round_data.jsonl').write_text(
        json.dumps({'round_id': 1, 'round_params': round_params}) + '\n'
    )

    trainer = Trainer(exp_dir)
    sensors = trainer.train([1])
    sensor = sensors[0]

    wired = WiredSensor(
        sensor_id='experiment:1',
        sensor=sensor,
        limen_manifest=trainer._manifest,
        round_params=sensor.round_params,
        strategy_id='test_strat',
        interval_seconds=60,
    )

    return wired, trainer._data


class TestProduceSignal:

    def test_produces_valid_signal(self, tmp_path: Path) -> None:
        '''produce_signal returns a Signal with correct fields.'''

        wired, market_data = _make_wired_sensor(tmp_path)
        signal = produce_signal(wired, market_data)

        assert signal.predictor_fn_id == 'experiment:1'
        assert signal.timestamp.tzinfo is timezone.utc
        assert '_preds' in signal.values
        assert '_probs' in signal.values

    def test_signal_values_are_scalars(self, tmp_path: Path) -> None:
        '''Signal values are Python scalars, not numpy types.'''

        wired, market_data = _make_wired_sensor(tmp_path)
        signal = produce_signal(wired, market_data)

        for val in signal.values.values():
            assert isinstance(val, (int, float))

    def test_empty_market_data_raises(self, tmp_path: Path) -> None:
        '''Empty market data raises ValueError.'''

        wired, _ = _make_wired_sensor(tmp_path)
        empty_df = pl.DataFrame()

        with pytest.raises(ValueError, match='market_data is empty'):
            produce_signal(wired, empty_df)


class TestExtractValues:

    def test_extracts_preds_and_probs(self) -> None:
        '''Extracts _preds and _probs from numpy arrays.'''

        result: dict[str, Any] = {
            '_preds': np.array([1]),
            '_probs': np.array([0.85]),
        }

        values = _extract_values(result)

        assert values['_preds'] == 1
        assert isinstance(values['_preds'], int)
        assert values['_probs'] == pytest.approx(0.85)
        assert isinstance(values['_probs'], float)

    def test_skips_private_keys(self) -> None:
        '''Skips private keys other than _preds and _probs.'''

        result: dict[str, Any] = {
            '_preds': np.array([1]),
            '_probs': np.array([0.9]),
            '_internal': np.array([42]),
        }

        values = _extract_values(result)

        assert '_internal' not in values
        assert '_preds' in values
        assert '_probs' in values

    def test_handles_scalar_values(self) -> None:
        '''Passes through plain int/float values.'''

        result: dict[str, Any] = {
            '_preds': np.array([0]),
            'confidence': 0.75,
        }

        values = _extract_values(result)

        assert values['confidence'] == 0.75

    def test_multi_element_array_takes_last(self) -> None:
        '''Multi-element array extracts last value.'''

        result: dict[str, Any] = {
            '_preds': np.array([0, 1, 1]),
        }

        values = _extract_values(result)

        assert values['_preds'] == 1.0
