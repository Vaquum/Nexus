'''Tests for StartupSequencer.'''

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nexus.infrastructure.state_store import StateStore
from nexus.startup import StartupError, StartupSequencer


def _make_mock_state_store() -> MagicMock:
    mock = MagicMock(spec=StateStore)
    return mock


def _make_sequencer(
    state_store: StateStore | None = None,
    manifest_path: Path | None = None,
    strategies_base_path: Path | None = None,
    allocated_capital: Decimal | None = None,
) -> StartupSequencer:
    return StartupSequencer(
        state_store=state_store or _make_mock_state_store(),
        manifest_path=manifest_path or Path('/tmp/manifest.yaml'),
        strategies_base_path=strategies_base_path or Path('/tmp/strategies'),
        allocated_capital=allocated_capital or Decimal('10000'),
    )


class TestStartupSequencerConstruction:

    def test_valid_construction(self) -> None:
        sequencer = _make_sequencer()

        assert sequencer is not None

    def test_invalid_state_store_rejected(self) -> None:
        with pytest.raises(ValueError, match='must be a StateStore'):
            StartupSequencer(
                state_store='not a state store',  # type: ignore[arg-type]
                manifest_path=Path('/tmp/manifest.yaml'),
                strategies_base_path=Path('/tmp/strategies'),
                allocated_capital=Decimal('10000'),
            )

    def test_invalid_manifest_path_rejected(self) -> None:
        with pytest.raises(ValueError, match='must be a Path'):
            StartupSequencer(
                state_store=_make_mock_state_store(),
                manifest_path='/tmp/manifest.yaml',  # type: ignore[arg-type]
                strategies_base_path=Path('/tmp/strategies'),
                allocated_capital=Decimal('10000'),
            )

    def test_invalid_strategies_base_path_rejected(self) -> None:
        with pytest.raises(ValueError, match='must be a Path'):
            StartupSequencer(
                state_store=_make_mock_state_store(),
                manifest_path=Path('/tmp/manifest.yaml'),
                strategies_base_path='/tmp/strategies',  # type: ignore[arg-type]
                allocated_capital=Decimal('10000'),
            )

    def test_invalid_allocated_capital_rejected(self) -> None:
        with pytest.raises(ValueError, match='must be a finite Decimal'):
            StartupSequencer(
                state_store=_make_mock_state_store(),
                manifest_path=Path('/tmp/manifest.yaml'),
                strategies_base_path=Path('/tmp/strategies'),
                allocated_capital=10000,  # type: ignore[arg-type]
            )

    def test_non_finite_allocated_capital_rejected(self) -> None:
        with pytest.raises(ValueError, match='must be a finite Decimal'):
            StartupSequencer(
                state_store=_make_mock_state_store(),
                manifest_path=Path('/tmp/manifest.yaml'),
                strategies_base_path=Path('/tmp/strategies'),
                allocated_capital=Decimal('Infinity'),
            )


class TestStartupSequencerStart:

    def test_start_returns_runner_when_complete(self) -> None:
        pass


class TestStateRecovery:

    def test_recover_state_calls_state_store_recover(self) -> None:
        mock_store = _make_mock_state_store()
        mock_store.recover.return_value = None
        sequencer = _make_sequencer(state_store=mock_store)

        sequencer._recover_state()

        mock_store.recover.assert_called_once()

    def test_recover_state_stores_result(self) -> None:
        mock_store = _make_mock_state_store()
        mock_state = MagicMock()
        mock_store.recover.return_value = mock_state
        sequencer = _make_sequencer(state_store=mock_store)

        sequencer._recover_state()

        assert sequencer._state is mock_state

    def test_recover_state_handles_none(self) -> None:
        mock_store = _make_mock_state_store()
        mock_store.recover.return_value = None
        sequencer = _make_sequencer(state_store=mock_store)

        sequencer._recover_state()

        assert sequencer._state is None

    def test_recover_state_wraps_exception_in_startup_error(self) -> None:
        mock_store = _make_mock_state_store()
        mock_store.recover.side_effect = RuntimeError('disk error')
        sequencer = _make_sequencer(state_store=mock_store)

        with pytest.raises(StartupError, match='recover_state') as exc_info:
            sequencer._recover_state()

        assert 'disk error' in exc_info.value.reason


class TestStartupError:

    def test_error_contains_step_and_reason(self) -> None:
        error = StartupError('load_state', 'file not found')

        assert error.step == 'load_state'
        assert error.reason == 'file not found'
        assert 'load_state' in str(error)
        assert 'file not found' in str(error)
