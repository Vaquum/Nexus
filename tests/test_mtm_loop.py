'''Tests for MtmLoop periodic mark-to-market of open positions.

Pins: invalid intervals are rejected; tick_once marks longs/shorts
correctly under various mark prices; provider returning None aborts
the tick without partial writes; aggregate is written via
update_unrealized_pnl which recomputes drawdown metrics; start/stop
is idempotent; periodic tick fires off the timer thread.
'''

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from nexus.core.domain.capital_state import CapitalState
from nexus.core.domain.enums import OrderSide
from nexus.core.domain.instance_state import InstanceState
from nexus.core.domain.position import Position
from nexus.core.domain.risk_state import StrategyRiskState
from nexus.core.mtm_loop import MtmLoop


def _make_state() -> InstanceState:
    return InstanceState(capital=CapitalState(capital_pool=Decimal('100000')))


def _make_position(
    trade_id: str,
    side: OrderSide,
    size: str,
    entry_price: str,
    symbol: str = 'BTCUSDT',
) -> Position:
    return Position(
        trade_id=trade_id,
        strategy_id=f'{trade_id}_strat',
        symbol=symbol,
        side=side,
        size=Decimal(size),
        entry_price=Decimal(entry_price),
        unrealized_pnl=Decimal('0'),
    )


def test_invalid_interval_rejected() -> None:
    state = _make_state()

    with pytest.raises(ValueError, match='interval_seconds must be a positive number'):
        MtmLoop(
            state=state,
            mark_price_provider=lambda _s: Decimal('0'),
            interval_seconds=0,
        )


def test_lock_identity_mismatch_rejected_at_construction() -> None:
    '''When positions_lock is supplied, state.risk.lock must be the
    SAME object. MtmLoop mutates state.risk.per_strategy and writes
    RiskState fields whose mutual exclusion against OutcomeProcessor +
    validator depends on the identity. Mirrors ShutdownSequencer's
    guard at shutdown_sequencer.py:188-204.
    '''

    state = _make_state()
    positions_lock = threading.Lock()
    state.risk.lock = threading.Lock()

    with pytest.raises(RuntimeError, match=r'state\.risk\.lock is positions_lock'):
        MtmLoop(
            state=state,
            mark_price_provider=lambda _s: Decimal('70000'),
            positions_lock=positions_lock,
        )


def test_lock_identity_match_accepted() -> None:
    state = _make_state()
    positions_lock = threading.Lock()
    state.risk.lock = positions_lock

    MtmLoop(
        state=state,
        mark_price_provider=lambda _s: Decimal('70000'),
        positions_lock=positions_lock,
    )


def test_no_positions_lock_accepts_any_risk_lock() -> None:
    state = _make_state()
    state.risk.lock = threading.Lock()

    MtmLoop(state=state, mark_price_provider=lambda _s: Decimal('70000'))


def test_bool_interval_rejected() -> None:
    state = _make_state()

    with pytest.raises(ValueError, match='interval_seconds must be a positive number'):
        MtmLoop(
            state=state,
            mark_price_provider=lambda _s: Decimal('0'),
            interval_seconds=True,  # type: ignore[arg-type]
        )


def test_no_positions_zeros_aggregate() -> None:
    state = _make_state()
    state.risk.unrealized_pnl = Decimal('123.45')

    loop = MtmLoop(
        state=state,
        mark_price_provider=lambda _s: Decimal('70000'),
    )
    loop.tick_once()

    assert state.risk.unrealized_pnl == Decimal('0')


def test_long_marked_above_entry_yields_positive_unrealized() -> None:
    state = _make_state()
    state.positions['t1'] = _make_position('t1', OrderSide.BUY, '0.5', '70000')

    loop = MtmLoop(state=state, mark_price_provider=lambda _s: Decimal('71000'))
    loop.tick_once()

    assert state.positions['t1'].unrealized_pnl == Decimal('500.0')
    assert state.risk.unrealized_pnl == Decimal('500.0')


def test_long_marked_below_entry_yields_negative_unrealized() -> None:
    state = _make_state()
    state.positions['t1'] = _make_position('t1', OrderSide.BUY, '1.0', '70000')

    loop = MtmLoop(state=state, mark_price_provider=lambda _s: Decimal('68000'))
    loop.tick_once()

    assert state.positions['t1'].unrealized_pnl == Decimal('-2000.0')
    assert state.risk.unrealized_pnl == Decimal('-2000.0')


def test_short_marked_below_entry_yields_positive_unrealized() -> None:
    state = _make_state()
    state.positions['t1'] = _make_position('t1', OrderSide.SELL, '0.5', '70000')

    loop = MtmLoop(state=state, mark_price_provider=lambda _s: Decimal('69000'))
    loop.tick_once()

    assert state.positions['t1'].unrealized_pnl == Decimal('500.0')
    assert state.risk.unrealized_pnl == Decimal('500.0')


