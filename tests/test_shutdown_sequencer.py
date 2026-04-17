'''Tests for ShutdownSequencer.'''

from __future__ import annotations

import queue
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nexus.core.domain.capital_state import CapitalState
from nexus.core.domain.instance_state import InstanceState
from nexus.infrastructure.manifest import Manifest, SensorSpec, StrategySpec
from nexus.infrastructure.praxis_connector.praxis_inbound import PraxisInbound
from nexus.infrastructure.praxis_connector.trade_outcome import TradeOutcome
from nexus.infrastructure.praxis_connector.trade_outcome_type import TradeOutcomeType
from nexus.infrastructure.state_store import StateStore
from nexus.startup.shutdown_sequencer import ShutdownSequencer
from nexus.core.domain.enums import OrderSide
from nexus.strategy.action import Action, ActionType
from nexus.strategy.runner import StrategyRunner

_PLACEHOLDER_PATH = Path('/placeholder/strategy_state')
_EXP_DIR_HANDLE = tempfile.TemporaryDirectory()
_EXP_DIR = Path(_EXP_DIR_HANDLE.name)


def _make_mock_runner() -> MagicMock:
    return MagicMock(spec=StrategyRunner)


def _make_mock_state_store() -> MagicMock:
    return MagicMock(spec=StateStore)


def _make_instance_state() -> InstanceState:
    return InstanceState(capital=CapitalState(capital_pool=Decimal('10000')))


def _make_strategy_spec(strategy_id: str = 'test_strategy') -> StrategySpec:
    pfn = SensorSpec(
        experiment_dir=_EXP_DIR,
        permutation_ids=(1,),
        interval_seconds=60,
    )
    return StrategySpec(
        strategy_id=strategy_id,
        file='test.py',
        sensors=(pfn,),
        capital_pct=Decimal('50'),
    )


def _make_manifest(strategies: tuple[StrategySpec, ...] | None = None) -> Manifest:
    return Manifest(
        capital_pool=Decimal('10000'),
        strategies=strategies or (_make_strategy_spec(),),
    )


def _make_sequencer(
    runner: StrategyRunner | None = None,
    manifest: Manifest | None = None,
    state_store: StateStore | None = None,
    state: InstanceState | None = None,
    strategy_state_path: Path | None = None,
) -> ShutdownSequencer:
    return ShutdownSequencer(
        runner=runner or _make_mock_runner(),
        manifest=manifest or _make_manifest(),
        state_store=state_store or _make_mock_state_store(),
        state=state or _make_instance_state(),
        strategy_state_path=strategy_state_path or _PLACEHOLDER_PATH,
    )


class TestShutdownSequencerConstruction:

    def test_valid_construction(self) -> None:
        sequencer = _make_sequencer()
        assert sequencer is not None

    def test_invalid_runner_rejected(self) -> None:
        with pytest.raises(ValueError, match='must be a StrategyRunner'):
            ShutdownSequencer(
                runner='not a runner',  # type: ignore[arg-type]
                manifest=_make_manifest(),
                state_store=_make_mock_state_store(),
                state=_make_instance_state(),
                strategy_state_path=_PLACEHOLDER_PATH,
            )

    def test_invalid_manifest_rejected(self) -> None:
        with pytest.raises(ValueError, match='must be a Manifest'):
            ShutdownSequencer(
                runner=_make_mock_runner(),
                manifest='not a manifest',  # type: ignore[arg-type]
                state_store=_make_mock_state_store(),
                state=_make_instance_state(),
                strategy_state_path=_PLACEHOLDER_PATH,
            )

    def test_invalid_state_store_rejected(self) -> None:
        with pytest.raises(ValueError, match='must be a StateStore'):
            ShutdownSequencer(
                runner=_make_mock_runner(),
                manifest=_make_manifest(),
                state_store='not a state store',  # type: ignore[arg-type]
                state=_make_instance_state(),
                strategy_state_path=_PLACEHOLDER_PATH,
            )

    def test_invalid_state_rejected(self) -> None:
        with pytest.raises(ValueError, match='must be an InstanceState'):
            ShutdownSequencer(
                runner=_make_mock_runner(),
                manifest=_make_manifest(),
                state_store=_make_mock_state_store(),
                state='not a state',  # type: ignore[arg-type]
                strategy_state_path=_PLACEHOLDER_PATH,
            )

    def test_invalid_strategy_state_path_rejected(self) -> None:
        with pytest.raises(ValueError, match='must be a Path'):
            ShutdownSequencer(
                runner=_make_mock_runner(),
                manifest=_make_manifest(),
                state_store=_make_mock_state_store(),
                state=_make_instance_state(),
                strategy_state_path='/placeholder',  # type: ignore[arg-type]
            )


