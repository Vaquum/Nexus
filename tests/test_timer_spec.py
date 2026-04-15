'''Tests for TimerSpec dataclass validation.'''

from __future__ import annotations

import pytest

from nexus.infrastructure.manifest import TimerSpec


class TestTimerSpec:

    def test_valid_spec(self) -> None:
        '''Valid TimerSpec creates successfully.'''

        spec = TimerSpec(timer_id='trailing_stop', interval_seconds=30)

        assert spec.timer_id == 'trailing_stop'
        assert spec.interval_seconds == 30

    def test_frozen(self) -> None:
        '''TimerSpec is immutable.'''

        spec = TimerSpec(timer_id='check', interval_seconds=60)

        with pytest.raises(AttributeError):
            spec.interval_seconds = 10  # type: ignore[misc]

    def test_empty_timer_id_raises(self) -> None:
        '''Empty timer_id raises ValueError.'''

        with pytest.raises(ValueError, match='timer_id must be a non-empty string'):
            TimerSpec(timer_id='', interval_seconds=60)

    def test_whitespace_timer_id_raises(self) -> None:
        '''Whitespace-only timer_id raises ValueError.'''

        with pytest.raises(ValueError, match='timer_id must be a non-empty string'):
            TimerSpec(timer_id='   ', interval_seconds=60)

    def test_zero_interval_raises(self) -> None:
        '''Zero interval_seconds raises ValueError.'''

        with pytest.raises(ValueError, match='interval_seconds must be positive'):
            TimerSpec(timer_id='check', interval_seconds=0)

    def test_negative_interval_raises(self) -> None:
        '''Negative interval_seconds raises ValueError.'''

        with pytest.raises(ValueError, match='interval_seconds must be positive'):
            TimerSpec(timer_id='check', interval_seconds=-1)

    def test_bool_interval_raises(self) -> None:
        '''Bool interval_seconds raises ValueError.'''

        with pytest.raises(ValueError, match='interval_seconds must be an int'):
            TimerSpec(timer_id='check', interval_seconds=True)  # type: ignore[arg-type]
