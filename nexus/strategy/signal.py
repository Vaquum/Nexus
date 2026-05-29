'''Signal from predictor function.'''

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True)
class Signal:
    '''Signal output from a predictor function.

    Args:
        predictor_fn_id: Identifier of the predictor function that produced this signal.
        values: Signal values dict. Keys must be strings. Numeric values (int, float,
            Decimal) must be finite. Common patterns: binary flags (CAN_ENTER=1),
            confidence scores, directional strength.
        timestamp: When the signal was generated (must be UTC).
    '''

    predictor_fn_id: str
    values: dict[str, Any]
    timestamp: datetime

    def __post_init__(self) -> None:
        '''Validate fields and wrap values as immutable.'''

        if not isinstance(self.predictor_fn_id, str) or not self.predictor_fn_id.strip():
            msg = 'predictor_fn_id must be a non-empty string'
            raise ValueError(msg)

        if not isinstance(self.values, dict):
            msg = 'values must be a dict'
            raise ValueError(msg)

        for key, val in self.values.items():
            if not isinstance(key, str):
                msg = 'values keys must be strings'
                raise ValueError(msg)

            if isinstance(val, (int, float)) and not math.isfinite(val):
                msg = f'values[{key!r}] must be finite'
                raise ValueError(msg)

            if isinstance(val, Decimal) and not val.is_finite():
                msg = f'values[{key!r}] must be finite'
                raise ValueError(msg)

        if not isinstance(self.timestamp, datetime):
            msg = 'timestamp must be a datetime'
            raise ValueError(msg)

        if self.timestamp.tzinfo is not timezone.utc:
            msg = 'timestamp must be UTC'
            raise ValueError(msg)

        object.__setattr__(self, 'values', MappingProxyType(dict(self.values)))

    def __reduce__(self) -> tuple[Any, tuple[Any, ...]]:
        '''Pickle protocol: ship `values` as a plain dict across the wire.

        `__post_init__` wraps `values` in a `MappingProxyType` for
        immutability, which is not picklable. Without this hook, returning
        a `Signal` from a `ProcessPoolExecutor` worker (e.g. the
        `PredictLoop` spawn pool) fails with
        `TypeError: cannot pickle 'mappingproxy' object` inside the pool's
        `_sendback_result`. `__reduce__` re-emits the constructor args with
        `values` unwrapped to a plain dict; unpickle re-runs
        `__post_init__` and re-wraps it on the receiving side, preserving
        the immutability contract for consumers without ever putting a
        `MappingProxyType` on the wire.
        '''

        return (
            self.__class__,
            (self.predictor_fn_id, dict(self.values), self.timestamp),
        )

    def get(self, key: str, default: Any = None) -> Any:
        '''Get a signal value by key.

        Args:
            key: Signal key.
            default: Value to return if key not found.

        Returns:
            Signal value or default.
        '''

        return self.values.get(key, default)
