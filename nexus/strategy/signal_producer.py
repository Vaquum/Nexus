'''Produce Signal from a WiredSensor and market data.'''

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np
import polars as pl

from nexus.startup.sequencer import WiredSensor
from nexus.strategy.signal import Signal

__all__ = ['produce_signal']


def produce_signal(wired: WiredSensor, market_data: pl.DataFrame) -> Signal:
    '''Run feature preparation and predict to produce a Signal.

    Args:
        wired: Trained Sensor with limen_manifest and round_params.
        market_data: Rolling DataFrame of market bars.

    Returns:
        Signal with predict output as values.

    Raises:
        ValueError: If market_data is empty or x_train is missing.
        Exception: Arbitrary exceptions from Limen prepare_data or predict.
    '''

    if market_data.is_empty():
        msg = f'market_data is empty for sensor {wired.sensor_id}'
        raise ValueError(msg)

    manifest_full = wired.limen_manifest.with_params_override(
        split_config=(1, 0, 0),
    )
    data_dict = manifest_full.prepare_data(market_data, wired.round_params)

    x_train = data_dict.get('x_train')

    if x_train is None or x_train.is_empty():
        msg = f'prepare_data returned no x_train for sensor {wired.sensor_id}'
        raise ValueError(msg)

    last_row = x_train.tail(1).to_numpy()

    result = wired.sensor.predict({'x_test': last_row})

    values = _extract_values(result)

    return Signal(
        predictor_fn_id=wired.sensor_id,
        values=values,
        timestamp=datetime.now(tz=timezone.utc),
    )


def _extract_values(result: dict[str, Any]) -> dict[str, Any]:
    '''Convert predict output to Signal-compatible values dict.

    Extracts scalar values from numpy arrays. Skips private keys
    that are not signal values.
    '''

    values: dict[str, Any] = {}

    for key, val in result.items():
        if key.startswith('_') and key not in ('_preds', '_probs'):
            continue

        if isinstance(val, np.ndarray):
            if val.size == 1:
                scalar = val.item()
                values[key] = int(scalar) if isinstance(scalar, (np.integer, int)) else float(scalar)
            else:
                values[key] = float(val[-1])
        elif isinstance(val, np.generic):
            values[key] = int(val) if isinstance(val, np.integer) else float(val)
        elif isinstance(val, (int, float)):
            values[key] = val

    return values
