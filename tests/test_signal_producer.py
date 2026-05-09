'''Tests for signal_producer with real Limen Sensors.'''

from __future__ import annotations

import json
import os
from datetime import timezone
from pathlib import Path
from typing import Any

import pytest

from nexus.startup.sequencer import WiredSensor
from nexus.strategy.signal_producer import _extract_values, produce_signal

limen = pytest.importorskip('limen')
_trainer_module = pytest.importorskip('limen.experiment.trainer.trainer')
Trainer = _trainer_module.Trainer
np = pytest.importorskip('numpy')
pl = pytest.importorskip('polars')


def _find_limen_root() -> Path | None:
    root = Path(limen.__file__).parent.parent
    if (root / 'datasets').is_dir():
        return root
    return None


_LIMEN_ROOT = _find_limen_root()
_HAS_LIMEN_DATA = _LIMEN_ROOT is not None

_needs_limen = pytest.mark.skipif(
    not _HAS_LIMEN_DATA,
    reason='Limen datasets not found adjacent to installed limen package',
)


@pytest.fixture()
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


@_needs_limen
class TestProduceSignal:

    def test_produces_valid_signal(self, tmp_path: Path, _limen_cwd: None) -> None:
        '''produce_signal returns a Signal with correct fields.'''

        wired, market_data = _make_wired_sensor(tmp_path)
        signal = produce_signal(wired, market_data)

        assert signal.predictor_fn_id == 'experiment:1'
        assert signal.timestamp.tzinfo is timezone.utc
        assert '_preds' in signal.values
        assert '_probs' in signal.values

    def test_signal_values_are_scalars(self, tmp_path: Path, _limen_cwd: None) -> None:
        '''Signal values are Python scalars, not numpy types.'''

        wired, market_data = _make_wired_sensor(tmp_path)
        signal = produce_signal(wired, market_data)

        for val in signal.values.values():
            assert isinstance(val, (int, float))

    def test_empty_market_data_raises(self, tmp_path: Path, _limen_cwd: None) -> None:
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

        assert values['_preds'] == 1

    def test_multi_element_int_array_preserves_int(self) -> None:
        '''Multi-element int array yields int (dtype preserved).'''

        result: dict[str, Any] = {
            '_preds': np.array([0, 1, 1, 0, 1]),
        }

        values = _extract_values(result)

        assert isinstance(values['_preds'], int)
        assert not isinstance(values['_preds'], bool)

    def test_multi_element_float_array_yields_float(self) -> None:
        '''Multi-element float array yields float (dtype preserved).'''

        result: dict[str, Any] = {
            '_probs': np.array([0.1, 0.5, 0.9]),
        }

        values = _extract_values(result)

        assert isinstance(values['_probs'], float)
        assert values['_probs'] == pytest.approx(0.9)

    def test_empty_array_skipped(self) -> None:
        '''Zero-element array is skipped (no key added).'''

        result: dict[str, Any] = {
            '_preds': np.array([], dtype=np.int64),
            '_probs': np.array([0.5]),
        }

        values = _extract_values(result)

        assert '_preds' not in values
        assert values['_probs'] == pytest.approx(0.5)


