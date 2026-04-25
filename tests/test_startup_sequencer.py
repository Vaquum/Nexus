'''Tests for StartupSequencer.'''

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nexus.core.domain.capital_state import CapitalState
from nexus.core.domain.enums import OperationalMode, OrderSide
from nexus.core.domain.instance_state import InstanceState
from nexus.infrastructure.state_store import StateStore
from nexus.infrastructure.strategy_event import StrategyEvent
from nexus.startup import StartupError, StartupSequencer
from nexus.strategy.runner import StrategyRunner


@pytest.fixture(autouse=True)
def _mock_trainer() -> None:
    '''Patch Limen Trainer so startup tests skip real training.'''

    mock_sensor = MagicMock()
    mock_sensor.permutation_id = 1
    mock_sensor.round_params = {}

    mock_trainer = MagicMock()
    mock_trainer.return_value.train.return_value = [mock_sensor]
    mock_trainer.return_value._manifest = MagicMock()

    with patch('nexus.startup.sequencer.Trainer', mock_trainer):
        yield


def _make_mock_state_store() -> MagicMock:
    mock = MagicMock(spec=StateStore)
    return mock


def _sensors_yaml(tmp_path: Path) -> str:
    exp_dir = tmp_path / 'experiment'
    exp_dir.mkdir(exist_ok=True)
    return (
        f'    sensors:\n'
        f'      - experiment: {exp_dir}\n'
        f'        permutation_ids: [1]\n'
        f'        interval_seconds: 60\n'
    )


_PLACEHOLDER_MANIFEST = Path('/placeholder/manifest.yaml')
_PLACEHOLDER_STRATEGIES = Path('/placeholder/strategies')


def _attach_stub_manifest(
    sequencer: StartupSequencer,
    *,
    account_id: str = 'test_acct',
    allocated_capital: Decimal = Decimal('50000'),
    capital_pool: Decimal | None = None,
) -> MagicMock:
    '''Inject a mocked Manifest onto a sequencer to bypass _load_manifest.'''

    manifest = MagicMock()
    manifest.account_id = account_id
    manifest.allocated_capital = allocated_capital
    manifest.capital_pool = capital_pool if capital_pool is not None else allocated_capital
    manifest.strategies = ()
    sequencer._manifest = manifest
    return manifest


def _make_sequencer(
    state_store: StateStore | None = None,
    manifest_path: Path | None = None,
    strategies_base_path: Path | None = None,
    strategy_state_path: Path | None = None,
) -> StartupSequencer:
    return StartupSequencer(
        state_store=state_store or _make_mock_state_store(),
        manifest_path=manifest_path or _PLACEHOLDER_MANIFEST,
        strategies_base_path=strategies_base_path or _PLACEHOLDER_STRATEGIES,
        strategy_state_path=strategy_state_path,
    )


class TestStartupSequencerConstruction:

    def test_valid_construction(self) -> None:
        sequencer = _make_sequencer()

        assert sequencer is not None

    def test_instance_state_none_before_recover(self) -> None:
        sequencer = _make_sequencer()

        assert sequencer.instance_state is None

    def test_manifest_none_before_load(self) -> None:
        sequencer = _make_sequencer()

        assert sequencer.manifest is None

    def test_instance_state_returns_live_object(self) -> None:
        '''instance_state returns the live (mutable) state, not a copy.'''

        sequencer = _make_sequencer()
        manifest = _attach_stub_manifest(sequencer)
        sequencer._state_store.recover.return_value = None
        sequencer._recover_state()

        state = sequencer.instance_state
        assert state is not None
        # Same identity across calls — confirms live object exposure
        assert sequencer.instance_state is state
        # Mutations to the returned object are visible on subsequent reads
        state.capital.position_notional = state.capital.position_notional + 1
        assert sequencer.instance_state.capital.position_notional == state.capital.position_notional
        # Manifest is exposed
        assert sequencer.manifest is manifest

    def test_invalid_state_store_rejected(self) -> None:
        with pytest.raises(ValueError, match='must be a StateStore'):
            StartupSequencer(
                state_store='not a state store',  # type: ignore[arg-type]
                manifest_path=_PLACEHOLDER_MANIFEST,
                strategies_base_path=_PLACEHOLDER_STRATEGIES,
            )

    def test_invalid_manifest_path_rejected(self) -> None:
        with pytest.raises(ValueError, match='must be a Path'):
            StartupSequencer(
                state_store=_make_mock_state_store(),
                manifest_path='/placeholder/manifest.yaml',  # type: ignore[arg-type]
                strategies_base_path=_PLACEHOLDER_STRATEGIES,
            )

    def test_invalid_strategies_base_path_rejected(self) -> None:
        with pytest.raises(ValueError, match='must be a Path'):
            StartupSequencer(
                state_store=_make_mock_state_store(),
                manifest_path=_PLACEHOLDER_MANIFEST,
                strategies_base_path='/placeholder/strategies',  # type: ignore[arg-type]
            )


