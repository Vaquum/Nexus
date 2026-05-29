'''Verify Signal survives the spawn ProcessPoolExecutor result-pickle path.

`Signal.__post_init__` wraps `values` in `types.MappingProxyType` for
immutability, which is not picklable by default. The original
`PredictLoop` ThreadPoolExecutor design never crossed a process
boundary, so the gap was latent; the v0.53.0 spawn
`ProcessPoolExecutor` does pickle every worker return value, and a
deploy without this test broke prod with
`TypeError: cannot pickle 'mappingproxy' object` in
`_sendback_result`.

This test would have failed before `Signal.__reduce__` was added; it
fails again if the `__reduce__` is removed or stops unwrapping
`values` to a plain dict. It uses a real `multiprocessing.spawn`
context and a real `ProcessPoolExecutor` so the assertion is the
production failure path, not a sync test-double approximation.
'''

from __future__ import annotations

import multiprocessing
import pickle
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from types import MappingProxyType

from nexus.strategy.signal import Signal


def _build_signal_in_worker() -> Signal:
    '''Module-level so the spawn pool can pickle the callable.

    Returns a `Signal` whose `values` will have been wrapped in
    `MappingProxyType` by `Signal.__post_init__`; the test asserts the
    pool can return this object to the parent without raising.
    '''

    return Signal(
        predictor_fn_id='spawn_pool_pickle_probe',
        values={'_preds': 1, '_probs': 0.85, 'close': 70500.0},
        timestamp=datetime.now(tz=timezone.utc),
    )


class TestSignalPickle:
    def test_round_trips_through_pickle(self) -> None:
        '''Direct `pickle.dumps`/`loads` round-trip preserves values.'''

        original = Signal(
            predictor_fn_id='direct_pickle',
            values={'_preds': 1, '_probs': 0.62, 'close': 73000.0},
            timestamp=datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc),
        )

        payload = pickle.dumps(original)
        recovered = pickle.loads(payload)  # noqa: S301 - test round-trips its own dump

        assert isinstance(recovered, Signal)
        assert recovered.predictor_fn_id == 'direct_pickle'
        assert recovered.timestamp == original.timestamp
        assert dict(recovered.values) == {
            '_preds': 1,
            '_probs': 0.62,
            'close': 73000.0,
        }
        assert isinstance(recovered.values, MappingProxyType)

    def test_survives_spawn_process_pool_result_pickle(self) -> None:
        '''Signal returned from a spawn ProcessPoolExecutor worker
        survives the pool's `_sendback_result` pickle.

        The production failure path: worker runs `_predict_in_process`,
        returns a `Signal`, pool pickles it to the result queue, parent
        unpickles it via `future.result()`. Without
        `Signal.__reduce__`, this raises
        `TypeError: cannot pickle 'mappingproxy' object` on the worker
        side and the parent's `future.result()` re-raises with that
        traceback chained.
        '''

        ctx = multiprocessing.get_context('spawn')

        with ProcessPoolExecutor(max_workers=1, mp_context=ctx) as pool:
            result = pool.submit(_build_signal_in_worker).result(timeout=30)

        assert isinstance(result, Signal)
        assert result.predictor_fn_id == 'spawn_pool_pickle_probe'
        assert dict(result.values) == {
            '_preds': 1,
            '_probs': 0.85,
            'close': 70500.0,
        }
        assert isinstance(result.values, MappingProxyType)
