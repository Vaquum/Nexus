'''Tests for ShutdownSequencer.'''

from __future__ import annotations

import queue
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nexus.core.capital_controller.capital_controller import CapitalController
from nexus.core.domain.capital_state import CapitalState
from nexus.core.domain.enums import OperationalMode, OrderSide
from nexus.core.domain.instance_state import InstanceState
from nexus.core.domain.order_types import ExecutionMode, OrderType
from nexus.core.domain.position import Position
from nexus.core.stp_mode import STPMode
from nexus.core.validator.pipeline_models import (
    ValidationAction,
    ValidationRequestContext,
)
from nexus.infrastructure.manifest import Manifest, SensorSpec, StrategySpec
from nexus.infrastructure.praxis_connector.outcome_processor import OutcomeProcessor
from nexus.infrastructure.praxis_connector.praxis_inbound import PraxisInbound
from nexus.infrastructure.praxis_connector.trade_outcome import TradeOutcome
from nexus.infrastructure.praxis_connector.trade_outcome_type import TradeOutcomeType
from nexus.infrastructure.state_store import StateStore
from nexus.instance_config import InstanceConfig
from nexus.startup.shutdown_sequencer import ShutdownSequencer
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
        account_id='test_acct',
        allocated_capital=Decimal('100000'),
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
                Action(action_type=ActionType.ENTER, direction=OrderSide.BUY, size=Decimal('1'), execution_mode=ExecutionMode.SINGLE_SHOT, order_type=OrderType.MARKET, deadline=300),
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

    def test_submit_actions_routes_exit_and_abort(self) -> None:
        config = InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            stp_mode=STPMode.CANCEL_TAKER,
        )
        state = InstanceState(
            capital=CapitalState(capital_pool=Decimal('10000')),
            positions={
                't-1': Position(
                    trade_id='t-1',
                    strategy_id='test',
                    symbol='BTCUSDT',
                    side=OrderSide.BUY,
                    size=Decimal('0.5'),
                    entry_price=Decimal('50000'),
                ),
            },
        )
        outbound = MagicMock(spec=['send_command', 'send_abort'])
        outbound.send_command.return_value = 'praxis_cmd_42'
        sequencer = _make_sequencer(state=state)
        sequencer._praxis_outbound = outbound
        sequencer._config = config
        sequencer._shutdown_actions = {
            'test': [
                Action(action_type=ActionType.EXIT, trade_id='t-1', size=Decimal('0.5')),
                Action(action_type=ActionType.ABORT, command_id='cmd-99'),
            ],
        }

        sequencer._submit_actions()

        assert outbound.send_command.call_count == 1
        assert outbound.send_abort.call_count == 1
        assert sequencer._submitted_command_ids == ['praxis_cmd_42', 'cmd-99']

        abort_kwargs = outbound.send_abort.call_args.kwargs
        assert abort_kwargs['command_id'] == 'cmd-99'
        assert abort_kwargs['account_id'] == 'acc_001'
        assert abort_kwargs['reason'] == 'shutdown'

    def test_exit_side_derived_from_position_side(self) -> None:
        '''_build_exit_context picks the opposite side of the open position.'''

        config = InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            stp_mode=STPMode.CANCEL_TAKER,
        )
        state = InstanceState(
            capital=CapitalState(capital_pool=Decimal('10000')),
            positions={
                't-buy': Position(
                    trade_id='t-buy',
                    strategy_id='s',
                    symbol='BTCUSDT',
                    side=OrderSide.BUY,
                    size=Decimal('0.1'),
                    entry_price=Decimal('50000'),
                ),
                't-sell': Position(
                    trade_id='t-sell',
                    strategy_id='s',
                    symbol='BTCUSDT',
                    side=OrderSide.SELL,
                    size=Decimal('0.1'),
                    entry_price=Decimal('50000'),
                ),
            },
        )
        sequencer = _make_sequencer(state=state)
        sequencer._config = config

        ctx_buy = sequencer._build_exit_context(
            's',
            Action(action_type=ActionType.EXIT, trade_id='t-buy', size=Decimal('0.1')),
        )
        ctx_sell = sequencer._build_exit_context(
            's',
            Action(action_type=ActionType.EXIT, trade_id='t-sell', size=Decimal('0.1')),
        )

        assert ctx_buy is not None and ctx_buy.order_side == OrderSide.SELL
        assert ctx_sell is not None and ctx_sell.order_side == OrderSide.BUY

    def test_submit_actions_skips_when_outbound_missing(self) -> None:
        config = InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            stp_mode=STPMode.CANCEL_TAKER,
        )
        sequencer = _make_sequencer()
        sequencer._config = config
        sequencer._shutdown_actions = {
            'test': [Action(action_type=ActionType.ABORT, command_id='cmd-1')],
        }

        sequencer._submit_actions()

        assert sequencer._submitted_command_ids == []

    def test_submit_actions_skips_when_config_missing(self) -> None:
        outbound = MagicMock(spec=['send_command', 'send_abort'])
        sequencer = _make_sequencer()
        sequencer._praxis_outbound = outbound
        sequencer._shutdown_actions = {
            'test': [Action(action_type=ActionType.ABORT, command_id='cmd-1')],
        }

        sequencer._submit_actions()

        outbound.send_command.assert_not_called()
        outbound.send_abort.assert_not_called()
        assert sequencer._submitted_command_ids == []

    def test_submit_exit_skips_unknown_trade_id(self) -> None:
        config = InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            stp_mode=STPMode.CANCEL_TAKER,
        )
        outbound = MagicMock(spec=['send_command', 'send_abort'])
        sequencer = _make_sequencer()
        sequencer._praxis_outbound = outbound
        sequencer._config = config
        sequencer._shutdown_actions = {
            'test': [Action(action_type=ActionType.EXIT, trade_id='missing', size=Decimal('1'))],
        }

        sequencer._submit_actions()

        outbound.send_command.assert_not_called()
        assert sequencer._submitted_command_ids == []

    def test_submit_exit_swallows_send_command_errors(self) -> None:
        '''send_command exceptions do not abort shutdown or record a command_id.'''

        config = InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            stp_mode=STPMode.CANCEL_TAKER,
        )
        state = InstanceState(
            capital=CapitalState(capital_pool=Decimal('10000')),
            positions={
                't-1': Position(
                    trade_id='t-1',
                    strategy_id='test',
                    symbol='BTCUSDT',
                    side=OrderSide.BUY,
                    size=Decimal('0.5'),
                    entry_price=Decimal('50000'),
                ),
            },
        )
        outbound = MagicMock(spec=['send_command', 'send_abort'])
        outbound.send_command.side_effect = RuntimeError('praxis down')
        sequencer = _make_sequencer(state=state)
        sequencer._praxis_outbound = outbound
        sequencer._config = config
        sequencer._shutdown_actions = {
            'test': [
                Action(action_type=ActionType.EXIT, trade_id='t-1', size=Decimal('0.5')),
            ],
        }

        sequencer._submit_actions()

        assert outbound.send_command.call_count == 1
        assert sequencer._submitted_command_ids == []

    def test_submit_abort_swallows_send_abort_errors(self) -> None:
        '''send_abort exceptions do not abort shutdown or record a command_id.'''

        config = InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            stp_mode=STPMode.CANCEL_TAKER,
        )
        outbound = MagicMock(spec=['send_command', 'send_abort'])
        outbound.send_abort.side_effect = RuntimeError('abort not supported')
        sequencer = _make_sequencer()
        sequencer._praxis_outbound = outbound
        sequencer._config = config
        sequencer._shutdown_actions = {
            'test': [Action(action_type=ActionType.ABORT, command_id='cmd-1')],
        }

        sequencer._submit_actions()

        assert outbound.send_abort.call_count == 1
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

    def test_shutdown_halts_state_mode_before_stopping_signals(
        self, tmp_path: Path,
    ) -> None:
        '''PT-FIX-25: `shutdown()` must flip `state.mode` to HALTED
        BEFORE `_stop_signals` so any FILLED outcome that the
        OutcomeLoop drains between `_stop_signals` and
        `_stop_outcome_loop` cannot drive a strategy ENTER past the
        validator. The validator's `_check_operational_mode` stage
        rejects ENTER when `state.mode != ACTIVE`; pre-fix the mode
        stayed ACTIVE through the whole shutdown sequence, so a
        late-arriving outcome could leak a fresh order to the venue
        without going through `_dispatch_shutdown`.'''

        state = _make_instance_state()
        assert state.mode.mode == OperationalMode.ACTIVE

        observed_modes: list[OperationalMode] = []

        mock_loop = MagicMock()
        mock_loop.stop.side_effect = lambda: observed_modes.append(state.mode.mode)

        runner = _make_mock_runner()
        runner.dispatch_shutdown.return_value = []
        runner.dispatch_save.return_value = b''

        sequencer = ShutdownSequencer(
            runner=runner,
            manifest=_make_manifest(),
            state_store=_make_mock_state_store(),
            state=state,
            strategy_state_path=tmp_path,
            predict_loop=mock_loop,
        )

        sequencer.shutdown()

        assert observed_modes == [OperationalMode.HALTED], (
            f'_stop_signals saw mode={observed_modes!r}; expected HALTED '
            f'set before _stop_signals ran'
        )
        assert state.mode.mode == OperationalMode.HALTED
        assert state.mode.trigger == 'shutdown'

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

    def test_wait_terminal_escalates_abort_on_timeout(self) -> None:
        '''On first-round timeout, _wait_terminal sends ABORT for each pending command.'''

        config = InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            stp_mode=STPMode.CANCEL_TAKER,
        )
        q: queue.Queue[TradeOutcome] = queue.Queue()
        inbound = PraxisInbound(outcome_queue=q, poll_timeout=0.01)
        outbound = MagicMock(spec=['send_command', 'send_abort'])

        sequencer = ShutdownSequencer(
            runner=_make_mock_runner(),
            manifest=_make_manifest(),
            state_store=_make_mock_state_store(),
            state=_make_instance_state(),
            strategy_state_path=_PLACEHOLDER_PATH,
            praxis_inbound=inbound,
            praxis_outbound=outbound,
            config=config,
            shutdown_timeout=0.05,
        )
        sequencer._submitted_command_ids.extend(['cmd_a', 'cmd_b'])

        sequencer._wait_terminal()

        assert outbound.send_abort.call_count == 2
        reasons = {call.kwargs['reason'] for call in outbound.send_abort.call_args_list}
        command_ids = {call.kwargs['command_id'] for call in outbound.send_abort.call_args_list}
        assert reasons == {'shutdown_escalation'}
        assert command_ids == {'cmd_a', 'cmd_b'}

    def test_wait_terminal_escalation_noop_when_outbound_missing(self) -> None:
        '''Escalation is a no-op (logged) when outbound or config is missing.'''

        q: queue.Queue[TradeOutcome] = queue.Queue()
        inbound = PraxisInbound(outcome_queue=q, poll_timeout=0.01)

        sequencer = ShutdownSequencer(
            runner=_make_mock_runner(),
            manifest=_make_manifest(),
            state_store=_make_mock_state_store(),
            state=_make_instance_state(),
            strategy_state_path=_PLACEHOLDER_PATH,
            praxis_inbound=inbound,
            shutdown_timeout=0.05,
        )
        sequencer._submitted_command_ids.append('cmd_a')

        sequencer._wait_terminal()

    def test_wait_terminal_no_escalation_when_all_terminal(self) -> None:
        '''Escalation does not fire when all commands terminate before timeout.'''

        config = InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            stp_mode=STPMode.CANCEL_TAKER,
        )
        q: queue.Queue[TradeOutcome] = queue.Queue()
        q.put(TradeOutcome(
            outcome_id='out_a',
            command_id='cmd_a',
            outcome_type=TradeOutcomeType.CANCELED,
            timestamp=datetime.now(tz=timezone.utc),
        ))
        inbound = PraxisInbound(outcome_queue=q, poll_timeout=0.01)
        outbound = MagicMock(spec=['send_command', 'send_abort'])

        sequencer = ShutdownSequencer(
            runner=_make_mock_runner(),
            manifest=_make_manifest(),
            state_store=_make_mock_state_store(),
            state=_make_instance_state(),
            strategy_state_path=_PLACEHOLDER_PATH,
            praxis_inbound=inbound,
            praxis_outbound=outbound,
            config=config,
            shutdown_timeout=5.0,
        )
        sequencer._submitted_command_ids.append('cmd_a')

        sequencer._wait_terminal()

        outbound.send_abort.assert_not_called()


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


