'''Signal from predictor function.'''

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
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
        timestamp: When the signal was generated (must be timezone-aware).
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

        if (
            self.timestamp.tzinfo is None
            or self.timestamp.tzinfo.utcoffset(self.timestamp) is None
        ):
            msg = 'timestamp must be timezone-aware'
            raise ValueError(msg)

        object.__setattr__(self, 'values', MappingProxyType(dict(self.values)))

    def get(self, key: str, default: Any = None) -> Any:
        '''Get a signal value by key.

        Args:
            key: Signal key.
            default: Value to return if key not found.

        Returns:
            Signal value or default.
        '''

        return self.values.get(key, default)