class TestDispatchShutdown:

    def test_dispatch_shutdown_calls_runner(self) -> None:
        runner = _make_mock_runner()
        runner.dispatch_shutdown.return_value = []
        sequencer = _make_sequencer(runner=runner)

        sequencer._dispatch_shutdown()

        runner.dispatch_shutdown.assert_called_once()

    def test_dispatch_shutdown_collects_actions(self) -> None:
        runner = _make_mock_runner()
        action = Action(action_type=ActionType.EXIT, trade_id='t-1', size=Decimal('1'))
        runner.dispatch_shutdown.return_value = [action]
        sequencer = _make_sequencer(runner=runner)

        sequencer._dispatch_shutdown()

        assert 'test_strategy' in sequencer._shutdown_actions
        assert sequencer._shutdown_actions['test_strategy'] == [action]

    def test_dispatch_shutdown_continues_on_exception(self) -> None:
        runner = _make_mock_runner()
        runner.dispatch_shutdown.side_effect = RuntimeError('strategy error')
        sequencer = _make_sequencer(runner=runner)

        sequencer._dispatch_shutdown()

        assert sequencer._shutdown_actions == {}


class TestSubmitActions:

    def test_submit_actions_filters_exit_abort(self) -> None:
        sequencer = _make_sequencer()
        sequencer._shutdown_actions = {
            'test': [
                Action(action_type=ActionType.EXIT, trade_id='t-1', size=Decimal('1')),
                Action(action_type=ActionType.ABORT, command_id='cmd-2'),
                Action(action_type=ActionType.ENTER, direction=OrderSide.BUY, size=Decimal('1'), execution_mode='SingleShot', order_type='Market', deadline=300),
                Action(action_type=ActionType.MODIFY, command_id='cmd-3'),
            ],
        }

        sequencer._submit_actions()

        assert sequencer._submitted_command_ids == []

    def test_submit_actions_skips_empty(self) -> None:
        sequencer = _make_sequencer()
        sequencer._shutdown_actions = {}

        sequencer._submit_actions()

        assert sequencer._submitted_command_ids == []


class TestDispatchSave:

    def test_dispatch_save_calls_runner(self) -> None:
        runner = _make_mock_runner()
        runner.dispatch_save.return_value = b'state'
        sequencer = _make_sequencer(runner=runner)

        sequencer._dispatch_save()

        runner.dispatch_save.assert_called_once_with('test_strategy')

    def test_dispatch_save_collects_blobs(self) -> None:
        runner = _make_mock_runner()
        runner.dispatch_save.return_value = b'saved_state'
        sequencer = _make_sequencer(runner=runner)

        sequencer._dispatch_save()

        assert sequencer._save_blobs['test_strategy'] == b'saved_state'

    def test_dispatch_save_continues_on_exception(self) -> None:
        runner = _make_mock_runner()
        runner.dispatch_save.side_effect = RuntimeError('save error')
        sequencer = _make_sequencer(runner=runner)

        sequencer._dispatch_save()

        assert sequencer._save_blobs['test_strategy'] == b''