class TestStopOutcomeLoop:

    def test_stop_outcome_loop_calls_stop(self) -> None:
        '''_stop_outcome_loop calls outcome_loop.stop() when configured.'''

        mock_loop = MagicMock()
        sequencer = ShutdownSequencer(
            runner=_make_mock_runner(),
            manifest=_make_manifest(),
            state_store=_make_mock_state_store(),
            state=_make_instance_state(),
            strategy_state_path=_PLACEHOLDER_PATH,
            outcome_loop=mock_loop,
        )

        sequencer._stop_outcome_loop()

        mock_loop.stop.assert_called_once()

    def test_stop_outcome_loop_without_loop(self) -> None:
        '''_stop_outcome_loop completes without error when outcome_loop is None.'''

        sequencer = _make_sequencer()
        sequencer._stop_outcome_loop()

    def test_outcome_loop_stopped_before_wait_terminal(self) -> None:
        '''OutcomeLoop must halt before _wait_terminal polls the inbound queue.

        Captures call ordering via a shared counter; asserts
        outcome_loop.stop is invoked strictly before _wait_terminal
        starts polling the shared PraxisInbound.
        '''

        call_order: list[str] = []

        outcome_loop = MagicMock()
        outcome_loop.stop.side_effect = lambda: call_order.append('outcome_stop')

        inbound = MagicMock(spec=PraxisInbound)
        inbound.receive_outcome.side_effect = lambda: (
            call_order.append('inbound_poll') or None
        )

        sequencer = ShutdownSequencer(
            runner=_make_mock_runner(),
            manifest=_make_manifest(),
            state_store=_make_mock_state_store(),
            state=_make_instance_state(),
            strategy_state_path=_PLACEHOLDER_PATH,
            outcome_loop=outcome_loop,
            praxis_inbound=inbound,
            shutdown_timeout=0.01,
        )
        sequencer._submitted_command_ids = ['cmd_probe']

        sequencer._stop_outcome_loop()
        sequencer._wait_terminal()

        assert 'outcome_stop' in call_order
        if 'inbound_poll' in call_order:
            assert call_order.index('outcome_stop') < call_order.index(
                'inbound_poll',
            )


