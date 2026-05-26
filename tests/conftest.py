'''Shared pytest fixtures for the Nexus test suite.'''

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_sensor_cache_env(monkeypatch: pytest.MonkeyPatch) -> None:
    '''Clear `NEXUS_SENSOR_CACHE_DIR` so the opt-in disk cache is off by default.

    Tests that patch `Trainer` and drive `_wire_sensors` would otherwise
    take the cache-HIT path — bypassing the patched reconstruction and
    reading/writing an external cache directory — whenever the variable
    leaks in from the developer or CI environment. Tests that exercise
    the cache set it explicitly in-body, which overrides this fixture.
    '''

    monkeypatch.delenv('NEXUS_SENSOR_CACHE_DIR', raising=False)