class TestPersistStrategyState:

    def test_persist_writes_blobs(self, tmp_path: Path) -> None:
        sequencer = _make_sequencer(strategy_state_path=tmp_path)
        sequencer._save_blobs = {'test_strategy': b'blob_data'}

        sequencer._persist_strategy_state()

        blob_file = tmp_path / 'test_strategy.bin'
        assert blob_file.exists()
        assert blob_file.read_bytes() == b'blob_data'

    def test_persist_skips_empty_blobs(self, tmp_path: Path) -> None:
        sequencer = _make_sequencer(strategy_state_path=tmp_path)
        sequencer._save_blobs = {'test_strategy': b''}

        sequencer._persist_strategy_state()

        blob_file = tmp_path / 'test_strategy.bin'
        assert not blob_file.exists()

    def test_persist_creates_directory(self, tmp_path: Path) -> None:
        nested_path = tmp_path / 'nested' / 'state'
        sequencer = _make_sequencer(strategy_state_path=nested_path)
        sequencer._save_blobs = {'test_strategy': b'data'}

        sequencer._persist_strategy_state()

        assert nested_path.exists()
        assert (nested_path / 'test_strategy.bin').read_bytes() == b'data'


class TestFinalCheckpoint:

    def test_checkpoint_calls_state_store(self) -> None:
        state_store = _make_mock_state_store()
        state = _make_instance_state()
        sequencer = _make_sequencer(state_store=state_store, state=state)

        sequencer._final_checkpoint()

        state_store.checkpoint.assert_called_once_with(state)


class TestShutdownSequence:

    def test_shutdown_runs_all_steps(self, tmp_path: Path) -> None:
        runner = _make_mock_runner()
        runner.dispatch_shutdown.return_value = []
        runner.dispatch_save.return_value = b''
        state_store = _make_mock_state_store()
        sequencer = _make_sequencer(
            runner=runner,
            state_store=state_store,
            strategy_state_path=tmp_path,
        )

        sequencer.shutdown()

        runner.dispatch_shutdown.assert_called_once()
        runner.dispatch_save.assert_called_once()
        state_store.checkpoint.assert_called_once()

    def test_shutdown_is_idempotent(self, tmp_path: Path) -> None:
        runner = _make_mock_runner()
        runner.dispatch_shutdown.return_value = []
        runner.dispatch_save.return_value = b''
        state_store = _make_mock_state_store()
        sequencer = _make_sequencer(
            runner=runner,
            state_store=state_store,
            strategy_state_path=tmp_path,
        )

        sequencer.shutdown()
        sequencer.shutdown()

        assert runner.dispatch_shutdown.call_count == 2
        assert runner.dispatch_save.call_count == 2
        assert state_store.checkpoint.call_count == 2


class TestStopSignals:

    def test_stop_signals_calls_predict_loop_stop(self) -> None:
        '''_stop_signals calls predict_loop.stop().'''

        mock_loop = MagicMock()
        sequencer = ShutdownSequencer(
            runner=_make_mock_runner(),
            manifest=_make_manifest(),
            state_store=_make_mock_state_store(),
            state=_make_instance_state(),
            strategy_state_path=_PLACEHOLDER_PATH,
            predict_loop=mock_loop,
        )

        sequencer._stop_signals()

        mock_loop.stop.assert_called_once()

    def test_stop_signals_without_predict_loop(self) -> None:
        '''_stop_signals completes without error when predict_loop is None.'''

        sequencer = _make_sequencer()
        sequencer._stop_signals()

    def test_shutdown_stops_signals_before_dispatch(self) -> None:
        '''Full shutdown calls predict_loop.stop() before dispatching on_shutdown.'''

        call_order: list[str] = []

        mock_loop = MagicMock()
        mock_loop.stop.side_effect = lambda: call_order.append('stop_signals')

        runner = _make_mock_runner()
        runner.dispatch_shutdown.side_effect = lambda *_a, **_kw: call_order.append('dispatch_shutdown')

        sequencer = ShutdownSequencer(
            runner=runner,
            manifest=_make_manifest(),
            state_store=_make_mock_state_store(),
            state=_make_instance_state(),
            strategy_state_path=_PLACEHOLDER_PATH,
            predict_loop=mock_loop,
        )

        sequencer.shutdown()

        assert call_order.index('stop_signals') < call_order.index('dispatch_shutdown')