class TestShutdownExitAppliesToState:
    '''PT-FIX-31: shutdown EXIT FILL outcomes must update `state.positions`.

    Pre-fix `_wait_terminal` only checked `is_terminal` and `command_id in
    remaining`; it never invoked `OutcomeProcessor.process`. The OutcomeLoop
    was already stopped, so no other code path applied the EXIT FILL to
    state. Result: shutdown-EXIT FILLs were silently dropped at the state
    level, leaving the next boot to recover a stale `Position` and
    `position_notional` for a position the venue had already closed.

    Post-fix `_apply_terminal_outcome` routes FILLED outcomes through the
    wired `OutcomeProcessor`, which uses the non-entry `_reduce_position`
    path to decrement / remove the `Position` entry without touching
    `CapitalController` (the shutdown EXIT was never registered via
    `bridge_to_capital`).
    '''

    def _make_state_with_position(
        self,
        trade_id: str = 't-1',
        size: Decimal = Decimal('0.5'),
        entry_price: Decimal = Decimal('50000'),
    ) -> InstanceState:
        return InstanceState(
            capital=CapitalState(capital_pool=Decimal('10000')),
            positions={
                trade_id: Position(
                    trade_id=trade_id,
                    strategy_id='test',
                    symbol='BTCUSDT',
                    side=OrderSide.BUY,
                    size=size,
                    entry_price=entry_price,
                ),
            },
        )

    def _make_filled_outcome(self, command_id: str, fill_size: Decimal) -> TradeOutcome:
        return TradeOutcome(
            outcome_id='out-1',
            command_id=command_id,
            outcome_type=TradeOutcomeType.FILLED,
            timestamp=datetime.now(tz=timezone.utc),
            fill_size=fill_size,
            fill_price=Decimal('50000'),
            fill_notional=fill_size * Decimal('50000'),
            actual_fees=Decimal('0'),
        )

    def test_shutdown_exit_fill_removes_closed_position(
        self, tmp_path: Path,
    ) -> None:
        config = InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            stp_mode=STPMode.CANCEL_TAKER,
        )
        state = self._make_state_with_position()
        capital_controller = CapitalController(state.capital)
        state_store = StateStore(tmp_path)
        outcome_processor = OutcomeProcessor(capital_controller, state, state_store)

        outbound = MagicMock(spec=['send_command', 'send_abort'])
        outbound.send_command.return_value = 'praxis_cmd_42'

        q: queue.Queue[TradeOutcome] = queue.Queue()
        q.put(self._make_filled_outcome('praxis_cmd_42', Decimal('0.5')))
        inbound = PraxisInbound(outcome_queue=q, poll_timeout=0.01)

        sequencer = ShutdownSequencer(
            runner=_make_mock_runner(),
            manifest=_make_manifest(),
            state_store=_make_mock_state_store(),
            state=state,
            strategy_state_path=tmp_path,
            praxis_outbound=outbound,
            praxis_inbound=inbound,
            shutdown_timeout=2.0,
            config=config,
            outcome_processor=outcome_processor,
        )
        sequencer._shutdown_actions = {
            'test': [
                Action(
                    action_type=ActionType.EXIT,
                    trade_id='t-1',
                    size=Decimal('0.5'),
                ),
            ],
        }

        sequencer._submit_actions()
        sequencer._wait_terminal()

        assert 't-1' not in state.positions, (
            'shutdown EXIT FILL did not remove the closed position; '
            'OutcomeProcessor was not invoked'
        )

    def test_shutdown_exit_partial_fill_decrements_position(
        self, tmp_path: Path,
    ) -> None:
        config = InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            stp_mode=STPMode.CANCEL_TAKER,
        )
        state = self._make_state_with_position(size=Decimal('1.0'))
        capital_controller = CapitalController(state.capital)
        state_store = StateStore(tmp_path)
        outcome_processor = OutcomeProcessor(capital_controller, state, state_store)

        outbound = MagicMock(spec=['send_command', 'send_abort'])
        outbound.send_command.return_value = 'praxis_cmd_42'

        q: queue.Queue[TradeOutcome] = queue.Queue()
        q.put(self._make_filled_outcome('praxis_cmd_42', Decimal('0.4')))
        inbound = PraxisInbound(outcome_queue=q, poll_timeout=0.01)

        sequencer = ShutdownSequencer(
            runner=_make_mock_runner(),
            manifest=_make_manifest(),
            state_store=_make_mock_state_store(),
            state=state,
            strategy_state_path=tmp_path,
            praxis_outbound=outbound,
            praxis_inbound=inbound,
            shutdown_timeout=2.0,
            config=config,
            outcome_processor=outcome_processor,
        )
        sequencer._shutdown_actions = {
            'test': [
                Action(
                    action_type=ActionType.EXIT,
                    trade_id='t-1',
                    size=Decimal('0.4'),
                ),
            ],
        }

        sequencer._submit_actions()
        sequencer._wait_terminal()

        assert state.positions['t-1'].size == Decimal('0.6')

    def test_shutdown_exit_rejected_leaves_position_open(
        self, tmp_path: Path,
    ) -> None:
        '''A REJECTED outcome means the venue did NOT close the position.
        Leaving `state.positions` untouched is the correct semantic — pre-
        and post-fix behavior is identical here, since the post-fix
        `_apply_terminal_outcome` deliberately skips non-FILL terminals.'''

        config = InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            stp_mode=STPMode.CANCEL_TAKER,
        )
        state = self._make_state_with_position()
        capital_controller = CapitalController(state.capital)
        state_store = StateStore(tmp_path)
        outcome_processor = OutcomeProcessor(capital_controller, state, state_store)

        outbound = MagicMock(spec=['send_command', 'send_abort'])
        outbound.send_command.return_value = 'praxis_cmd_42'

        rejected = TradeOutcome(
            outcome_id='out-1',
            command_id='praxis_cmd_42',
            outcome_type=TradeOutcomeType.REJECTED,
            timestamp=datetime.now(tz=timezone.utc),
            reject_reason='venue rejected',
        )
        q: queue.Queue[TradeOutcome] = queue.Queue()
        q.put(rejected)
        inbound = PraxisInbound(outcome_queue=q, poll_timeout=0.01)

        sequencer = ShutdownSequencer(
            runner=_make_mock_runner(),
            manifest=_make_manifest(),
            state_store=_make_mock_state_store(),
            state=state,
            strategy_state_path=tmp_path,
            praxis_outbound=outbound,
            praxis_inbound=inbound,
            shutdown_timeout=2.0,
            config=config,
            outcome_processor=outcome_processor,
        )
        sequencer._shutdown_actions = {
            'test': [
                Action(
                    action_type=ActionType.EXIT,
                    trade_id='t-1',
                    size=Decimal('0.5'),
                ),
            ],
        }

        sequencer._submit_actions()
        sequencer._wait_terminal()

        assert 't-1' in state.positions
        assert state.positions['t-1'].size == Decimal('0.5')

    def test_shutdown_exit_without_outcome_processor_logs_warning(
        self, tmp_path: Path,
    ) -> None:
        '''When `outcome_processor` is None, the FILL is observed but state
        is not updated. The sequencer logs a warning rather than crashing —
        avoids regressing on partially-wired test setups.'''

        config = InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            stp_mode=STPMode.CANCEL_TAKER,
        )
        state = self._make_state_with_position()
        outbound = MagicMock(spec=['send_command', 'send_abort'])
        outbound.send_command.return_value = 'praxis_cmd_42'

        q: queue.Queue[TradeOutcome] = queue.Queue()
        q.put(self._make_filled_outcome('praxis_cmd_42', Decimal('0.5')))
        inbound = PraxisInbound(outcome_queue=q, poll_timeout=0.01)

        sequencer = ShutdownSequencer(
            runner=_make_mock_runner(),
            manifest=_make_manifest(),
            state_store=_make_mock_state_store(),
            state=state,
            strategy_state_path=tmp_path,
            praxis_outbound=outbound,
            praxis_inbound=inbound,
            shutdown_timeout=2.0,
            config=config,
        )
        sequencer._shutdown_actions = {
            'test': [
                Action(
                    action_type=ActionType.EXIT,
                    trade_id='t-1',
                    size=Decimal('0.5'),
                ),
            ],
        }

        sequencer._submit_actions()
        sequencer._wait_terminal()

        assert 't-1' in state.positions