class TestStartupSequencerStart:

    def test_start_returns_runner(self, tmp_path: Path) -> None:
        manifest_path = tmp_path / 'manifest.yaml'
        strategy_file = tmp_path / 'strat.py'
        strategy_file.write_text(VALID_STRATEGY)
        manifest_path.write_text(
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - id: test_strat\n'
            '    file: strat.py\n'
            f'{_sensors_yaml(tmp_path)}'
            '    capital_pct: 50\n'
        )
        state_store = _make_mock_state_store()
        state_store.recover.return_value = None
        sequencer = _make_sequencer(
            state_store=state_store,
            manifest_path=manifest_path,
            strategies_base_path=tmp_path,
        )

        runner = sequencer.start()

        assert runner is not None

    def test_start_raises_startup_error_on_step_failure(self) -> None:
        state_store = _make_mock_state_store()
        state_store.recover.side_effect = RuntimeError('disk error')
        sequencer = _make_sequencer(state_store=state_store)

        with pytest.raises(StartupError):
            sequencer.start()

    def test_start_loads_manifest_before_instantiation(self, tmp_path: Path) -> None:
        manifest_path = tmp_path / 'manifest.yaml'
        strategy_file = tmp_path / 'strat.py'
        strategy_file.write_text(VALID_STRATEGY)
        manifest_path.write_text(
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - id: test_strat\n'
            '    file: strat.py\n'
            f'{_sensors_yaml(tmp_path)}'
            '    capital_pct: 50\n'
        )
        state_store = _make_mock_state_store()
        state_store.recover.return_value = None
        sequencer = _make_sequencer(
            state_store=state_store,
            manifest_path=manifest_path,
            strategies_base_path=tmp_path,
        )

        sequencer.start()

        assert sequencer._manifest is not None
        assert sequencer._runner is not None

    def test_start_runner_ready_for_dispatch(self, tmp_path: Path) -> None:
        manifest_path = tmp_path / 'manifest.yaml'
        strategy_file = tmp_path / 'strat.py'
        strategy_file.write_text(VALID_STRATEGY)
        manifest_path.write_text(
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - id: test_strat\n'
            '    file: strat.py\n'
            f'{_sensors_yaml(tmp_path)}'
            '    capital_pct: 50\n'
        )
        state_store = _make_mock_state_store()
        state_store.recover.return_value = None
        sequencer = _make_sequencer(
            state_store=state_store,
            manifest_path=manifest_path,
            strategies_base_path=tmp_path,
        )

        runner = sequencer.start()

        assert isinstance(runner, StrategyRunner)


class TestStateRecovery:

    def test_recover_state_calls_state_store_recover(self) -> None:
        mock_store = _make_mock_state_store()
        mock_store.recover.return_value = None
        sequencer = _make_sequencer(state_store=mock_store)
        _attach_stub_manifest(sequencer)

        sequencer._recover_state()

        mock_store.recover.assert_called_once()

    def test_recover_state_stores_result(self) -> None:
        mock_store = _make_mock_state_store()
        mock_state = MagicMock()
        mock_store.recover.return_value = mock_state
        sequencer = _make_sequencer(state_store=mock_store)
        _attach_stub_manifest(sequencer)

        sequencer._recover_state()

        assert sequencer._state is mock_state

    def test_recover_state_creates_initial_state_on_fresh_start(self) -> None:
        mock_store = _make_mock_state_store()
        mock_store.recover.return_value = None
        sequencer = _make_sequencer(
            state_store=mock_store,
        )
        _attach_stub_manifest(sequencer, allocated_capital=Decimal('50000'))

        sequencer._recover_state()

        assert sequencer._state is not None
        assert isinstance(sequencer._state, InstanceState)
        assert sequencer._state.capital.capital_pool == Decimal('50000')

    def test_recover_state_wraps_exception_in_startup_error(self) -> None:
        mock_store = _make_mock_state_store()
        mock_store.recover.side_effect = RuntimeError('disk error')
        sequencer = _make_sequencer(state_store=mock_store)
        _attach_stub_manifest(sequencer)

        with pytest.raises(StartupError, match='recover_state') as exc_info:
            sequencer._recover_state()

        assert 'disk error' in exc_info.value.reason