@_needs_limen
class TestLookback:

    def test_default_lookback_one_matches_baseline(
        self, tmp_path: Path, _limen_cwd: None,
    ) -> None:
        '''Default lookback=1 matches the no-lookback caller bit-for-bit.'''

        wired, market_data = _make_wired_sensor(tmp_path)
        signal_default = produce_signal(wired, market_data)
        signal_explicit = produce_signal(wired, market_data, lookback=1)

        assert signal_default.values == signal_explicit.values

    def test_lookback_greater_than_one_invokes_predict_with_n_rows(
        self, tmp_path: Path, _limen_cwd: None,
    ) -> None:
        '''lookback=N feeds N rows to sensor.predict.'''

        wired, market_data = _make_wired_sensor(tmp_path)

        captured: dict[str, Any] = {}
        original_predict = wired.sensor.predict

        def _capturing_predict(data: dict[str, Any]) -> dict[str, Any]:
            captured['x_test_shape'] = data['x_test'].shape
            return original_predict(data)

        wired.sensor.predict = _capturing_predict  # type: ignore[method-assign]
        try:
            produce_signal(wired, market_data, lookback=10)
        finally:
            wired.sensor.predict = original_predict  # type: ignore[method-assign]

        assert captured['x_test_shape'][0] == 10

    def test_predict_receives_polars_dataframe_preserving_column_names(
        self, tmp_path: Path, _limen_cwd: None,
    ) -> None:
        '''signal_producer must pass `x_test` as a polars DataFrame so SFDs can select features by name.

        Previously `x_train.tail(lookback).to_numpy()` discarded column names.
        SFDs that filter `_model_columns` from the live frame (e.g. the
        `BtcLogRegEVSFD` bundle, which records `self.model_cols` at fit time
        and calls `frame.select(self.model_cols)` in `_raw_probs`) then fell
        into a brittle index-based fallback that crashed when the predict-time
        frame had extra columns the training-time frame did not (e.g. binancial
        trade-aggregation produces `median`/`iqr` that the HF dataset does not).

        Pinning the type contract here defends the fix against regression.
        '''

        wired, market_data = _make_wired_sensor(tmp_path)

        captured: dict[str, Any] = {}
        original_predict = wired.sensor.predict

        def _capturing_predict(data: dict[str, Any]) -> dict[str, Any]:
            captured['x_test_type'] = type(data['x_test'])
            captured['x_test_columns'] = (
                list(data['x_test'].columns)
                if isinstance(data['x_test'], pl.DataFrame)
                else None
            )
            return original_predict(data)

        wired.sensor.predict = _capturing_predict  # type: ignore[method-assign]
        try:
            produce_signal(wired, market_data)
        finally:
            wired.sensor.predict = original_predict  # type: ignore[method-assign]

        assert captured['x_test_type'] is pl.DataFrame
        assert captured['x_test_columns'] is not None
        assert len(captured['x_test_columns']) > 0

    def test_lookback_signal_carries_last_row(
        self, tmp_path: Path, _limen_cwd: None,
    ) -> None:
        '''Signal values come from the LAST row of the multi-row predict.'''

        wired, market_data = _make_wired_sensor(tmp_path)

        original_predict = wired.sensor.predict

        def _last_marker_predict(data: dict[str, Any]) -> dict[str, Any]:
            n = data['x_test'].shape[0]
            preds = np.zeros(n, dtype=np.int64)
            probs = np.zeros(n, dtype=np.float64)
            preds[-1] = 1
            probs[-1] = 0.9
            return {'_preds': preds, '_probs': probs}

        wired.sensor.predict = _last_marker_predict  # type: ignore[method-assign]
        try:
            signal = produce_signal(wired, market_data, lookback=5)
        finally:
            wired.sensor.predict = original_predict  # type: ignore[method-assign]

        assert signal.values['_preds'] == 1
        assert isinstance(signal.values['_preds'], int)
        assert signal.values['_probs'] == pytest.approx(0.9)

    def test_lookback_zero_raises(
        self, tmp_path: Path, _limen_cwd: None,
    ) -> None:
        wired, market_data = _make_wired_sensor(tmp_path)
        with pytest.raises(ValueError, match='must be >= 1'):
            produce_signal(wired, market_data, lookback=0)

    def test_lookback_negative_raises(
        self, tmp_path: Path, _limen_cwd: None,
    ) -> None:
        wired, market_data = _make_wired_sensor(tmp_path)
        with pytest.raises(ValueError, match='must be >= 1'):
            produce_signal(wired, market_data, lookback=-3)

    def test_lookback_non_int_raises(
        self, tmp_path: Path, _limen_cwd: None,
    ) -> None:
        wired, market_data = _make_wired_sensor(tmp_path)
        with pytest.raises(TypeError, match='must be int'):
            produce_signal(wired, market_data, lookback=1.0)  # type: ignore[arg-type]

    def test_lookback_bool_raises(
        self, tmp_path: Path, _limen_cwd: None,
    ) -> None:
        '''bool subclasses int but is rejected for clarity.'''

        wired, market_data = _make_wired_sensor(tmp_path)
        with pytest.raises(TypeError, match='must be int'):
            produce_signal(wired, market_data, lookback=True)  # type: ignore[arg-type]