def test_short_marked_above_entry_yields_negative_unrealized() -> None:
    state = _make_state()
    state.positions['t1'] = _make_position('t1', OrderSide.SELL, '0.5', '70000')

    loop = MtmLoop(state=state, mark_price_provider=lambda _s: Decimal('71500'))
    loop.tick_once()

    assert state.positions['t1'].unrealized_pnl == Decimal('-750.0')
    assert state.risk.unrealized_pnl == Decimal('-750.0')


def test_multiple_positions_aggregate_correctly() -> None:
    state = _make_state()
    state.positions['long_winner']  = _make_position('long_winner',  OrderSide.BUY, '1.0', '70000')
    state.positions['long_loser']   = _make_position('long_loser',   OrderSide.BUY, '0.5', '72000')
    state.positions['short_winner'] = _make_position('short_winner', OrderSide.SELL, '0.25', '74000')

    loop = MtmLoop(state=state, mark_price_provider=lambda _s: Decimal('71000'))
    loop.tick_once()

    assert state.positions['long_winner'].unrealized_pnl  == Decimal('1000.0')
    assert state.positions['long_loser'].unrealized_pnl   == Decimal('-500.0')
    assert state.positions['short_winner'].unrealized_pnl == Decimal('750.0')
    assert state.risk.unrealized_pnl == Decimal('1250.0')


def test_provider_returns_none_aborts_tick_without_partial_writes() -> None:
    state = _make_state()
    state.positions['t1'] = _make_position('t1', OrderSide.BUY, '1.0', '70000')
    state.positions['t1'].unrealized_pnl = Decimal('42')
    state.risk.unrealized_pnl = Decimal('999')

    loop = MtmLoop(state=state, mark_price_provider=lambda _s: None)
    loop.tick_once()

    assert state.positions['t1'].unrealized_pnl == Decimal('42')
    assert state.risk.unrealized_pnl == Decimal('999')


def test_provider_returns_non_finite_aborts_tick() -> None:
    state = _make_state()
    state.positions['t1'] = _make_position('t1', OrderSide.BUY, '1.0', '70000')
    state.positions['t1'].unrealized_pnl = Decimal('42')

    loop = MtmLoop(state=state, mark_price_provider=lambda _s: Decimal('Infinity'))
    loop.tick_once()

    assert state.positions['t1'].unrealized_pnl == Decimal('42')


def test_provider_exception_swallowed_loop_survives() -> None:
    state = _make_state()
    state.positions['t1'] = _make_position('t1', OrderSide.BUY, '1.0', '70000')
    state.positions['t1'].unrealized_pnl = Decimal('42')

    def failing_provider(_symbol: str) -> Decimal:
        raise RuntimeError('mark feed down')

    loop = MtmLoop(state=state, mark_price_provider=failing_provider)
    loop.tick_once()
    loop.tick_once()

    assert state.positions['t1'].unrealized_pnl == Decimal('42')


def test_drawdown_recomputed_after_mark() -> None:
    '''update_unrealized_pnl must call recompute_drawdown_metrics so
    equity / equity_hwm / total_drawdown stay coherent.
    '''

    state = _make_state()
    state.risk.starting_capital = Decimal('10000')
    state.risk.equity = Decimal('10000')
    state.risk.equity_hwm = Decimal('10000')
    state.positions['t1'] = _make_position('t1', OrderSide.BUY, '1.0', '70000')

    loop = MtmLoop(state=state, mark_price_provider=lambda _s: Decimal('69500'))
    loop.tick_once()

    assert state.risk.unrealized_pnl == Decimal('-500.0')
    assert state.risk.equity == Decimal('9500.0')
    assert state.risk.equity_hwm == Decimal('10000')
    assert state.risk.total_drawdown == Decimal('500.0')


def test_multi_symbol_provider_called_once_per_symbol() -> None:
    state = _make_state()
    state.positions['btc_a'] = _make_position('btc_a', OrderSide.BUY, '0.5', '70000', symbol='BTCUSDT')
    state.positions['btc_b'] = _make_position('btc_b', OrderSide.BUY, '0.25', '71000', symbol='BTCUSDT')
    state.positions['eth_a'] = _make_position('eth_a', OrderSide.BUY, '10', '3500', symbol='ETHUSDT')

    calls: list[str] = []
    marks = {'BTCUSDT': Decimal('72000'), 'ETHUSDT': Decimal('3600')}

    def counting_provider(symbol: str) -> Decimal:
        calls.append(symbol)
        return marks[symbol]

    loop = MtmLoop(state=state, mark_price_provider=counting_provider)
    loop.tick_once()

    assert sorted(calls) == ['BTCUSDT', 'ETHUSDT'], 'provider must be called exactly once per symbol'
    assert state.positions['btc_a'].unrealized_pnl == Decimal('1000.0')
    assert state.positions['btc_b'].unrealized_pnl == Decimal('250.0')
    assert state.positions['eth_a'].unrealized_pnl == Decimal('1000')
    assert state.risk.unrealized_pnl == Decimal('2250.0')