class TestSubmitExitMissingPositionRace:
    '''PT-FIX-36: `_build_exit_order_context` must use `.get()` instead
    of bare `[]` on `state.positions`. `_outcome_loop.stop()` does not
    block — a final OutcomeLoop tick can process a FILL between
    `_build_exit_context` (which `.get()`-checks the position) and
    `_build_exit_order_context` (which previously used `[]`). The
    final tick removes the position via `_reduce_position`; the bare
    `[]` then raises `KeyError`. The surrounding `try / except
    ValueError` does NOT catch `KeyError`, so the exception
    propagates out of `_submit_exit`, aborting the entire shutdown
    sequence before `_dispatch_save` / `_persist_strategy_state` /
    `_final_checkpoint` can run.

    Post-fix: `.get()` returns `None`, the explicit `is None` check
    raises `ValueError` (which IS caught), the OrderContext store is
    skipped, and shutdown continues. The terminal-outcome handler
    will gracefully no-op for this command_id since no context is
    stored.
    '''

    def test_position_removed_between_context_and_order_context_does_not_abort(
        self, tmp_path: Path,
    ) -> None:
        config = InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            stp_mode=STPMode.CANCEL_TAKER,
        )
        state = InstanceState(
            capital=CapitalState(capital_pool=Decimal('10000')),
            positions={
                't-1': Position(
                    trade_id='t-1',
                    strategy_id='test',
                    symbol='BTCUSDT',
                    side=OrderSide.BUY,
                    size=Decimal('0.5'),
                    entry_price=Decimal('50000'),
                ),
            },
        )

        outbound = MagicMock(spec=['send_command', 'send_abort'])

        def _send_command_then_remove_position(_cmd: object) -> str:
            del state.positions['t-1']
            return 'praxis_cmd_42'

        outbound.send_command.side_effect = _send_command_then_remove_position

        sequencer = ShutdownSequencer(
            runner=_make_mock_runner(),
            manifest=_make_manifest(),
            state_store=_make_mock_state_store(),
            state=state,
            strategy_state_path=tmp_path,
            praxis_outbound=outbound,
            shutdown_timeout=0.01,
            config=config,
        )
        sequencer._shutdown_actions = {
            'test': [
                Action(
                    action_type=ActionType.EXIT,
                    trade_id='t-1',
                    size=Decimal('0.5'),
                ),
            ],
        }

        sequencer._submit_actions()

        assert 'praxis_cmd_42' in sequencer._submitted_command_ids
        assert 'praxis_cmd_42' not in sequencer._exit_contexts

    def test_build_exit_order_context_raises_value_error_for_missing_position(
        self,
    ) -> None:
        '''Direct unit test on `_build_exit_order_context`: passing an
        action whose trade_id is not in `state.positions` raises
        `ValueError` (NOT `KeyError`). The surrounding `try / except
        ValueError` in `_submit_exit` catches `ValueError` only.'''

        config = InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            stp_mode=STPMode.CANCEL_TAKER,
        )
        state = _make_instance_state()

        sequencer = _make_sequencer(state=state)
        sequencer._config = config

        action = Action(
            action_type=ActionType.EXIT,
            trade_id='t-missing',
            size=Decimal('0.1'),
        )
        validation_context = ValidationRequestContext(
            strategy_id='test',
            action=ValidationAction.EXIT,
            symbol='BTCUSDT',
            order_side=OrderSide.SELL,
            order_size=Decimal('0.1'),
            command_id='cmd-1',
            trade_id='t-missing',
            order_notional=Decimal('0'),
            estimated_fees=Decimal('0'),
            strategy_budget=Decimal('0'),
            state=state,
            config=config,
        )

        with pytest.raises(ValueError, match=r'not in state\.positions'):
            sequencer._build_exit_order_context(
                strategy_id='test',
                action=action,
                command_id='cmd-1',
                validation_context=validation_context,
            )