class TestExternalIntegrationStubs:

    def test_register_with_trading_does_not_raise(self) -> None:
        sequencer = _make_sequencer()

        sequencer._register_with_trading()

    def test_reconcile_capital_does_not_raise(self) -> None:
        sequencer = _make_sequencer()

        sequencer._reconcile_capital()

    def test_reconcile_capital_imports_praxis_only_position(self) -> None:
        '''A Praxis position Nexus does not know about is imported into InstanceState.'''

        praxis_pos = MagicMock()
        praxis_pos.account_id = 'acc_001'
        praxis_pos.trade_id = 'trade_xyz'
        praxis_pos.symbol = 'BTCUSDT'
        praxis_pos.side = OrderSide.BUY
        praxis_pos.qty = Decimal('0.5')
        praxis_pos.avg_entry_price = Decimal('50000')
        praxis_pos.strategy_id = 'momentum'

        outbound = MagicMock()
        outbound.pull_positions.return_value = {
            ('acc_001', 'trade_xyz'): praxis_pos,
        }

        sequencer = _make_sequencer()
        sequencer._praxis_outbound = outbound
        _attach_stub_manifest(sequencer, account_id='acc_001')
        sequencer._state = InstanceState(
            capital=CapitalState(capital_pool=Decimal('10000')),
        )

        sequencer._reconcile_capital()

        imported = sequencer._state.positions.get('trade_xyz')
        assert imported is not None
        assert imported.strategy_id == 'momentum'
        assert imported.symbol == 'BTCUSDT'
        assert imported.side == OrderSide.BUY
        assert imported.size == Decimal('0.5')
        assert imported.entry_price == Decimal('50000')
        assert sequencer._state.capital.position_notional == Decimal('25000')

    def test_reconcile_capital_skips_praxis_position_without_strategy_id(self) -> None:
        '''Praxis positions lacking strategy_id are not imported.'''

        praxis_pos = MagicMock()
        praxis_pos.account_id = 'acc_001'
        praxis_pos.trade_id = 'trade_xyz'
        praxis_pos.symbol = 'BTCUSDT'
        praxis_pos.side = OrderSide.BUY
        praxis_pos.qty = Decimal('0.5')
        praxis_pos.avg_entry_price = Decimal('50000')
        praxis_pos.strategy_id = None

        outbound = MagicMock()
        outbound.pull_positions.return_value = {
            ('acc_001', 'trade_xyz'): praxis_pos,
        }

        sequencer = _make_sequencer()
        sequencer._praxis_outbound = outbound
        _attach_stub_manifest(sequencer, account_id='acc_001')
        sequencer._state = InstanceState(
            capital=CapitalState(capital_pool=Decimal('10000')),
        )

        sequencer._reconcile_capital()

        assert 'trade_xyz' not in sequencer._state.positions

    def test_restore_strategy_state_without_path_logs_warning(self) -> None:
        sequencer = _make_sequencer()
        sequencer._runner = MagicMock()
        sequencer._manifest = MagicMock()

        sequencer._restore_strategy_state()

    def test_replay_strategy_events_fails_without_runner(self) -> None:
        sequencer = _make_sequencer()

        with pytest.raises(StartupError, match='replay_strategy_events'):
            sequencer._replay_strategy_events()

    def test_replay_strategy_events_fails_without_manifest(self) -> None:
        sequencer = _make_sequencer()
        sequencer._runner = MagicMock()

        with pytest.raises(StartupError, match='replay_strategy_events'):
            sequencer._replay_strategy_events()

    def test_replay_strategy_events_wraps_read_events_failure(self) -> None:
        mock_store = _make_mock_state_store()
        mock_store.read_events.side_effect = RuntimeError('WAL corrupted')
        sequencer = _make_sequencer(state_store=mock_store)
        sequencer._runner = MagicMock()
        sequencer._manifest = MagicMock()

        with pytest.raises(StartupError, match='replay_strategy_events'):
            sequencer._replay_strategy_events()

    def test_wire_sensors_without_manifest_raises(self) -> None:
        sequencer = _make_sequencer()

        with pytest.raises(StartupError, match='manifest not loaded'):
            sequencer._wire_sensors()

    def test_register_timers_without_manifest_raises(self) -> None:
        sequencer = _make_sequencer()

        with pytest.raises(StartupError, match='manifest not loaded'):
            sequencer._register_timers()

    def test_determine_mode_sets_active(self) -> None:
        sequencer = _make_sequencer()

        sequencer._determine_mode()

        assert sequencer._mode == OperationalMode.ACTIVE


