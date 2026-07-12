'''Tests for StartupSequencer.'''

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nexus.core.domain.capital_state import CapitalState
from nexus.core.domain.enums import OperationalMode, OrderSide
from nexus.core.domain.instance_state import InstanceState
from nexus.core.domain.position import Position
from nexus.infrastructure.state_store import StateStore
from nexus.infrastructure.strategy_event import StrategyEvent
from nexus.startup import StartupError, StartupSequencer
from nexus.startup.sequencer import SignalBinding
from nexus.strategy.action import Action, ActionType
from nexus.strategy.runner import StrategyRunner


def _make_mock_state_store() -> MagicMock:
    mock = MagicMock(spec=StateStore)
    return mock


def _sensors_yaml(_tmp_path: Path) -> str:
    return (
        '    signal:\n'
        '      series: time_15m\n'
        '      interval_seconds: 60\n'
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
    sequencer = StartupSequencer(
        state_store=state_store or _make_mock_state_store(),
        manifest_path=manifest_path or _PLACEHOLDER_MANIFEST,
        strategies_base_path=strategies_base_path or _PLACEHOLDER_STRATEGIES,
        strategy_state_path=strategy_state_path,
    )
    return sequencer


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

    def test_reconcile_capital_uses_avg_cost_basis_for_both_present(self) -> None:
        '''When a trade_id is present in both Nexus and Praxis,
        `praxis_total_notional` must accumulate `qty * nexus_pos.avg_cost_basis`
        (fee-inclusive) rather than `qty * praxis_avg_entry_price`
        (Praxis has no fee data). Without this, `position_notional`
        post-reconcile is below the `cost_basis_released` that the
        next EXIT FILL computes from `Position.avg_cost_basis`,
        triggering INVARIANT_BREACH on every post-crash EXIT.'''

        praxis_pos = MagicMock()
        praxis_pos.account_id = 'acc_001'
        praxis_pos.trade_id = 'shared_trade'
        praxis_pos.symbol = 'BTCUSDT'
        praxis_pos.side = OrderSide.BUY
        praxis_pos.qty = Decimal('0.002')
        praxis_pos.avg_entry_price = Decimal('50000')
        praxis_pos.strategy_id = 'momentum'

        outbound = MagicMock()
        outbound.pull_positions.return_value = {
            ('acc_001', 'shared_trade'): praxis_pos,
        }

        sequencer = _make_sequencer()
        sequencer._praxis_outbound = outbound
        _attach_stub_manifest(sequencer, account_id='acc_001')
        state = InstanceState(
            capital=CapitalState(capital_pool=Decimal('10000')),
        )
        state.positions['shared_trade'] = Position(
            trade_id='shared_trade',
            strategy_id='momentum',
            symbol='BTCUSDT',
            side=OrderSide.BUY,
            size=Decimal('0.002'),
            entry_price=Decimal('50000'),
            avg_cost_basis=Decimal('50500'),
        )
        sequencer._state = state

        sequencer._reconcile_capital()

        assert sequencer._state.capital.position_notional == Decimal('101.000')

    def test_reconcile_capital_size_mismatch_adopts_praxis_qty(self) -> None:
        '''When `nexus_pos.size != qty`, `_reconcile_capital` must update
        `nexus_pos.size = qty` so downstream `reconcile_at_boot` rebuilds
        `per_strategy_deployed` from the same qty that
        `praxis_total_notional` uses. Pre-fix the warning-only branch
        left `position_notional` (Praxis qty) and `per_strategy_deployed`
        (stale Nexus qty) divergent → permanent attribution-mismatch
        denial of every subsequent ENTER. Reachable on every reboot
        following a crash mid-fill where the persisted Nexus snapshot
        lags Praxis's WS-applied state.
        '''

        praxis_pos = MagicMock()
        praxis_pos.account_id = 'acc_001'
        praxis_pos.trade_id = 'shared_trade'
        praxis_pos.symbol = 'BTCUSDT'
        praxis_pos.side = OrderSide.BUY
        praxis_pos.qty = Decimal('5')
        praxis_pos.avg_entry_price = Decimal('50000')
        praxis_pos.strategy_id = 'momentum'

        outbound = MagicMock()
        outbound.pull_positions.return_value = {
            ('acc_001', 'shared_trade'): praxis_pos,
        }

        sequencer = _make_sequencer()
        sequencer._praxis_outbound = outbound
        _attach_stub_manifest(sequencer, account_id='acc_001')
        state = InstanceState(
            capital=CapitalState(capital_pool=Decimal('10000000')),
        )
        state.positions['shared_trade'] = Position(
            trade_id='shared_trade',
            strategy_id='momentum',
            symbol='BTCUSDT',
            side=OrderSide.BUY,
            size=Decimal('10'),
            entry_price=Decimal('50000'),
            avg_cost_basis=Decimal('50000'),
        )
        sequencer._state = state

        sequencer._reconcile_capital()

        assert sequencer._state.positions['shared_trade'].size == Decimal('5')
        assert sequencer._state.capital.position_notional == Decimal('250000')

    def test_reconcile_capital_zero_avg_cost_basis_falls_back(self) -> None:
        '''When `nexus_pos.avg_cost_basis == _ZERO` (legacy snapshot
        placeholder, or position imported via a path that did not
        populate the field), `_reconcile_capital` must fall back to
        the Praxis avg_entry_price (or `nexus_pos.entry_price` if the
        Praxis price is also zero). Pre-fix `praxis_total_notional`
        accumulated `qty * 0 == 0`, undercounting `position_notional`
        and triggering INVARIANT_BREACH on the next EXIT fill.
        '''

        praxis_pos = MagicMock()
        praxis_pos.account_id = 'acc_001'
        praxis_pos.trade_id = 'shared_trade'
        praxis_pos.symbol = 'BTCUSDT'
        praxis_pos.side = OrderSide.BUY
        praxis_pos.qty = Decimal('2')
        praxis_pos.avg_entry_price = Decimal('50000')
        praxis_pos.strategy_id = 'momentum'

        outbound = MagicMock()
        outbound.pull_positions.return_value = {
            ('acc_001', 'shared_trade'): praxis_pos,
        }

        sequencer = _make_sequencer()
        sequencer._praxis_outbound = outbound
        _attach_stub_manifest(sequencer, account_id='acc_001')
        state = InstanceState(
            capital=CapitalState(capital_pool=Decimal('10000000')),
        )
        state.positions['shared_trade'] = Position(
            trade_id='shared_trade',
            strategy_id='momentum',
            symbol='BTCUSDT',
            side=OrderSide.BUY,
            size=Decimal('2'),
            entry_price=Decimal('49000'),
            avg_cost_basis=Decimal('0'),
        )
        sequencer._state = state

        sequencer._reconcile_capital()

        assert sequencer._state.capital.position_notional == Decimal('100000')
        assert sequencer._state.positions['shared_trade'].avg_cost_basis == Decimal('50000')

    def test_reconcile_capital_fails_closed_on_nexus_only_position(self) -> None:
        '''A Nexus position absent from the Praxis snapshot must not be
        silently deleted — that would orphan the venue holding. Reconcile
        fails closed (raises StartupError) and preserves the position so a
        subsequent consistent boot can recover it.'''

        outbound = MagicMock()
        outbound.pull_positions.return_value = {}

        sequencer = _make_sequencer()
        sequencer._praxis_outbound = outbound
        _attach_stub_manifest(sequencer, account_id='acc_001')
        state = InstanceState(
            capital=CapitalState(
                capital_pool=Decimal('10000'),
                position_notional=Decimal('25000'),
            ),
        )
        state.positions['stale_trade'] = Position(
            trade_id='stale_trade',
            strategy_id='momentum',
            symbol='BTCUSDT',
            side=OrderSide.BUY,
            size=Decimal('0.5'),
            entry_price=Decimal('50000'),
        )
        sequencer._state = state

        with pytest.raises(StartupError):
            sequencer._reconcile_capital()

        assert 'stale_trade' in sequencer._state.positions

    def test_reconcile_capital_keeps_position_present_in_both(self) -> None:
        '''A position present in both Nexus and Praxis stays put
        (regression — eviction must only target Nexus-only entries).'''

        praxis_pos = MagicMock()
        praxis_pos.account_id = 'acc_001'
        praxis_pos.trade_id = 'shared_trade'
        praxis_pos.symbol = 'BTCUSDT'
        praxis_pos.side = OrderSide.BUY
        praxis_pos.qty = Decimal('0.5')
        praxis_pos.avg_entry_price = Decimal('50000')
        praxis_pos.strategy_id = 'momentum'

        outbound = MagicMock()
        outbound.pull_positions.return_value = {
            ('acc_001', 'shared_trade'): praxis_pos,
        }

        sequencer = _make_sequencer()
        sequencer._praxis_outbound = outbound
        _attach_stub_manifest(sequencer, account_id='acc_001')
        state = InstanceState(
            capital=CapitalState(capital_pool=Decimal('10000')),
        )
        state.positions['shared_trade'] = Position(
            trade_id='shared_trade',
            strategy_id='momentum',
            symbol='BTCUSDT',
            side=OrderSide.BUY,
            size=Decimal('0.5'),
            entry_price=Decimal('50000'),
        )
        sequencer._state = state

        sequencer._reconcile_capital()

        assert 'shared_trade' in sequencer._state.positions

    def test_reconcile_capital_matches_on_pos_trade_id_not_tuple_order(self) -> None:
        '''Praxis keys positions `(trade_id, account_id)`; reconcile must
        match on `pos.trade_id`, not on a tuple position. A position
        present in both must reconcile cleanly (no spurious fail-closed),
        regardless of the snapshot key ordering.'''

        praxis_pos = MagicMock()
        praxis_pos.account_id = 'acc_001'
        praxis_pos.trade_id = 'shared_trade'
        praxis_pos.symbol = 'BTCUSDT'
        praxis_pos.side = OrderSide.BUY
        praxis_pos.qty = Decimal('0.5')
        praxis_pos.avg_entry_price = Decimal('50000')
        praxis_pos.strategy_id = 'momentum'

        outbound = MagicMock()
        outbound.pull_positions.return_value = {
            ('shared_trade', 'acc_001'): praxis_pos,
        }

        sequencer = _make_sequencer()
        sequencer._praxis_outbound = outbound
        _attach_stub_manifest(sequencer, account_id='acc_001')
        state = InstanceState(
            capital=CapitalState(capital_pool=Decimal('10000')),
        )
        state.positions['shared_trade'] = Position(
            trade_id='shared_trade',
            strategy_id='momentum',
            symbol='BTCUSDT',
            side=OrderSide.BUY,
            size=Decimal('0.5'),
            entry_price=Decimal('50000'),
        )
        sequencer._state = state

        sequencer._reconcile_capital()

        assert 'shared_trade' in sequencer._state.positions

    def test_reconcile_capital_fails_closed_preserving_nexus_only(self) -> None:
        '''Mixed scenario — Nexus has trade_a only, Praxis has trade_b
        only. The Praxis-only position imports, but the Nexus-only trade_a
        makes reconcile fail closed (StartupError) and stay preserved
        rather than being silently deleted.'''

        praxis_pos = MagicMock()
        praxis_pos.account_id = 'acc_001'
        praxis_pos.trade_id = 'trade_b'
        praxis_pos.symbol = 'BTCUSDT'
        praxis_pos.side = OrderSide.BUY
        praxis_pos.qty = Decimal('1')
        praxis_pos.avg_entry_price = Decimal('40000')
        praxis_pos.strategy_id = 'momentum'

        outbound = MagicMock()
        outbound.pull_positions.return_value = {
            ('acc_001', 'trade_b'): praxis_pos,
        }

        sequencer = _make_sequencer()
        sequencer._praxis_outbound = outbound
        _attach_stub_manifest(sequencer, account_id='acc_001')
        state = InstanceState(
            capital=CapitalState(
                capital_pool=Decimal('100000'),
                position_notional=Decimal('25000'),
            ),
        )
        state.positions['trade_a'] = Position(
            trade_id='trade_a',
            strategy_id='momentum',
            symbol='ETHUSDT',
            side=OrderSide.BUY,
            size=Decimal('0.5'),
            entry_price=Decimal('50000'),
        )
        sequencer._state = state

        with pytest.raises(StartupError):
            sequencer._reconcile_capital()

        assert 'trade_a' in sequencer._state.positions
        assert 'trade_b' not in sequencer._state.positions

    def test_reconcile_capital_skips_praxis_position_without_strategy_id(self) -> None:
        '''Praxis positions lacking strategy_id are not imported AND do not
        contribute to `position_notional`. Pre-fix the position was dropped
        from `state.positions` but its `qty * price` still inflated
        `praxis_total_notional`; downstream `reconcile_at_boot` rebuilt
        `per_strategy_deployed` only from `state.positions` (excluding the
        un-importable). Result: `position_notional > sum(per_strategy_deployed)`
        → `'Per-strategy deployed attribution mismatch for non-flat state'`
        denial → permanent ENTER refusal for the rest of the boot.
        '''

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
        assert sequencer._state.capital.position_notional == Decimal('0')

    def test_reconcile_capital_un_importable_does_not_block_importable(self) -> None:
        '''When some Praxis positions import successfully and others don't
        (mixed batch — typical post-TD-E-fix migration period), the
        un-importable contributions are skipped while importable ones flow
        through normally. `position_notional` reflects only the importable
        positions.
        '''

        importable = MagicMock()
        importable.account_id = 'acc_001'
        importable.trade_id = 'trade_good'
        importable.symbol = 'BTCUSDT'
        importable.side = OrderSide.BUY
        importable.qty = Decimal('0.2')
        importable.avg_entry_price = Decimal('50000')
        importable.strategy_id = 'momentum'

        un_importable = MagicMock()
        un_importable.account_id = 'acc_001'
        un_importable.trade_id = 'trade_orphan'
        un_importable.symbol = 'ETHUSDT'
        un_importable.side = OrderSide.BUY
        un_importable.qty = Decimal('1.5')
        un_importable.avg_entry_price = Decimal('3000')
        un_importable.strategy_id = None

        outbound = MagicMock()
        outbound.pull_positions.return_value = {
            ('acc_001', 'trade_good'): importable,
            ('acc_001', 'trade_orphan'): un_importable,
        }

        sequencer = _make_sequencer()
        sequencer._praxis_outbound = outbound
        _attach_stub_manifest(sequencer, account_id='acc_001')
        sequencer._state = InstanceState(
            capital=CapitalState(capital_pool=Decimal('10000000')),
        )

        sequencer._reconcile_capital()

        assert 'trade_good' in sequencer._state.positions
        assert 'trade_orphan' not in sequencer._state.positions
        assert sequencer._state.capital.position_notional == Decimal('10000')

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

    def test_build_signal_bindings_without_manifest_raises(self) -> None:
        sequencer = _make_sequencer()

        with pytest.raises(StartupError, match='manifest not loaded'):
            sequencer._build_signal_bindings()

    def test_build_signal_bindings_one_per_strategy(self, tmp_path: Path) -> None:
        '''Each manifest strategy yields exactly one SignalBinding
        carrying that strategy's series / interval / name.'''

        manifest_path = tmp_path / 'manifest.yaml'
        strategy_file = tmp_path / 'strat.py'
        strategy_file.write_text(VALID_STRATEGY)
        manifest_path.write_text(
            'account_id: test_acct\n'
            'allocated_capital: 10000\n'
            'capital_pool: 10000\n'
            'strategies:\n'
            '  - id: alpha\n'
            '    file: strat.py\n'
            '    signal:\n'
            '      series: time_15m\n'
            '      interval_seconds: 30\n'
            '      name: alpha_cohort\n'
            '    capital_pct: 40\n'
            '  - id: beta\n'
            '    file: strat.py\n'
            '    signal:\n'
            '      series: time_1h\n'
            '      interval_seconds: 60\n'
            '    capital_pct: 50\n'
        )
        sequencer = _make_sequencer(
            manifest_path=manifest_path,
            strategies_base_path=tmp_path,
        )
        sequencer._load_manifest()

        sequencer._build_signal_bindings()

        bindings = sequencer.signal_bindings
        assert bindings == [
            SignalBinding(
                strategy_id='alpha',
                series='time_15m',
                interval_seconds=30,
                name='alpha_cohort',
            ),
            SignalBinding(
                strategy_id='beta',
                series='time_1h',
                interval_seconds=60,
                name=None,
            ),
        ]

    def test_build_signal_bindings_empty_when_no_strategies(self) -> None:
        '''No strategies means no bindings (no error — the manifest
        validators already reject an empty strategies list at load).'''

        sequencer = _make_sequencer()
        manifest = _attach_stub_manifest(sequencer)
        manifest.strategies = ()

        sequencer._build_signal_bindings()

        assert sequencer.signal_bindings == []

    def test_register_timers_without_manifest_raises(self) -> None:
        sequencer = _make_sequencer()

        with pytest.raises(StartupError, match='manifest not loaded'):
            sequencer._register_timers()

    def test_determine_mode_defaults_to_reduce_only_without_health(self) -> None:
        '''PT-FIX-26: When no `health_snapshot` is wired at boot,
        `_determine_mode` defaults to REDUCE_ONLY (not ACTIVE) so the
        validator's `_check_operational_mode` stage rejects ENTER
        actions during the ~5 s window before the first HealthLoop
        tick lands. Pre-fix the sequencer defaulted to ACTIVE,
        leaving an unprotected window where on_startup ENTERs reach
        the venue even when Praxis health is already degraded.'''

        sequencer = _make_sequencer()

        sequencer._determine_mode()

        assert sequencer._mode == OperationalMode.REDUCE_ONLY

    def test_determine_mode_writes_health_mode(self) -> None:
        '''PT-FIX-26: `_determine_mode` writes its decision into
        `state.health_mode` (the health input); the launcher's
        `ModeController.reconcile` commits it to `state.mode`, keeping the
        controller the sole `state.mode` writer. Sequencer-local `_mode`
        alone is invisible to the controller.'''

        state_store = _make_mock_state_store()
        state_store.recover.return_value = InstanceState(
            capital=CapitalState(capital_pool=Decimal('10000')),
        )
        sequencer = _make_sequencer(state_store=state_store)
        _attach_stub_manifest(sequencer)
        sequencer._recover_state()

        sequencer._determine_mode()

        assert sequencer._state is not None
        assert sequencer._state.health_mode == OperationalMode.REDUCE_ONLY
        assert sequencer._mode == OperationalMode.REDUCE_ONLY


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

    def test_drain_pending_forwards_buffered_actions(self) -> None:

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

    def test_drain_pending_is_idempotent(self) -> None:

        sequencer = _make_sequencer()
        sequencer._pending_startup_actions = {
            'strat_a': [Action(action_type=ActionType.EXIT, trade_id='trade_a', size=Decimal('1'))],
        }

        submitter = MagicMock()
        sequencer.drain_pending_startup_actions(submitter)
        sequencer.drain_pending_startup_actions(submitter)

        assert submitter.call_count == 1

    def test_drain_pending_swallows_per_strategy_submitter_exceptions(self) -> None:

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
