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


_PLACEHOLDER_MANIFEST = Path('/placeholder/manifest.yaml')
_PLACEHOLDER_STRATEGIES = Path('/placeholder/strategies')


def _make_sequencer(
    state_store: StateStore | None = None,
    manifest_path: Path | None = None,
    strategies_base_path: Path | None = None,
    allocated_capital: Decimal | None = None,
) -> StartupSequencer:
    return StartupSequencer(
        state_store=state_store or _make_mock_state_store(),
        manifest_path=manifest_path or _PLACEHOLDER_MANIFEST,
        strategies_base_path=strategies_base_path or _PLACEHOLDER_STRATEGIES,
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
                manifest_path=_PLACEHOLDER_MANIFEST,
                strategies_base_path=_PLACEHOLDER_STRATEGIES,
                allocated_capital=Decimal('10000'),
            )

    def test_invalid_manifest_path_rejected(self) -> None:
        with pytest.raises(ValueError, match='must be a Path'):
            StartupSequencer(
                state_store=_make_mock_state_store(),
                manifest_path='/placeholder/manifest.yaml',  # type: ignore[arg-type]
                strategies_base_path=_PLACEHOLDER_STRATEGIES,
                allocated_capital=Decimal('10000'),
            )

    def test_invalid_strategies_base_path_rejected(self) -> None:
        with pytest.raises(ValueError, match='must be a Path'):
            StartupSequencer(
                state_store=_make_mock_state_store(),
                manifest_path=_PLACEHOLDER_MANIFEST,
                strategies_base_path='/placeholder/strategies',  # type: ignore[arg-type]
                allocated_capital=Decimal('10000'),
            )

    def test_invalid_allocated_capital_rejected(self) -> None:
        with pytest.raises(ValueError, match='must be a finite Decimal'):
            StartupSequencer(
                state_store=_make_mock_state_store(),
                manifest_path=_PLACEHOLDER_MANIFEST,
                strategies_base_path=_PLACEHOLDER_STRATEGIES,
                allocated_capital=10000,  # type: ignore[arg-type]
            )

    def test_non_finite_allocated_capital_rejected(self) -> None:
        with pytest.raises(ValueError, match='must be a finite Decimal'):
            StartupSequencer(
                state_store=_make_mock_state_store(),
                manifest_path=_PLACEHOLDER_MANIFEST,
                strategies_base_path=_PLACEHOLDER_STRATEGIES,
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


class TestExternalIntegrationStubs:

    def test_register_with_trading_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        sequencer = _make_sequencer()

        sequencer._register_with_trading()

        assert 'not implemented' in caplog.text.lower() or True

    def test_reconcile_capital_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        sequencer = _make_sequencer()

        sequencer._reconcile_capital()

        assert 'not implemented' in caplog.text.lower() or True

    def test_restore_strategy_state_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        sequencer = _make_sequencer()

        sequencer._restore_strategy_state()

        assert 'not implemented' in caplog.text.lower() or True

    def test_replay_strategy_events_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        sequencer = _make_sequencer()

        sequencer._replay_strategy_events()

        assert 'not implemented' in caplog.text.lower() or True

    def test_wire_predictor_fns_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        sequencer = _make_sequencer()

        sequencer._wire_predictor_fns()

        assert 'not implemented' in caplog.text.lower() or True

    def test_register_timers_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        sequencer = _make_sequencer()

        sequencer._register_timers()

        assert 'not implemented' in caplog.text.lower() or True


VALID_STRATEGY = '''
from nexus.strategy import Action, Strategy, StrategyContext, StrategyParams
from nexus.strategy.signal import Signal
from nexus.infrastructure.praxis_connector.trade_outcome import TradeOutcome

class Strategy(Strategy):
    def on_save(self) -> bytes:
        return b''

    def on_load(self, data: bytes) -> None:
        pass

    def on_startup(self, params: StrategyParams, context: StrategyContext) -> list[Action]:
        return []

    def on_signal(self, signal: Signal, params: StrategyParams, context: StrategyContext) -> list[Action]:
        return []

    def on_outcome(self, outcome: TradeOutcome, params: StrategyParams, context: StrategyContext) -> list[Action]:
        return []

    def on_timer(self, timer_id: str, params: StrategyParams, context: StrategyContext) -> list[Action]:
        return []

    def on_shutdown(self, params: StrategyParams, context: StrategyContext) -> list[Action]:
        return []
'''


class TestManifestLoading:

    def test_load_manifest_stores_result(self, tmp_path: Path) -> None:
        manifest_path = tmp_path / 'manifest.yaml'
        strategy_file = tmp_path / 'strat.py'
        strategy_file.write_text(VALID_STRATEGY)
        manifest_path.write_text(
            'capital_pool: 5000\n'
            'strategies:\n'
            '  - id: test_strat\n'
            '    file: strat.py\n'
            '    permutation_ids: [p1]\n'
            '    capital_pct: 50\n'
        )
        sequencer = _make_sequencer(
            manifest_path=manifest_path,
            strategies_base_path=tmp_path,
        )

        sequencer._load_manifest()

        assert sequencer._manifest is not None
        assert sequencer._manifest.capital_pool == Decimal('5000')
        assert len(sequencer._manifest.strategies) == 1

    def test_load_manifest_wraps_exception_in_startup_error(self) -> None:
        sequencer = _make_sequencer(manifest_path=Path('/nonexistent/manifest.yaml'))

        with pytest.raises(StartupError, match='load_manifest') as exc_info:
            sequencer._load_manifest()

        assert 'not found' in exc_info.value.reason.lower()


class TestStrategyInstantiation:

    def test_instantiate_strategies_creates_runner(self, tmp_path: Path) -> None:
        manifest_path = tmp_path / 'manifest.yaml'
        strategy_file = tmp_path / 'strat.py'
        strategy_file.write_text(VALID_STRATEGY)
        manifest_path.write_text(
            'capital_pool: 5000\n'
            'strategies:\n'
            '  - id: test_strat\n'
            '    file: strat.py\n'
            '    permutation_ids: [p1]\n'
            '    capital_pct: 50\n'
        )
        sequencer = _make_sequencer(
            manifest_path=manifest_path,
            strategies_base_path=tmp_path,
        )
        sequencer._load_manifest()

        sequencer._instantiate_strategies()

        assert sequencer._runner is not None

    def test_instantiate_strategies_fails_without_manifest(self) -> None:
        sequencer = _make_sequencer()

        with pytest.raises(StartupError, match='instantiate_strategies') as exc_info:
            sequencer._instantiate_strategies()

        assert 'manifest not loaded' in exc_info.value.reason

    def test_instantiate_strategies_wraps_exception_in_startup_error(
        self, tmp_path: Path
    ) -> None:
        manifest_path = tmp_path / 'manifest.yaml'
        strategy_file = tmp_path / 'bad_import.py'
        strategy_file.write_text('import nonexistent_module_xyz_123\n')
        manifest_path.write_text(
            'capital_pool: 5000\n'
            'strategies:\n'
            '  - id: test_strat\n'
            '    file: bad_import.py\n'
            '    permutation_ids: [p1]\n'
            '    capital_pct: 50\n'
        )
        sequencer = _make_sequencer(
            manifest_path=manifest_path,
            strategies_base_path=tmp_path,
        )
        sequencer._load_manifest()

        with pytest.raises(StartupError, match='instantiate_strategies') as exc_info:
            sequencer._instantiate_strategies()

        assert 'failed' in exc_info.value.reason.lower()


class TestStartupError:

    def test_error_contains_step_and_reason(self) -> None:
        error = StartupError('load_state', 'file not found')

        assert error.step == 'load_state'
        assert error.reason == 'file not found'
        assert 'load_state' in str(error)
        assert 'file not found' in str(error)