class TestStrategyStateRestoration:

    def test_restore_loads_existing_state_file(self, tmp_path: Path) -> None:
        manifest_path = tmp_path / 'manifest.yaml'
        strategy_file = tmp_path / 'strat.py'
        strategy_state_path = tmp_path / 'strategy_state'
        strategy_state_path.mkdir()
        (strategy_state_path / 'test_strat.bin').write_bytes(b'saved_data')

        strategy_file.write_text(VALID_STRATEGY)
        manifest_path.write_text(
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - id: test_strat\n'
            '    file: strat.py\n'
            f'{_sensors_yaml(tmp_path)}'
            '    capital_pct: 50\n'
        )
        state_store = _make_mock_state_store()
        state_store.recover.return_value = None
        sequencer = _make_sequencer(
            state_store=state_store,
            manifest_path=manifest_path,
            strategies_base_path=tmp_path,
            strategy_state_path=strategy_state_path,
        )
        sequencer._load_manifest()
        sequencer._recover_state()
        sequencer._instantiate_strategies()

        sequencer._restore_strategy_state()

    def test_restore_uses_empty_bytes_when_file_missing(self, tmp_path: Path) -> None:
        manifest_path = tmp_path / 'manifest.yaml'
        strategy_file = tmp_path / 'strat.py'
        strategy_state_path = tmp_path / 'strategy_state'
        strategy_state_path.mkdir()

        strategy_file.write_text(VALID_STRATEGY)
        manifest_path.write_text(
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - id: test_strat\n'
            '    file: strat.py\n'
            f'{_sensors_yaml(tmp_path)}'
            '    capital_pct: 50\n'
        )
        state_store = _make_mock_state_store()
        state_store.recover.return_value = None
        sequencer = _make_sequencer(
            state_store=state_store,
            manifest_path=manifest_path,
            strategies_base_path=tmp_path,
            strategy_state_path=strategy_state_path,
        )
        sequencer._load_manifest()
        sequencer._recover_state()
        sequencer._instantiate_strategies()

        sequencer._restore_strategy_state()

    def test_restore_fails_without_runner(self) -> None:
        sequencer = _make_sequencer(strategy_state_path=Path('/placeholder/state'))

        with pytest.raises(StartupError, match='restore_strategy_state'):
            sequencer._restore_strategy_state()

    def test_restore_fails_without_manifest(self) -> None:
        sequencer = _make_sequencer(strategy_state_path=Path('/placeholder/state'))
        sequencer._runner = MagicMock()

        with pytest.raises(StartupError, match='restore_strategy_state'):
            sequencer._restore_strategy_state()

    def test_restore_skips_unsafe_strategy_id_with_path_separator(self, tmp_path: Path) -> None:
        manifest_path = tmp_path / 'manifest.yaml'
        strategy_file = tmp_path / 'strat.py'
        strategy_state_path = tmp_path / 'strategy_state'
        strategy_state_path.mkdir()

        strategy_file.write_text(VALID_STRATEGY)
        manifest_path.write_text(
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - id: ../evil\n'
            '    file: strat.py\n'
            f'{_sensors_yaml(tmp_path)}'
            '    capital_pct: 50\n'
        )
        state_store = _make_mock_state_store()
        state_store.recover.return_value = None
        sequencer = _make_sequencer(
            state_store=state_store,
            manifest_path=manifest_path,
            strategies_base_path=tmp_path,
            strategy_state_path=strategy_state_path,
        )
        sequencer._load_manifest()
        sequencer._recover_state()
        sequencer._runner = MagicMock()

        sequencer._restore_strategy_state()

        sequencer._runner.dispatch_load.assert_not_called()


