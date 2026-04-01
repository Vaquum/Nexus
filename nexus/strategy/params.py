'''Strategy parameters from manifest configuration.'''

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True)
class StrategyParams:
    '''Strategy parameters from manifest configuration.

    Wraps the raw params dict from the manifest, providing typed access
    to user-defined strategy configuration. The dict is defensively copied
    and wrapped as immutable at construction.

    Args:
        raw: Raw parameters dict from manifest (stored as MappingProxyType).
    '''

    raw: dict[str, Any] | MappingProxyType[str, Any]

    def __post_init__(self) -> None:
        '''Validate and wrap raw dict as immutable.'''

        if not isinstance(self.raw, dict):
            msg = 'raw must be a dict'
            raise ValueError(msg)

        object.__setattr__(self, 'raw', MappingProxyType(dict(self.raw)))

    def get(self, key: str, default: Any = None) -> Any:
        '''Get a parameter value by key.

        Args:
            key: Parameter name.
            default: Value to return if key not found.

        Returns:
            Parameter value or default.
        '''

        return self.raw.get(key, default)