class TestWaitTerminal:

    def test_wait_terminal_completes_on_terminal_outcome(self) -> None:
        '''_wait_terminal returns when submitted commands reach terminal state.'''

        q: queue.Queue[TradeOutcome] = queue.Queue()
        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.CANCELED,
            timestamp=datetime.now(tz=timezone.utc),
        )
        q.put(outcome)

        inbound = PraxisInbound(outcome_queue=q, poll_timeout=0.01)

        sequencer = ShutdownSequencer(
            runner=_make_mock_runner(),
            manifest=_make_manifest(),
            state_store=_make_mock_state_store(),
            state=_make_instance_state(),
            strategy_state_path=_PLACEHOLDER_PATH,
            praxis_inbound=inbound,
            shutdown_timeout=5.0,
        )
        sequencer._submitted_command_ids.append('cmd_001')

        sequencer._wait_terminal()

        assert q.empty()

    def test_wait_terminal_times_out_on_missing_outcome(self) -> None:
        '''_wait_terminal logs warning when timeout expires with pending commands.'''

        q: queue.Queue[TradeOutcome] = queue.Queue()
        inbound = PraxisInbound(outcome_queue=q, poll_timeout=0.01)

        sequencer = ShutdownSequencer(
            runner=_make_mock_runner(),
            manifest=_make_manifest(),
            state_store=_make_mock_state_store(),
            state=_make_instance_state(),
            strategy_state_path=_PLACEHOLDER_PATH,
            praxis_inbound=inbound,
            shutdown_timeout=0.1,
        )
        sequencer._submitted_command_ids.append('cmd_never')

        sequencer._wait_terminal()

    def test_wait_terminal_without_inbound_skips(self) -> None:
        '''_wait_terminal skips when praxis_inbound is not configured.'''

        sequencer = _make_sequencer()
        sequencer._submitted_command_ids.append('cmd_001')

        sequencer._wait_terminal()

    def test_wait_terminal_ignores_non_terminal_outcomes(self) -> None:
        '''_wait_terminal ignores ACK/PARTIAL outcomes.'''

        q: queue.Queue[TradeOutcome] = queue.Queue()
        ack = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.ACK,
            timestamp=datetime.now(tz=timezone.utc),
        )
        filled = TradeOutcome(
            outcome_id='out_002',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.CANCELED,
            timestamp=datetime.now(tz=timezone.utc),
        )
        q.put(ack)
        q.put(filled)

        inbound = PraxisInbound(outcome_queue=q, poll_timeout=0.01)

        sequencer = ShutdownSequencer(
            runner=_make_mock_runner(),
            manifest=_make_manifest(),
            state_store=_make_mock_state_store(),
            state=_make_instance_state(),
            strategy_state_path=_PLACEHOLDER_PATH,
            praxis_inbound=inbound,
            shutdown_timeout=5.0,
        )
        sequencer._submitted_command_ids.append('cmd_001')

        sequencer._wait_terminal()

        assert q.empty()


class TestStopTimers:

    def test_stop_timers_calls_timer_loop_stop(self) -> None:
        '''_stop_timers calls timer_loop.stop().'''

        mock_loop = MagicMock()
        sequencer = ShutdownSequencer(
            runner=_make_mock_runner(),
            manifest=_make_manifest(),
            state_store=_make_mock_state_store(),
            state=_make_instance_state(),
            strategy_state_path=_PLACEHOLDER_PATH,
            timer_loop=mock_loop,
        )

        sequencer._stop_timers()

        mock_loop.stop.assert_called_once()

    def test_stop_timers_without_timer_loop(self) -> None:
        '''_stop_timers completes without error when timer_loop is None.'''

        sequencer = _make_sequencer()
        sequencer._stop_timers()