class TestEventReplay:

    def test_replay_with_no_events_succeeds(self, tmp_path: Path) -> None:
        manifest_path = tmp_path / 'manifest.yaml'
        strategy_file = tmp_path / 'strat.py'
        strategy_file.write_text(VALID_STRATEGY)
        manifest_path.write_text(
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - id: test_strat\n'
            '    file: strat.py\n'
            f'{_sensors_yaml(tmp_path)}'
            '    capital_pct: 50\n'
        )
        state_store = _make_mock_state_store()
        state_store.recover.return_value = None
        state_store.read_events.return_value = []
        sequencer = _make_sequencer(
            state_store=state_store,
            manifest_path=manifest_path,
            strategies_base_path=tmp_path,
        )
        sequencer._load_manifest()
        sequencer._recover_state()
        sequencer._instantiate_strategies()

        sequencer._replay_strategy_events()

    def test_replay_dispatches_events_to_runner(self, tmp_path: Path) -> None:
        manifest_path = tmp_path / 'manifest.yaml'
        strategy_file = tmp_path / 'strat.py'
        strategy_file.write_text(VALID_STRATEGY)
        manifest_path.write_text(
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - id: test_strat\n'
            '    file: strat.py\n'
            f'{_sensors_yaml(tmp_path)}'
            '    capital_pct: 50\n'
        )
        state_store = _make_mock_state_store()
        state_store.recover.return_value = None
        event = StrategyEvent(
            strategy_id='test_strat',
            event_type='trade_outcome',
            realized_pnl=Decimal('-50'),
            timestamp=datetime.now(tz=timezone.utc),
        )
        state_store.read_events.return_value = [event]
        sequencer = _make_sequencer(
            state_store=state_store,
            manifest_path=manifest_path,
            strategies_base_path=tmp_path,
        )
        sequencer._load_manifest()
        sequencer._recover_state()
        sequencer._instantiate_strategies()
        sequencer._runner.dispatch_event_replay = MagicMock()

        sequencer._replay_strategy_events()

        sequencer._runner.dispatch_event_replay.assert_called_once_with('test_strat', event)

    def test_replay_skips_unknown_strategy(self, tmp_path: Path) -> None:
        manifest_path = tmp_path / 'manifest.yaml'
        strategy_file = tmp_path / 'strat.py'
        strategy_file.write_text(VALID_STRATEGY)
        manifest_path.write_text(
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - id: test_strat\n'
            '    file: strat.py\n'
            f'{_sensors_yaml(tmp_path)}'
            '    capital_pct: 50\n'
        )
        state_store = _make_mock_state_store()
        state_store.recover.return_value = None
        event = StrategyEvent(
            strategy_id='unknown_strat',
            event_type='trade_outcome',
            realized_pnl=Decimal('-50'),
            timestamp=datetime.now(tz=timezone.utc),
        )
        state_store.read_events.return_value = [event]
        sequencer = _make_sequencer(
            state_store=state_store,
            manifest_path=manifest_path,
            strategies_base_path=tmp_path,
        )
        sequencer._load_manifest()
        sequencer._recover_state()
        sequencer._instantiate_strategies()
        sequencer._runner.dispatch_event_replay = MagicMock()

        sequencer._replay_strategy_events()

        sequencer._runner.dispatch_event_replay.assert_not_called()


class TestStartupDispatch:

    def test_dispatch_startup_invokes_runner_dispatch(self, tmp_path: Path) -> None:
        manifest_path = tmp_path / 'manifest.yaml'
        strategy_file = tmp_path / 'strat.py'
        strategy_file.write_text(VALID_STRATEGY)
        manifest_path.write_text(
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - id: test_strat\n'
            '    file: strat.py\n'
            f'{_sensors_yaml(tmp_path)}'
            '    capital_pct: 50\n'
        )
        sequencer = _make_sequencer(
            manifest_path=manifest_path,
            strategies_base_path=tmp_path,
        )
        sequencer._load_manifest()
        sequencer._instantiate_strategies()
        sequencer._determine_mode()
        sequencer._runner.dispatch_startup = MagicMock()

        sequencer._dispatch_startup()

        sequencer._runner.dispatch_startup.assert_called_once()
        call_args = sequencer._runner.dispatch_startup.call_args
        assert call_args[0][0] == 'test_strat'

    def test_dispatch_startup_fails_without_runner(self) -> None:
        sequencer = _make_sequencer()

        with pytest.raises(StartupError, match='dispatch_startup') as exc_info:
            sequencer._dispatch_startup()

        assert 'runner not initialized' in exc_info.value.reason

    def test_dispatch_startup_fails_without_manifest(self) -> None:
        sequencer = _make_sequencer()
        sequencer._runner = MagicMock()

        with pytest.raises(StartupError, match='dispatch_startup') as exc_info:
            sequencer._dispatch_startup()

        assert 'manifest not loaded' in exc_info.value.reason

    def test_dispatch_startup_fails_without_mode(self, tmp_path: Path) -> None:
        manifest_path = tmp_path / 'manifest.yaml'
        strategy_file = tmp_path / 'strat.py'
        strategy_file.write_text(VALID_STRATEGY)
        manifest_path.write_text(
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - id: test_strat\n'
            '    file: strat.py\n'
            f'{_sensors_yaml(tmp_path)}'
            '    capital_pct: 50\n'
        )
        sequencer = _make_sequencer(
            manifest_path=manifest_path,
            strategies_base_path=tmp_path,
        )
        sequencer._load_manifest()
        sequencer._instantiate_strategies()

        with pytest.raises(StartupError, match='dispatch_startup') as exc_info:
            sequencer._dispatch_startup()

        assert 'mode not determined' in exc_info.value.reason

    def test_dispatch_startup_wraps_strategy_exception(self, tmp_path: Path) -> None:
        manifest_path = tmp_path / 'manifest.yaml'
        strategy_file = tmp_path / 'strat.py'
        strategy_file.write_text(VALID_STRATEGY)
        manifest_path.write_text(
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - id: test_strat\n'
            '    file: strat.py\n'
            f'{_sensors_yaml(tmp_path)}'
            '    capital_pct: 50\n'
        )
        sequencer = _make_sequencer(
            manifest_path=manifest_path,
            strategies_base_path=tmp_path,
        )
        sequencer._load_manifest()
        sequencer._instantiate_strategies()
        sequencer._determine_mode()
        sequencer._runner.dispatch_startup = MagicMock(side_effect=RuntimeError('callback failed'))

        with pytest.raises(StartupError, match='dispatch_startup') as exc_info:
            sequencer._dispatch_startup()

        assert 'test_strat' in exc_info.value.reason
        assert 'callback failed' in exc_info.value.reason


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
            'account_id: test_acct\n'
            'allocated_capital: 5000\n'
            'capital_pool: 5000\n'
            'strategies:\n'
            '  - id: test_strat\n'
            '    file: strat.py\n'
            f'{_sensors_yaml(tmp_path)}'
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

    def test_load_manifest_wraps_exception_in_startup_error(self, tmp_path: Path) -> None:
        sequencer = _make_sequencer(manifest_path=tmp_path / 'nonexistent_manifest.yaml')

        with pytest.raises(StartupError, match='load_manifest') as exc_info:
            sequencer._load_manifest()

        assert 'not found' in exc_info.value.reason.lower()