def test_provider_can_reacquire_risk_lock_without_deadlock() -> None:
    '''Phase B (mark fetch) must NOT hold positions_lock / state.risk.lock.

    The lock is non-reentrant; if MtmLoop held it across the provider
    call, any provider implementation that re-entered risk APIs (e.g.
    `state.risk.to_risk_check_metrics()` which acquires
    `state.risk.lock_cm()`) would deadlock. This test wires a
    provider that explicitly acquires state.risk.lock and asserts
    tick_once() returns inside a small wall-clock budget.
    '''

    state = _make_state()
    state.positions['t1'] = _make_position('t1', OrderSide.BUY, '1.0', '70000')
    positions_lock = threading.Lock()
    state.risk.lock = positions_lock

    provider_observed_lock_held: list[bool] = []

    def reentrant_provider(_symbol: str) -> Decimal:
        provider_observed_lock_held.append(positions_lock.locked())
        with state.risk.lock_cm():
            return Decimal('71000')

    loop = MtmLoop(
        state=state,
        mark_price_provider=reentrant_provider,
        positions_lock=positions_lock,
    )

    completed = threading.Event()

    def run_tick() -> None:
        loop.tick_once()
        completed.set()

    worker = threading.Thread(target=run_tick, daemon=True)
    worker.start()
    finished = completed.wait(timeout=3)
    worker.join(timeout=1)

    assert finished, 'tick_once() did not complete within 3s — provider deadlocked on positions_lock'
    assert not worker.is_alive()
    assert provider_observed_lock_held == [False], (
        'provider observed positions_lock held during its call; '
        'lock must be released before Phase B mark fetch'
    )
    assert state.positions['t1'].unrealized_pnl == Decimal('1000.0')


def test_provider_called_without_positions_lock() -> None:
    '''Pin the deadlock-safety contract: the mark_price_provider is
    invoked OUTSIDE positions_lock (Phase B). A provider that
    re-enters risk APIs needing state.risk.lock can do so safely
    because the lock is released before the call.
    '''

    state = _make_state()
    state.positions['t1'] = _make_position('t1', OrderSide.BUY, '1.0', '70000')
    positions_lock = threading.Lock()
    state.risk.lock = positions_lock

    lock_held_during_provider: list[bool] = []

    def spying_provider(_symbol: str) -> Decimal:
        lock_held_during_provider.append(positions_lock.locked())
        return Decimal('71000')

    loop = MtmLoop(
        state=state,
        mark_price_provider=spying_provider,
        positions_lock=positions_lock,
    )
    loop.tick_once()

    assert lock_held_during_provider == [False]
    assert state.positions['t1'].unrealized_pnl == Decimal('1000.0')


def test_start_stop_is_idempotent() -> None:
    state = _make_state()
    loop = MtmLoop(
        state=state,
        mark_price_provider=lambda _s: Decimal('70000'),
        interval_seconds=60,
    )

    assert not loop.running

    loop.start()
    assert loop.running
    loop.start()
    assert loop.running

    loop.stop()
    assert not loop.running
    loop.stop()
    assert not loop.running


def test_per_strategy_unrealized_attributed_correctly() -> None:
    '''Each StrategyRiskState.strategy_unrealized_pnl must reflect
    only the open positions belonging to that strategy.
    '''

    state = _make_state()
    p_a1 = _make_position('a1', OrderSide.BUY, '1.0', '70000')
    p_a1.strategy_id = 'strat_A'
    p_a2 = _make_position('a2', OrderSide.BUY, '0.5', '70000')
    p_a2.strategy_id = 'strat_A'
    p_b = _make_position('b1', OrderSide.BUY, '2.0', '72000')
    p_b.strategy_id = 'strat_B'
    state.positions = {'a1': p_a1, 'a2': p_a2, 'b1': p_b}

    loop = MtmLoop(state=state, mark_price_provider=lambda _s: Decimal('71000'))
    loop.tick_once()

    assert state.risk.per_strategy['strat_A'].strategy_unrealized_pnl == Decimal('1500.0')
    assert state.risk.per_strategy['strat_B'].strategy_unrealized_pnl == Decimal('-2000.0')
    assert state.risk.unrealized_pnl == Decimal('-500.0')


