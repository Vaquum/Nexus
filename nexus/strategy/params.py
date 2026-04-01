'''Strategy parameters from manifest configuration.'''

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StrategyParams:
    '''Strategy parameters from manifest configuration.

    Wraps the raw params dict from the manifest, providing typed access
    to user-defined strategy configuration.

    Args:
        raw: Raw parameters dict from manifest.
    '''

    raw: dict[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.raw, dict):
            msg = 'raw must be a dict'
            raise ValueError(msg)

    def get(self, key: str, default: Any = None) -> Any:
        '''Get a parameter value by key.

        Args:
            key: Parameter name.
            default: Value to return if key not found.

        Returns:
            Parameter value or default.
        '''

        return self.raw.get(key, default)