class TestStrategyInstantiation:

    def test_instantiate_strategies_creates_runner(self, tmp_path: Path) -> None:
        manifest_path = tmp_path / 'manifest.yaml'
        strategy_file = tmp_path / 'strat.py'
        strategy_file.write_text(VALID_STRATEGY)
        manifest_path.write_text(
            'account_id: test_acct\n'
            'allocated_capital: 5000\n'
            'capital_pool: 5000\n'
            'strategies:\n'
            '  - id: test_strat\n'
            '    file: strat.py\n'
            f'{_sensors_yaml(tmp_path)}'
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
            'account_id: test_acct\n'
            'allocated_capital: 5000\n'
            'capital_pool: 5000\n'
            'strategies:\n'
            '  - id: test_strat\n'
            '    file: bad_import.py\n'
            f'{_sensors_yaml(tmp_path)}'
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


class TestCrashOnlyDesign:

    def test_fresh_start_always_calls_recover(self, tmp_path: Path) -> None:
        manifest_path = tmp_path / 'manifest.yaml'
        strategy_file = tmp_path / 'strat.py'
        strategy_file.write_text(VALID_STRATEGY)
        manifest_path.write_text(
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - id: test_strat\n'
            '    file: strat.py\n'
            f'{_sensors_yaml(tmp_path)}'
            '    capital_pct: 50\n'
        )
        state_store = _make_mock_state_store()
        state_store.recover.return_value = None
        state_store.read_events.return_value = []
        sequencer = _make_sequencer(
            state_store=state_store,
            manifest_path=manifest_path,
            strategies_base_path=tmp_path,
        )

        sequencer.start()

        state_store.recover.assert_called_once()

    def test_crash_recovery_calls_dispatch_load_with_file_contents(self, tmp_path: Path) -> None:
        manifest_path = tmp_path / 'manifest.yaml'
        strategy_file = tmp_path / 'strat.py'
        strategy_state_path = tmp_path / 'strategy_state'
        strategy_state_path.mkdir()
        (strategy_state_path / 'test_strat.bin').write_bytes(b'recovered_state_data')

        strategy_file.write_text(VALID_STRATEGY)
        manifest_path.write_text(
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - id: test_strat\n'
            '    file: strat.py\n'
            f'{_sensors_yaml(tmp_path)}'
            '    capital_pct: 50\n'
        )
        state_store = _make_mock_state_store()
        state_store.recover.return_value = None
        state_store.read_events.return_value = []
        sequencer = _make_sequencer(
            state_store=state_store,
            manifest_path=manifest_path,
            strategies_base_path=tmp_path,
            strategy_state_path=strategy_state_path,
        )
        sequencer._load_manifest()
        sequencer._recover_state()
        sequencer._instantiate_strategies()
        sequencer._runner.dispatch_load = MagicMock()

        sequencer._restore_strategy_state()

        sequencer._runner.dispatch_load.assert_called_once_with(
            'test_strat', b'recovered_state_data'
        )

    def test_fresh_start_calls_dispatch_load_with_empty_bytes(self, tmp_path: Path) -> None:
        manifest_path = tmp_path / 'manifest.yaml'
        strategy_file = tmp_path / 'strat.py'
        strategy_state_path = tmp_path / 'strategy_state'
        strategy_state_path.mkdir()

        strategy_file.write_text(VALID_STRATEGY)
        manifest_path.write_text(
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - id: test_strat\n'
            '    file: strat.py\n'
            f'{_sensors_yaml(tmp_path)}'
            '    capital_pct: 50\n'
        )
        state_store = _make_mock_state_store()
        state_store.recover.return_value = None
        state_store.read_events.return_value = []
        sequencer = _make_sequencer(
            state_store=state_store,
            manifest_path=manifest_path,
            strategies_base_path=tmp_path,
            strategy_state_path=strategy_state_path,
        )
        sequencer._load_manifest()
        sequencer._recover_state()
        sequencer._instantiate_strategies()
        sequencer._runner.dispatch_load = MagicMock()

        sequencer._restore_strategy_state()

        sequencer._runner.dispatch_load.assert_called_once_with('test_strat', b'')

    def test_event_replay_calls_dispatch_event_replay(self, tmp_path: Path) -> None:
        manifest_path = tmp_path / 'manifest.yaml'
        strategy_file = tmp_path / 'strat.py'

        strategy_file.write_text(VALID_STRATEGY)
        manifest_path.write_text(
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - id: test_strat\n'
            '    file: strat.py\n'
            f'{_sensors_yaml(tmp_path)}'
            '    capital_pct: 50\n'
        )
        event = StrategyEvent(
            strategy_id='test_strat',
            event_type='trade_outcome',
            realized_pnl=Decimal('-100'),
            timestamp=datetime.now(tz=timezone.utc),
        )
        state_store = _make_mock_state_store()
        state_store.recover.return_value = None
        state_store.read_events.return_value = [event]
        sequencer = _make_sequencer(
            state_store=state_store,
            manifest_path=manifest_path,
            strategies_base_path=tmp_path,
        )
        sequencer._load_manifest()
        sequencer._recover_state()
        sequencer._instantiate_strategies()
        sequencer._runner.dispatch_event_replay = MagicMock()

        sequencer._replay_strategy_events()

        sequencer._runner.dispatch_event_replay.assert_called_once_with('test_strat', event)

    def test_same_code_path_for_fresh_and_crash(self, tmp_path: Path) -> None:
        manifest_path = tmp_path / 'manifest.yaml'
        strategy_file = tmp_path / 'strat.py'
        strategy_state_path = tmp_path / 'strategy_state'
        strategy_state_path.mkdir()

        strategy_file.write_text(VALID_STRATEGY)
        manifest_path.write_text(
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - id: test_strat\n'
            '    file: strat.py\n'
            f'{_sensors_yaml(tmp_path)}'
            '    capital_pct: 50\n'
        )

        state_store_fresh = _make_mock_state_store()
        state_store_fresh.recover.return_value = None
        state_store_fresh.read_events.return_value = []

        sequencer_fresh = _make_sequencer(
            state_store=state_store_fresh,
            manifest_path=manifest_path,
            strategies_base_path=tmp_path,
            strategy_state_path=strategy_state_path,
        )
        runner_fresh = sequencer_fresh.start()

        (strategy_state_path / 'test_strat.bin').write_bytes(b'crash_state')
        state_store_crash = _make_mock_state_store()
        state_store_crash.recover.return_value = None
        state_store_crash.read_events.return_value = []

        sequencer_crash = _make_sequencer(
            state_store=state_store_crash,
            manifest_path=manifest_path,
            strategies_base_path=tmp_path,
            strategy_state_path=strategy_state_path,
        )
        runner_crash = sequencer_crash.start()

        state_store_fresh.recover.assert_called_once()
        state_store_crash.recover.assert_called_once()
        assert runner_fresh is not None
        assert runner_crash is not None


class TestPendingStartupActions:
    '''PT-FIX-16: on_startup actions are buffered and drained via submitter.

    The runtime submitter depends on `instance_state` (capital
    controller / validator / praxis_outbound) which only exists after
    `start()` runs, so `_dispatch_startup` cannot call the submitter
    inline. Actions returned by `Strategy.on_startup` are stashed in
    `_pending_startup_actions` and forwarded by the launcher via
    `drain_pending_startup_actions(submitter)` once wiring completes.
    '''

    def test_dispatch_startup_buffers_actions_without_submitter(
        self, tmp_path: Path,
    ) -> None:
        from unittest.mock import MagicMock
        from nexus.strategy.action import Action, ActionType
        from nexus.core.domain.enums import OrderSide

        manifest_path = tmp_path / 'manifest.yaml'
        strategy_file = tmp_path / 'strat.py'
        strategy_file.write_text(VALID_STRATEGY)
        manifest_path.write_text(
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - id: test_strat\n'
            '    file: strat.py\n'
            f'{_sensors_yaml(tmp_path)}'
            '    capital_pct: 50\n'
        )
        sequencer = _make_sequencer(
            manifest_path=manifest_path,
            strategies_base_path=tmp_path,
        )
        sequencer._load_manifest()
        sequencer._instantiate_strategies()
        sequencer._determine_mode()

        action = Action(action_type=ActionType.EXIT, trade_id='trade_existing', size=Decimal('1'))
        sequencer._runner.dispatch_startup = MagicMock(return_value=[action])

        sequencer._dispatch_startup()

        assert sequencer._pending_startup_actions == {'test_strat': [action]}

    def test_dispatch_startup_invokes_submitter_when_wired(
        self, tmp_path: Path,
    ) -> None:
        from unittest.mock import MagicMock
        from nexus.strategy.action import Action, ActionType

        manifest_path = tmp_path / 'manifest.yaml'
        strategy_file = tmp_path / 'strat.py'
        strategy_file.write_text(VALID_STRATEGY)
        manifest_path.write_text(
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - id: test_strat\n'
            '    file: strat.py\n'
            f'{_sensors_yaml(tmp_path)}'
            '    capital_pct: 50\n'
        )

        submitter = MagicMock()
        sequencer = StartupSequencer(
            state_store=_make_mock_state_store(),
            manifest_path=manifest_path,
            strategies_base_path=tmp_path,
            action_submit=submitter,
        )
        sequencer._load_manifest()
        sequencer._instantiate_strategies()
        sequencer._determine_mode()

        action = Action(action_type=ActionType.EXIT, trade_id='trade_existing', size=Decimal('1'))
        sequencer._runner.dispatch_startup = MagicMock(return_value=[action])

        sequencer._dispatch_startup()

        submitter.assert_called_once_with([action], 'test_strat')
        assert sequencer._pending_startup_actions == {}

    def test_drain_pending_forwards_buffered_actions(
        self, tmp_path: Path,
    ) -> None:
        from unittest.mock import MagicMock
        from nexus.strategy.action import Action, ActionType

        sequencer = _make_sequencer()
        action_a = Action(action_type=ActionType.EXIT, trade_id='trade_a', size=Decimal('1'))
        action_b = Action(action_type=ActionType.EXIT, trade_id='trade_b', size=Decimal('1'))
        sequencer._pending_startup_actions = {
            'strat_a': [action_a],
            'strat_b': [action_b],
        }

        submitter = MagicMock()
        sequencer.drain_pending_startup_actions(submitter)

        assert submitter.call_count == 2
        submitter.assert_any_call([action_a], 'strat_a')
        submitter.assert_any_call([action_b], 'strat_b')
        assert sequencer._pending_startup_actions == {}

    def test_drain_pending_is_idempotent(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock
        from nexus.strategy.action import Action, ActionType

        sequencer = _make_sequencer()
        sequencer._pending_startup_actions = {
            'strat_a': [Action(action_type=ActionType.EXIT, trade_id='trade_a', size=Decimal('1'))],
        }

        submitter = MagicMock()
        sequencer.drain_pending_startup_actions(submitter)
        sequencer.drain_pending_startup_actions(submitter)

        assert submitter.call_count == 1

    def test_drain_pending_swallows_per_strategy_submitter_exceptions(
        self, tmp_path: Path,
    ) -> None:
        from unittest.mock import MagicMock
        from nexus.strategy.action import Action, ActionType

        sequencer = _make_sequencer()
        action_a = Action(action_type=ActionType.EXIT, trade_id='trade_a', size=Decimal('1'))
        action_b = Action(action_type=ActionType.EXIT, trade_id='trade_b', size=Decimal('1'))
        sequencer._pending_startup_actions = {
            'strat_bad': [action_a],
            'strat_ok': [action_b],
        }

        calls: list[tuple[str, list[Action]]] = []

        def submitter(actions: list[Action], strategy_id: str) -> None:
            calls.append((strategy_id, actions))
            if strategy_id == 'strat_bad':
                raise RuntimeError('submit_failed')

        sequencer.drain_pending_startup_actions(submitter)

        seen = {strategy_id for strategy_id, _ in calls}
        assert seen == {'strat_bad', 'strat_ok'}
        assert sequencer._pending_startup_actions == {}
