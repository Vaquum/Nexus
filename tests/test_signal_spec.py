'''Tests for SignalSpec dataclass.'''

from __future__ import annotations

import pytest

from nexus.infrastructure.manifest import SignalSpec


class TestSignalSpec:

    def test_valid_spec(self) -> None:
        '''Valid SignalSpec creates successfully.'''

        spec = SignalSpec(
            series='time_15m',
            interval_seconds=900,
        )

        assert spec.series == 'time_15m'
        assert spec.interval_seconds == 900
        assert spec.stale_policy == 'skip'
        assert spec.name is None

    def test_valid_spec_with_optional_fields(self) -> None:
        '''Valid SignalSpec with optional fields creates successfully.'''

        spec = SignalSpec(
            series='time_15m',
            interval_seconds=900,
            stale_policy='skip',
            name='cohort_label',
        )

        assert spec.stale_policy == 'skip'
        assert spec.name == 'cohort_label'

    def test_frozen(self) -> None:
        '''SignalSpec is immutable.'''

        spec = SignalSpec(
            series='time_15m',
            interval_seconds=900,
        )

        with pytest.raises(AttributeError):
            spec.interval_seconds = 30  # type: ignore[misc]

    def test_empty_series_raises(self) -> None:
        '''Empty series raises ValueError.'''

        with pytest.raises(
            ValueError,
            match='series must be a non-empty string without surrounding whitespace',
        ):
            SignalSpec(
                series='',
                interval_seconds=900,
            )

    def test_whitespace_series_raises(self) -> None:
        '''Whitespace-only series raises ValueError.'''

        with pytest.raises(
            ValueError,
            match='series must be a non-empty string without surrounding whitespace',
        ):
            SignalSpec(
                series='   ',
                interval_seconds=900,
            )

    def test_padded_series_raises(self) -> None:
        '''Series with surrounding whitespace raises ValueError.'''

        with pytest.raises(
            ValueError,
            match='series must be a non-empty string without surrounding whitespace',
        ):
            SignalSpec(
                series=' time_15m ',
                interval_seconds=900,
            )

    def test_series_not_str_raises(self) -> None:
        '''Non-str series raises ValueError.'''

        with pytest.raises(
            ValueError,
            match='series must be a non-empty string without surrounding whitespace',
        ):
            SignalSpec(
                series=123,  # type: ignore[arg-type]
                interval_seconds=900,
            )

    def test_zero_interval_raises(self) -> None:
        '''Zero interval_seconds raises ValueError.'''

        with pytest.raises(ValueError, match='interval_seconds must be positive'):
            SignalSpec(
                series='time_15m',
                interval_seconds=0,
            )

    def test_negative_interval_raises(self) -> None:
        '''Negative interval_seconds raises ValueError.'''

        with pytest.raises(ValueError, match='interval_seconds must be positive'):
            SignalSpec(
                series='time_15m',
                interval_seconds=-10,
            )

    def test_bool_interval_raises(self) -> None:
        '''Bool interval_seconds raises ValueError.'''

        with pytest.raises(ValueError, match='interval_seconds must be an int'):
            SignalSpec(
                series='time_15m',
                interval_seconds=True,  # type: ignore[arg-type]
            )

    def test_non_int_interval_raises(self) -> None:
        '''Non-int interval_seconds raises ValueError.'''

        with pytest.raises(ValueError, match='interval_seconds must be an int'):
            SignalSpec(
                series='time_15m',
                interval_seconds='900',  # type: ignore[arg-type]
            )

    def test_invalid_stale_policy_raises(self) -> None:
        '''stale_policy other than skip raises ValueError.'''

        with pytest.raises(ValueError, match="stale_policy must be 'skip'"):
            SignalSpec(
                series='time_15m',
                interval_seconds=900,
                stale_policy='hold',
            )

    def test_non_str_name_raises(self) -> None:
        '''Non-str name raises ValueError.'''

        with pytest.raises(ValueError, match='name must be a string or None'):
            SignalSpec(
                series='time_15m',
                interval_seconds=900,
                name=123,  # type: ignore[arg-type]
            )