def test_no_positions_branch_recomputes_drawdown_when_per_strategy_was_nonzero() -> None:
    '''A tick where `state.positions` is empty AND the aggregate
    `state.risk.unrealized_pnl` is already zero but a per-strategy
    entry still carries a non-zero `strategy_unrealized_pnl` must
    still call `update_unrealized_pnl(_ZERO)` so drawdown metrics
    stay coherent. Pre-fix the per-strategy zeroing happened but the
    drawdown recompute was gated on aggregate-already-non-zero only.
    '''

    state = _make_state()
    state.risk.starting_capital = Decimal('10000')
    state.risk.equity = Decimal('10500')
    state.risk.equity_hwm = Decimal('10500')
    state.risk.unrealized_pnl = Decimal('0')
    state.risk.per_strategy['strat_stale'] = StrategyRiskState(
        strategy_id='strat_stale',
        strategy_unrealized_pnl=Decimal('500'),
    )

    update_calls: list[Decimal] = []
    original_update = state.risk.update_unrealized_pnl

    def spy_update(value: Decimal) -> None:
        update_calls.append(value)
        original_update(value)

    state.risk.update_unrealized_pnl = spy_update  # type: ignore[method-assign]

    loop = MtmLoop(state=state, mark_price_provider=lambda _s: Decimal('70000'))
    loop.tick_once()

    assert update_calls == [Decimal('0')], (
        'update_unrealized_pnl(_ZERO) must fire so drawdown recomputes '
        'when per-strategy unrealized was non-zero, even if aggregate was zero'
    )
    assert state.risk.per_strategy['strat_stale'].strategy_unrealized_pnl == Decimal('0')


def test_per_strategy_unrealized_zeroes_when_strategy_has_no_open_positions() -> None:
    '''A strategy that previously carried open unrealized but now has
    no open positions must zero out — stale values become silent risk.
    '''

    state = _make_state()
    state.risk.per_strategy['strat_stale'] = StrategyRiskState(
        strategy_id='strat_stale',
        strategy_unrealized_pnl=Decimal('555'),
    )
    p_live = _make_position('live', OrderSide.BUY, '1.0', '70000')
    p_live.strategy_id = 'strat_live'
    state.positions['live'] = p_live

    loop = MtmLoop(state=state, mark_price_provider=lambda _s: Decimal('71000'))
    loop.tick_once()

    assert state.risk.per_strategy['strat_live'].strategy_unrealized_pnl == Decimal('1000.0')
    assert state.risk.per_strategy['strat_stale'].strategy_unrealized_pnl == Decimal('0')


def test_no_positions_zeroes_per_strategy_unrealized() -> None:
    state = _make_state()
    state.risk.unrealized_pnl = Decimal('999')
    state.risk.per_strategy['strat_X'] = StrategyRiskState(
        strategy_id='strat_X',
        strategy_unrealized_pnl=Decimal('123'),
    )

    loop = MtmLoop(state=state, mark_price_provider=lambda _s: Decimal('70000'))
    loop.tick_once()

    assert state.risk.unrealized_pnl == Decimal('0')
    assert state.risk.per_strategy['strat_X'].strategy_unrealized_pnl == Decimal('0')


def test_mtm_creates_strategyriskstate_on_demand_for_open_position_with_no_prior_record() -> None:
    '''A strategy that opened a position but never closed one yet has
    no StrategyRiskState entry (OutcomeProcessor creates it lazily on
    first exit). The MTM loop must create it on demand to write the
    unrealized; otherwise per-strategy unrealized for never-exited
    strategies is silently dropped.
    '''

    state = _make_state()
    p = _make_position('t1', OrderSide.BUY, '1.0', '70000')
    p.strategy_id = 'strat_new'
    state.positions['t1'] = p

    assert 'strat_new' not in state.risk.per_strategy

    loop = MtmLoop(state=state, mark_price_provider=lambda _s: Decimal('71500'))
    loop.tick_once()

    assert 'strat_new' in state.risk.per_strategy
    assert state.risk.per_strategy['strat_new'].strategy_unrealized_pnl == Decimal('1500.0')


def test_periodic_tick_fires_off_timer() -> None:
    state = _make_state()
    state.positions['t1'] = _make_position('t1', OrderSide.BUY, '1.0', '70000')

    tick_seen = threading.Event()

    def spying_provider(_symbol: str) -> Decimal:
        tick_seen.set()
        return Decimal('71000')

    loop = MtmLoop(
        state=state,
        mark_price_provider=spying_provider,
        interval_seconds=0.05,
    )

    loop.start()
    try:
        fired = tick_seen.wait(timeout=2.0)
    finally:
        loop.stop()

    assert fired, 'periodic timer must invoke provider at least once within 2s at 0.05s interval'