class TestShutdownExitPartialFill:
    '''PT-FIX-38: PARTIAL fills during shutdown EXIT must update
    `state.positions`. Pre-fix `_apply_terminal_outcome` only routed
    FILLED outcomes through `OutcomeProcessor`. PARTIAL outcomes
    failed `is_terminal` so `_poll_until_terminal` never forwarded
    them, and even if they had been forwarded the `== FILLED` check
    in `_apply_terminal_outcome` would have dropped them. Net effect:
    a partial fill on a shutdown EXIT followed by a CANCELED /
    EXPIRED terminal left `state.positions[trade_id].size` carrying
    the partial-fill amount that the venue had actually decremented.

    Post-fix: `_poll_until_terminal` forwards `is_fill` outcomes
    (PARTIAL + FILLED) to `_apply_terminal_outcome` for state
    update; the gate is now `is_fill` rather than `== FILLED`.
    '''

    def _make_state_with_position(
        self,
        trade_id: str = 't-1',
        size: Decimal = Decimal('1.0'),
        entry_price: Decimal = Decimal('50000'),
    ) -> InstanceState:
        return InstanceState(
            capital=CapitalState(capital_pool=Decimal('10000')),
            positions={
                trade_id: Position(
                    trade_id=trade_id,
                    strategy_id='test',
                    symbol='BTCUSDT',
                    side=OrderSide.BUY,
                    size=size,
                    entry_price=entry_price,
                ),
            },
        )

    def _make_partial_outcome(
        self,
        command_id: str,
        fill_size: Decimal,
    ) -> TradeOutcome:
        return TradeOutcome(
            outcome_id=f'out-partial-{command_id}',
            command_id=command_id,
            outcome_type=TradeOutcomeType.PARTIAL,
            timestamp=datetime.now(tz=timezone.utc),
            fill_size=fill_size,
            fill_price=Decimal('50000'),
            fill_notional=fill_size * Decimal('50000'),
            actual_fees=Decimal('0'),
        )

    def _make_canceled_outcome(self, command_id: str) -> TradeOutcome:
        return TradeOutcome(
            outcome_id=f'out-cancel-{command_id}',
            command_id=command_id,
            outcome_type=TradeOutcomeType.CANCELED,
            timestamp=datetime.now(tz=timezone.utc),
        )

    def _make_filled_outcome(
        self,
        command_id: str,
        fill_size: Decimal,
    ) -> TradeOutcome:
        return TradeOutcome(
            outcome_id=f'out-fill-{command_id}',
            command_id=command_id,
            outcome_type=TradeOutcomeType.FILLED,
            timestamp=datetime.now(tz=timezone.utc),
            fill_size=fill_size,
            fill_price=Decimal('50000'),
            fill_notional=fill_size * Decimal('50000'),
            actual_fees=Decimal('0'),
        )

    def test_partial_fill_then_canceled_decrements_position_by_partial_amount(
        self, tmp_path: Path,
    ) -> None:
        config = InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            stp_mode=STPMode.CANCEL_TAKER,
        )
        state = self._make_state_with_position(size=Decimal('1.0'))
        capital_controller = CapitalController(state.capital)
        state_store = StateStore(tmp_path)
        outcome_processor = OutcomeProcessor(capital_controller, state, state_store)

        outbound = MagicMock(spec=['send_command', 'send_abort'])
        outbound.send_command.return_value = 'praxis_cmd_42'

        q: queue.Queue[TradeOutcome] = queue.Queue()
        q.put(self._make_partial_outcome('praxis_cmd_42', Decimal('0.4')))
        q.put(self._make_canceled_outcome('praxis_cmd_42'))
        inbound = PraxisInbound(outcome_queue=q, poll_timeout=0.01)

        sequencer = ShutdownSequencer(
            runner=_make_mock_runner(),
            manifest=_make_manifest(),
            state_store=_make_mock_state_store(),
            state=state,
            strategy_state_path=tmp_path,
            praxis_outbound=outbound,
            praxis_inbound=inbound,
            shutdown_timeout=2.0,
            config=config,
            outcome_processor=outcome_processor,
        )
        sequencer._shutdown_actions = {
            'test': [
                Action(
                    action_type=ActionType.EXIT,
                    trade_id='t-1',
                    size=Decimal('1.0'),
                ),
            ],
        }

        sequencer._submit_actions()
        sequencer._wait_terminal()

        assert state.positions['t-1'].size == Decimal('0.6')

    def test_filled_outcome_only_decrements_once_when_also_terminal(
        self, tmp_path: Path,
    ) -> None:
        '''FILLED outcomes are both `is_fill` AND `is_terminal`. The
        `_poll_until_terminal` loop must invoke `_apply_terminal_outcome`
        exactly once for them — not twice. A double-decrement would
        underflow the position.'''

        config = InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            stp_mode=STPMode.CANCEL_TAKER,
        )
        state = self._make_state_with_position(size=Decimal('1.0'))
        capital_controller = CapitalController(state.capital)
        state_store = StateStore(tmp_path)
        outcome_processor = OutcomeProcessor(capital_controller, state, state_store)

        outbound = MagicMock(spec=['send_command', 'send_abort'])
        outbound.send_command.return_value = 'praxis_cmd_42'

        q: queue.Queue[TradeOutcome] = queue.Queue()
        q.put(self._make_filled_outcome('praxis_cmd_42', Decimal('1.0')))
        inbound = PraxisInbound(outcome_queue=q, poll_timeout=0.01)

        sequencer = ShutdownSequencer(
            runner=_make_mock_runner(),
            manifest=_make_manifest(),
            state_store=_make_mock_state_store(),
            state=state,
            strategy_state_path=tmp_path,
            praxis_outbound=outbound,
            praxis_inbound=inbound,
            shutdown_timeout=2.0,
            config=config,
            outcome_processor=outcome_processor,
        )
        sequencer._shutdown_actions = {
            'test': [
                Action(
                    action_type=ActionType.EXIT,
                    trade_id='t-1',
                    size=Decimal('1.0'),
                ),
            ],
        }

        sequencer._submit_actions()
        sequencer._wait_terminal()

        assert 't-1' not in state.positions
