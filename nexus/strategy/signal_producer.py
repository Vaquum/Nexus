'''Produce Signal from a WiredSensor and market data.'''

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np
import polars as pl

from limen.experiment import RuleBasedManifest

from nexus.startup.sequencer import WiredSensor
from nexus.strategy.signal import Signal

__all__ = ['produce_signal']


def produce_signal(
    wired: WiredSensor,
    market_data: pl.DataFrame,
    *,
    lookback: int = 1,
) -> Signal:
    '''Run feature preparation and predict to produce a Signal.

    Supports both `MLManifest` and `RuleBasedManifest` SFDs by branching
    on the manifest type to pick the right `prepare_data` output keys
    (`x_train`/`x_test` for ML, `train`/`test` for rule-based).

    Args:
        wired: Trained Sensor with limen_manifest and round_params.
        market_data: Rolling DataFrame of market bars.
        lookback: Trailing prepared rows fed to sensor.predict. Default 1.

    Returns:
        Signal with predict output as values.

    Raises:
        TypeError: If lookback is not int.
        ValueError: If market_data is empty, the train frame is missing,
            or lookback < 1.
        Exception: Arbitrary exceptions from Limen prepare_data or predict.
    '''

    if not isinstance(lookback, int) or isinstance(lookback, bool):
        msg = f'lookback must be int, got {type(lookback).__name__}'
        raise TypeError(msg)
    if lookback < 1:
        msg = f'lookback must be >= 1, got {lookback}'
        raise ValueError(msg)

    if market_data.is_empty():
        msg = f'market_data is empty for sensor {wired.sensor_id}'
        raise ValueError(msg)

    is_rule_based = isinstance(wired.limen_manifest, RuleBasedManifest)
    train_key = 'train' if is_rule_based else 'x_train'
    test_key = 'test' if is_rule_based else 'x_test'

    manifest_full = wired.limen_manifest.with_params_override(
        split_config=(1, 0, 0),
    )
    data_dict = manifest_full.prepare_data(market_data, wired.round_params)

    train_frame = data_dict.get(train_key)

    if train_frame is None or train_frame.is_empty():
        msg = (
            f'prepare_data returned no {train_key!r} for sensor '
            f'{wired.sensor_id}'
        )
        raise ValueError(msg)

    tail_frame = train_frame.tail(lookback)

    result = wired.sensor.predict({test_key: tail_frame})

    values = _extract_values(result)

    return Signal(
        predictor_fn_id=wired.sensor_id,
        values=values,
        timestamp=datetime.now(tz=timezone.utc),
    )


def _extract_values(result: dict[str, Any]) -> dict[str, Any]:
    '''Convert predict output to Signal-compatible values dict.

    Extracts scalar values from numpy arrays. Skips private keys
    that are not signal values. Multi-element arrays use the last
    element with dtype preserved.
    '''

    values: dict[str, Any] = {}

    for key, val in result.items():
        if key.startswith('_') and key not in ('_preds', '_probs'):
            continue

        if isinstance(val, np.ndarray):
            if val.size == 0:
                continue
            last = val.item() if val.size == 1 else val[-1]
            values[key] = int(last) if isinstance(last, (np.integer, int)) else float(last)
        elif isinstance(val, np.generic):
            values[key] = int(val) if isinstance(val, np.integer) else float(val)
        elif isinstance(val, (int, float)):
            values[key] = val

    return values
