'''Verify WAL codec round-trip serialization for InstanceState and StrategyEvent.'''

from __future__ import annotations

import threading
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, cast

import msgpack
import pytest

from nexus.core.domain.capital_state import CapitalState
from nexus.core.domain.enums import OperationalMode, OrderSide
from nexus.core.domain.instance_state import InstanceState
from nexus.core.domain.operational_mode import HaltHold, ModeState, StrategyModeState
from nexus.core.domain.position import Position
from nexus.core.domain.risk_state import RiskState, StrategyRiskState
from nexus.infrastructure import wal_codec
from nexus.infrastructure.strategy_event import StrategyEvent
from nexus.infrastructure.wal_codec import (
    deserialize_event,
    deserialize_state,
    serialize_event,
    serialize_state,
)


def _make_minimal_state() -> InstanceState:
    '''Build a minimal InstanceState with only capital.'''

    return InstanceState(capital=CapitalState(capital_pool=Decimal('10000')))


def _make_full_state() -> InstanceState:
    '''Build a fully populated InstanceState with all fields non-default.'''

    return InstanceState(
        capital=CapitalState(
            capital_pool=Decimal('100000'),
            position_notional=Decimal('25000.50'),
            working_order_notional=Decimal('5000'),
            in_flight_order_notional=Decimal('1000.75'),
            fee_reserve=Decimal('200'),
            reservation_notional=Decimal('3000'),
            per_strategy_deployed={
                'momentum': Decimal('21000.5'),
                'mean_rev': Decimal('8000.25'),
            },
        ),
        risk=RiskState(
            high_water_mark=Decimal('110000'),
            starting_capital=Decimal('100000'),
            cumulative_realized_pnl=Decimal('450.25'),
            unrealized_pnl=Decimal('-125.75'),
            equity=Decimal('100324.50'),
            equity_hwm=Decimal('111000'),
            realized_equity_hwm=Decimal('108000'),
            total_drawdown=Decimal('10675.50'),
            total_drawdown_pct=Decimal('0.09617567567567567567567567568'),
            realized_drawdown=Decimal('7550.25'),
            unrealized_drawdown=Decimal('125.75'),
            max_drawdown=Decimal('20000'),
            max_drawdown_pct=Decimal('0.18'),
            per_strategy={
                'momentum': StrategyRiskState(
                    strategy_id='momentum',
                    high_water_mark=Decimal('60000'),
                    rolling_loss_24h=Decimal('150.25'),
                    rolling_loss_7d=Decimal('800'),
                    rolling_loss_30d=Decimal('2500'),
                    strategy_realized_pnl=Decimal('-500.75'),
                ),
            },
        ),
        positions={
            't1': Position(
                trade_id='t1',
                strategy_id='momentum',
                symbol='BTCUSDT',
                side=OrderSide.BUY,
                size=Decimal('0.5'),
                entry_price=Decimal('50000'),
                unrealized_pnl=Decimal('1250.50'),
                pending_exit=Decimal('0.25'),
                avg_cost_basis=Decimal('50500'),
            ),
            't2': Position(
                trade_id='t2',
                strategy_id='momentum',
                symbol='ETHUSDT',
                side=OrderSide.SELL,
                size=Decimal('10'),
                entry_price=Decimal('3000'),
                unrealized_pnl=Decimal('-200'),
            ),
        },
        mode=ModeState(
            mode=OperationalMode.REDUCE_ONLY,
            trigger='risk_breach',
            transitioned_at=datetime(2025, 6, 15, 14, 30, 0),
        ),
        strategy_modes={
            'momentum': StrategyModeState(
                strategy_id='momentum',
                state=ModeState(
                    mode=OperationalMode.HALTED,
                    trigger='manual_halt',
                    transitioned_at=datetime(2025, 6, 15, 15, 0, 0),
                ),
            ),
        },
    )


class TestRoundTrip:
    '''Verify serialize → deserialize produces identical state.'''

    def test_minimal_state(self) -> None:
        '''Verify round-trip for minimal (defaults-only) InstanceState.'''

        original = _make_minimal_state()
        restored = deserialize_state(serialize_state(original))

        assert restored.capital.capital_pool == original.capital.capital_pool
        assert restored.capital.position_notional == Decimal(0)
        assert restored.risk.high_water_mark == Decimal(0)
        assert restored.positions == {}
        assert restored.mode.mode == OperationalMode.ACTIVE
        assert restored.strategy_modes == {}

    def test_full_state_capital(self) -> None:
        '''Verify capital fields survive round-trip.'''

        original = _make_full_state()
        restored = deserialize_state(serialize_state(original))

        assert restored.capital.capital_pool == original.capital.capital_pool
        assert restored.capital.position_notional == original.capital.position_notional
        assert (
            restored.capital.working_order_notional
            == original.capital.working_order_notional
        )
        assert (
            restored.capital.in_flight_order_notional
            == original.capital.in_flight_order_notional
        )
        assert restored.capital.fee_reserve == original.capital.fee_reserve
        assert (
            restored.capital.reservation_notional
            == original.capital.reservation_notional
        )
        assert (
            restored.capital.per_strategy_deployed
            == original.capital.per_strategy_deployed
        )

    def test_full_state_risk(self) -> None:
        '''Verify risk fields survive round-trip.'''

        original = _make_full_state()
        restored = deserialize_state(serialize_state(original))

        assert restored.risk.high_water_mark == original.risk.high_water_mark
        assert restored.risk.starting_capital == original.risk.starting_capital
        assert (
            restored.risk.cumulative_realized_pnl
            == original.risk.cumulative_realized_pnl
        )
        assert restored.risk.unrealized_pnl == original.risk.unrealized_pnl
        assert restored.risk.equity == original.risk.equity
        assert restored.risk.equity_hwm == original.risk.equity_hwm
        assert restored.risk.realized_equity_hwm == original.risk.realized_equity_hwm
        assert restored.risk.total_drawdown == original.risk.total_drawdown
        assert restored.risk.total_drawdown_pct == original.risk.total_drawdown_pct
        assert restored.risk.realized_drawdown == original.risk.realized_drawdown
        assert restored.risk.unrealized_drawdown == original.risk.unrealized_drawdown
        assert restored.risk.max_drawdown == original.risk.max_drawdown
        assert restored.risk.max_drawdown_pct == original.risk.max_drawdown_pct
        assert 'momentum' in restored.risk.per_strategy

        orig_srs = original.risk.per_strategy['momentum']
        rest_srs = restored.risk.per_strategy['momentum']
        assert rest_srs.strategy_id == orig_srs.strategy_id
        assert rest_srs.high_water_mark == orig_srs.high_water_mark
        assert rest_srs.rolling_loss_24h == orig_srs.rolling_loss_24h
        assert rest_srs.rolling_loss_7d == orig_srs.rolling_loss_7d
        assert rest_srs.rolling_loss_30d == orig_srs.rolling_loss_30d
        assert rest_srs.strategy_realized_pnl == orig_srs.strategy_realized_pnl

    def test_full_state_positions(self) -> None:
        '''Verify position fields survive round-trip.'''

        original = _make_full_state()
        restored = deserialize_state(serialize_state(original))

        assert set(restored.positions.keys()) == {'t1', 't2'}

        for tid in ('t1', 't2'):
            orig_pos = original.positions[tid]
            rest_pos = restored.positions[tid]
            assert rest_pos.trade_id == orig_pos.trade_id
            assert rest_pos.strategy_id == orig_pos.strategy_id
            assert rest_pos.symbol == orig_pos.symbol
            assert rest_pos.side == orig_pos.side
            assert rest_pos.size == orig_pos.size
            assert rest_pos.entry_price == orig_pos.entry_price
            assert rest_pos.unrealized_pnl == orig_pos.unrealized_pnl
            assert rest_pos.pending_exit == orig_pos.pending_exit
            assert rest_pos.avg_cost_basis == orig_pos.avg_cost_basis

    def test_legacy_position_decode_defaults_avg_cost_basis_to_entry_price(self) -> None:
        '''Pre-fix snapshots written without avg_cost_basis must decode to
        entry_price (best-effort default — fees lost but recoverable).'''

        from nexus.infrastructure.wal_codec import _decode_position

        legacy_dict = {
            'trade_id': 't_legacy',
            'strategy_id': 'momentum',
            'symbol': 'BTCUSDT',
            'side': OrderSide.BUY.value,
            'size': '0.5',
            'entry_price': '50000',
            'unrealized_pnl': '0',
            'pending_exit': '0',
        }
        pos = _decode_position(legacy_dict)
        assert pos.avg_cost_basis == Decimal('50000')

    def test_full_state_mode(self) -> None:
        '''Verify mode fields survive round-trip.'''

        original = _make_full_state()
        restored = deserialize_state(serialize_state(original))

        assert restored.mode.mode == original.mode.mode
        assert restored.mode.trigger == original.mode.trigger
        assert restored.mode.transitioned_at == original.mode.transitioned_at

    def test_full_state_strategy_modes(self) -> None:
        '''Verify strategy mode fields survive round-trip.'''

        original = _make_full_state()
        restored = deserialize_state(serialize_state(original))

        assert 'momentum' in restored.strategy_modes
        orig_sm = original.strategy_modes['momentum']
        rest_sm = restored.strategy_modes['momentum']
        assert rest_sm.strategy_id == orig_sm.strategy_id
        assert rest_sm.state.mode == orig_sm.state.mode
        assert rest_sm.state.trigger == orig_sm.state.trigger
        assert rest_sm.state.transitioned_at == orig_sm.state.transitioned_at


class TestDecimalPrecision:
    '''Verify Decimal precision is not lost through serialization.'''

    def test_high_precision_decimal(self) -> None:
        '''Verify Decimal with many significant digits survives round-trip.'''

        state = InstanceState(
            capital=CapitalState(capital_pool=Decimal('99999.123456789012345678')),
        )
        restored = deserialize_state(serialize_state(state))
        assert restored.capital.capital_pool == Decimal('99999.123456789012345678')

    def test_negative_pnl_preserved(self) -> None:
        '''Verify negative realized PnL survives round-trip.'''

        state = InstanceState(
            capital=CapitalState(capital_pool=Decimal('10000')),
            risk=RiskState(
                per_strategy={
                    'arb': StrategyRiskState(
                        strategy_id='arb',
                        strategy_realized_pnl=Decimal('-12345.6789'),
                    ),
                },
            ),
        )
        restored = deserialize_state(serialize_state(state))
        assert restored.risk.per_strategy['arb'].strategy_realized_pnl == Decimal(
            '-12345.6789'
        )


class TestCodecVersioning:
    '''Verify codec version enforcement.'''

    def test_unsupported_version_rejected(self) -> None:
        '''Verify deserialize rejects unknown codec version.'''

        bad_data = cast(bytes, msgpack.packb({'_v': 999}))
        with pytest.raises(ValueError, match='Unsupported WAL codec version'):
            deserialize_state(bad_data)

    def test_missing_version_defaults_to_v1(self) -> None:
        '''Verify deserialize treats missing _v as v1 for backward compatibility.'''

        state = InstanceState(capital=CapitalState(capital_pool=Decimal('10000')))
        v1_data = cast(bytes, serialize_state(state))

        d = msgpack.unpackb(v1_data, raw=False)
        del d['_v']
        no_version_data = cast(bytes, msgpack.packb(d))

        recovered = deserialize_state(no_version_data)
        assert recovered.capital.capital_pool == Decimal('10000')


class TestRiskDecodeDefaults:
    '''Verify risk decode defaults for missing fields.'''

    def test_missing_risk_fields_seed_from_high_water_mark(self) -> None:
        '''Verify missing risk fields default from high_water_mark.'''

        payload = {
            '_v': 1,
            'capital': {
                'capital_pool': '100000',
                'position_notional': '0',
                'working_order_notional': '0',
                'in_flight_order_notional': '0',
                'fee_reserve': '0',
                'reservation_notional': '0',
            },
            'risk': {
                'high_water_mark': '110000',
                'per_strategy': {},
            },
            'positions': {},
            'mode': {
                'mode': 'ACTIVE',
                'trigger': 'init',
                'transitioned_at': '2026-03-20T00:00:00',
            },
            'strategy_modes': {},
        }

        restored = deserialize_state(cast(bytes, msgpack.packb(payload)))

        assert restored.risk.high_water_mark == Decimal('110000')
        assert restored.risk.starting_capital == Decimal('110000')
        assert restored.risk.equity == Decimal('110000')
        assert restored.risk.equity_hwm == Decimal('110000')
        assert restored.risk.realized_equity_hwm == Decimal('110000')
        assert restored.risk.total_drawdown_pct == Decimal('0')
        assert restored.risk.max_drawdown == Decimal('0')
        assert restored.risk.max_drawdown_pct == Decimal('0')
        assert restored.capital.per_strategy_deployed == {}


class TestMalformedPayload:
    '''Verify deserialize_state rejects non-dict payloads.'''

    def test_non_dict_payload_raises(self) -> None:
        '''Verify non-dict msgpack payload raises ValueError.'''

        bad_data = cast(bytes, msgpack.packb([1, 2, 3]))

        with pytest.raises(ValueError, match='Expected dict from WAL payload'):
            deserialize_state(bad_data)

    def test_invalid_decimal_in_risk_payload_raises(self) -> None:
        '''Verify malformed Decimal risk field raises normalized ValueError.'''

        payload = {
            '_v': 1,
            'capital': {
                'capital_pool': '100000',
                'position_notional': '0',
                'working_order_notional': '0',
                'in_flight_order_notional': '0',
                'fee_reserve': '0',
                'reservation_notional': '0',
            },
            'risk': {
                'high_water_mark': 'not_a_number',
                'per_strategy': {},
            },
            'positions': {},
            'mode': {
                'mode': 'ACTIVE',
                'trigger': 'init',
                'transitioned_at': '2026-03-20T00:00:00',
            },
            'strategy_modes': {},
        }

        data = cast(bytes, msgpack.packb(payload))

        with pytest.raises(ValueError, match='Malformed WAL codec payload'):
            deserialize_state(data)


class TestSerializationOutput:
    '''Verify serialization produces expected binary format.'''

    def test_output_is_bytes(self) -> None:
        '''Verify serialize_state returns bytes.'''

        state = _make_minimal_state()
        result = serialize_state(state)
        assert isinstance(result, bytes)

    def test_output_is_non_empty(self) -> None:
        '''Verify serialized output is non-empty.'''

        state = _make_minimal_state()
        result = serialize_state(state)
        assert len(result) > 0

    def test_output_is_valid_msgpack(self) -> None:
        '''Verify serialized bytes are valid msgpack.'''

        state = _make_minimal_state()
        result = serialize_state(state)
        unpacked = msgpack.unpackb(result, raw=False)
        assert isinstance(unpacked, dict)
        assert unpacked['_v'] == 1


def _make_event() -> StrategyEvent:
    return StrategyEvent(
        strategy_id='strat_a',
        event_type='trade_outcome',
        realized_pnl=Decimal('-50.25'),
        timestamp=datetime(2026, 3, 19, 12, 0, 0, tzinfo=timezone.utc),
    )


class TestEventRoundTrip:
    def test_basic_round_trip(self) -> None:
        event = _make_event()
        data = serialize_event(event)
        recovered = deserialize_event(data)
        assert recovered.strategy_id == event.strategy_id
        assert recovered.event_type == event.event_type
        assert recovered.realized_pnl == event.realized_pnl
        assert recovered.timestamp == event.timestamp

    def test_decimal_precision_preserved(self) -> None:
        event = StrategyEvent(
            strategy_id='strat_b',
            event_type='trade_outcome',
            realized_pnl=Decimal('123.456789012345678901234567890'),
            timestamp=datetime(2026, 3, 19, 12, 0, 0, tzinfo=timezone.utc),
        )
        recovered = deserialize_event(serialize_event(event))
        assert recovered.realized_pnl == event.realized_pnl

    def test_negative_pnl_round_trip(self) -> None:
        event = StrategyEvent(
            strategy_id='strat_a',
            event_type='trade_outcome',
            realized_pnl=Decimal('-999.99'),
            timestamp=datetime(2026, 3, 19, 12, 0, 0, tzinfo=timezone.utc),
        )
        recovered = deserialize_event(serialize_event(event))
        assert recovered.realized_pnl == Decimal('-999.99')

    def test_zero_pnl_round_trip(self) -> None:
        event = StrategyEvent(
            strategy_id='strat_a',
            event_type='trade_outcome',
            realized_pnl=Decimal('0'),
            timestamp=datetime(2026, 3, 19, 12, 0, 0, tzinfo=timezone.utc),
        )
        recovered = deserialize_event(serialize_event(event))
        assert recovered.realized_pnl == Decimal('0')


class TestEventCodecVersion:
    def test_version_embedded_v1_when_outcome_id_empty(self) -> None:
        '''PR #55 review: an event without `outcome_id` is encoded as
        v1 so the strict v2 decoder never sees an empty / missing key.
        FINAL-TD-02 added `outcome_id` for `derive_rolling_losses`
        dedup of Praxis re-deliveries; events that predate that field
        keep the legacy v1 contract (no dedup).
        '''

        data = serialize_event(_make_event())
        unpacked = msgpack.unpackb(data, raw=False)
        assert unpacked['_v'] == 1
        assert 'outcome_id' not in unpacked

    def test_version_embedded_v2_when_outcome_id_present(self) -> None:
        '''Events with a non-empty `outcome_id` encode as v2 so the
        recovery deduper has a stable key.
        '''

        event = StrategyEvent(
            strategy_id='strat_a',
            event_type='trade_outcome',
            realized_pnl=Decimal('-50.25'),
            timestamp=datetime(2026, 3, 19, 12, 0, 0, tzinfo=timezone.utc),
            outcome_id='outcome-abc-123',
        )
        data = serialize_event(event)
        unpacked = msgpack.unpackb(data, raw=False)
        assert unpacked['_v'] == 2
        assert unpacked['outcome_id'] == 'outcome-abc-123'

    def test_wrong_version_rejected(self) -> None:
        d = {
            '_v': 99,
            'strategy_id': 'strat_a',
            'event_type': 'trade_outcome',
            'realized_pnl': '0',
            'timestamp': '2026-03-19T12:00:00+00:00',
        }
        data = cast(bytes, msgpack.packb(d))

        with pytest.raises(ValueError, match='Unsupported event codec version'):
            deserialize_event(data)


class TestEventMalformedPayload:
    def test_non_dict_rejected(self) -> None:
        data = cast(bytes, msgpack.packb([1, 2, 3]))

        with pytest.raises(ValueError, match='Expected dict from event payload'):
            deserialize_event(data)

    def test_missing_field_rejected(self) -> None:
        d = {'_v': 1, 'strategy_id': 'strat_a'}
        data = cast(bytes, msgpack.packb(d))

        with pytest.raises(ValueError, match='Malformed event codec payload'):
            deserialize_event(data)

    def test_invalid_decimal_rejected(self) -> None:
        d = {
            '_v': 1,
            'strategy_id': 'strat_a',
            'event_type': 'trade_outcome',
            'realized_pnl': 'not_a_number',
            'timestamp': '2026-03-19T12:00:00+00:00',
        }
        data = cast(bytes, msgpack.packb(d))

        with pytest.raises(ValueError, match='Malformed event codec payload'):
            deserialize_event(data)

    def test_invalid_timestamp_rejected(self) -> None:
        d = {
            '_v': 1,
            'strategy_id': 'strat_a',
            'event_type': 'trade_outcome',
            'realized_pnl': '100',
            'timestamp': 'not-a-date',
        }
        data = cast(bytes, msgpack.packb(d))

        with pytest.raises(ValueError, match='Malformed event codec payload'):
            deserialize_event(data)

    def test_v2_missing_outcome_id_rejected(self) -> None:
        '''PR #55 review: v2 payloads without `outcome_id` must hard-fail
        instead of silently decoding with `outcome_id=''` (which would
        disable dedup and reintroduce double-counting on WAL corruption).
        '''

        d = {
            '_v': 2,
            'strategy_id': 'strat_a',
            'event_type': 'trade_outcome',
            'realized_pnl': '100',
            'timestamp': '2026-03-19T12:00:00+00:00',
        }
        data = cast(bytes, msgpack.packb(d))

        with pytest.raises(ValueError, match='Malformed event codec payload'):
            deserialize_event(data)

    def test_v2_empty_outcome_id_rejected(self) -> None:
        '''PR #55 review: v2 payloads with empty-string `outcome_id`
        must hard-fail — empty strings would collide in the dedup set
        across distinct outcomes, defeating dedup entirely.
        '''

        d = {
            '_v': 2,
            'strategy_id': 'strat_a',
            'event_type': 'trade_outcome',
            'realized_pnl': '100',
            'timestamp': '2026-03-19T12:00:00+00:00',
            'outcome_id': '',
        }
        data = cast(bytes, msgpack.packb(d))

        with pytest.raises(ValueError, match='Malformed event codec payload'):
            deserialize_event(data)

    def test_v2_non_string_outcome_id_rejected(self) -> None:
        '''PR #55 review: v2 payloads with non-string `outcome_id`
        (e.g. accidental int from upstream serializer drift) must
        hard-fail rather than silently coerce.
        '''

        d = {
            '_v': 2,
            'strategy_id': 'strat_a',
            'event_type': 'trade_outcome',
            'realized_pnl': '100',
            'timestamp': '2026-03-19T12:00:00+00:00',
            'outcome_id': 12345,
        }
        data = cast(bytes, msgpack.packb(d))

        with pytest.raises(ValueError, match='Malformed event codec payload'):
            deserialize_event(data)


class TestEventSerializationOutput:
    def test_output_is_bytes(self) -> None:
        result = serialize_event(_make_event())
        assert isinstance(result, bytes)

    def test_output_is_non_empty(self) -> None:
        result = serialize_event(_make_event())
        assert len(result) > 0

    def test_output_is_valid_msgpack(self) -> None:
        result = serialize_event(_make_event())
        unpacked = msgpack.unpackb(result, raw=False)
        assert isinstance(unpacked, dict)


def _anchor_position(trade_id: str) -> Position:
    '''Build a Position with `trade_id` and otherwise minimal fields.'''

    return Position(
        trade_id=trade_id,
        strategy_id=f'{trade_id}_strat',
        symbol='BTCUSDT',
        side=OrderSide.BUY,
        size=Decimal('0.001'),
        entry_price=Decimal('70000'),
        unrealized_pnl=Decimal('0'),
    )


class TestSerializeStateConcurrentMutation:
    '''Pin: `serialize_state` must not raise
    `RuntimeError: dictionary changed size during iteration` when
    another thread mutates `state.positions` concurrently.

    The v0.53.0-era prod hit this exact failure path: an
    OutcomeProcessor / dispatch thread was inserting/popping into
    `state.positions` while `state_store.append_mutation(state)` was
    iterating the live dict inside `serialize_state`. The error
    propagated, the outcome ack was withheld (by design), and the
    instance state-machine demoted the operational mode to
    `REDUCE_ONLY` — which then blocked every subsequent ENTER for
    ~26 h until the next epoch reset.

    The fix snapshots `positions` and `strategy_modes` via `dict()`
    inside `serialize_state` before iterating; `dict()` is a single
    C-level operation under the GIL and is safe against Python-level
    mutations from other threads.
    '''

    def test_serialize_does_not_observe_mid_iteration_position_insert(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        '''An insert into `state.positions` that lands mid-iteration
        of the encode comprehension must not trip the dict-mutation
        guard, and the resulting payload must reflect the pre-insert
        snapshot only.

        The test wraps `_encode_position` with a barrier that suspends
        the encoder during the **first** position encode; a worker
        thread then inserts a fresh position into `state.positions`
        and releases the barrier. Without the `dict(state.positions)`
        snapshot in `serialize_state`, the comprehension would iterate
        the live dict and the next item-view step after the worker's
        insert would raise
        `RuntimeError: dictionary changed size during iteration`,
        which propagates out of `serialize_state` and fails the test.
        With the snapshot, the comprehension iterates the copy taken
        before the encoder enters the barrier, so the worker's insert
        is invisible to the payload.
        '''

        state = _make_minimal_state()

        for i in range(5):
            state.positions[f'anchor_{i}'] = _anchor_position(f'anchor_{i}')

        original_encode = wal_codec._encode_position
        encoder_inside_barrier = threading.Event()
        encoder_release_barrier = threading.Event()
        encode_call_count = 0

        def barriered_encode(position: Position) -> dict[str, str]:
            nonlocal encode_call_count
            encode_call_count += 1

            if encode_call_count == 1:
                encoder_inside_barrier.set()
                assert encoder_release_barrier.wait(timeout=2), (
                    'mutator did not release the barrier within 2s'
                )

            return original_encode(position)

        monkeypatch.setattr(wal_codec, '_encode_position', barriered_encode)

        mutator_done = threading.Event()
        worker_errors: list[str] = []

        def mutator() -> None:
            if not encoder_inside_barrier.wait(timeout=2):
                worker_errors.append('encoder did not reach barrier within 2s')
                return

            state.positions['injected_after_iter_started'] = _anchor_position(
                'injected_after_iter_started'
            )
            encoder_release_barrier.set()
            mutator_done.set()

        worker = threading.Thread(target=mutator, daemon=True)
        worker.start()

        payload = serialize_state(state)

        worker.join(timeout=2)

        assert not worker.is_alive(), 'mutator thread did not exit within 2s'
        assert worker_errors == [], f'mutator reported errors: {worker_errors}'
        assert mutator_done.is_set()

        decoded = deserialize_state(payload)

        assert 'injected_after_iter_started' not in decoded.positions
        assert sorted(decoded.positions) == [f'anchor_{i}' for i in range(5)]

    def test_serialize_does_not_observe_mid_iteration_strategy_modes_insert(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        '''Symmetric guarantee for `state.strategy_modes`: the snapshot
        at `serialize_state` covers strategy_modes as well as positions,
        and an insert that lands while `_encode_strategy_mode_state` is
        mid-iteration must not trip the dict-mutation guard or appear in
        the decoded payload. Without the `dict(state.strategy_modes)`
        snapshot, removing it for strategy_modes alone (while leaving the
        positions snapshot intact) would silently slip past the
        positions-only test.
        '''

        state = _make_minimal_state()

        for i in range(5):
            state.strategy_modes[f'anchor_strat_{i}'] = StrategyModeState(
                strategy_id=f'anchor_strat_{i}',
            )

        original_encode = wal_codec._encode_strategy_mode_state
        encoder_inside_barrier = threading.Event()
        encoder_release_barrier = threading.Event()
        encode_call_count = 0

        def barriered_encode(sms: StrategyModeState) -> dict[str, Any]:
            nonlocal encode_call_count
            encode_call_count += 1

            if encode_call_count == 1:
                encoder_inside_barrier.set()
                assert encoder_release_barrier.wait(timeout=2), (
                    'mutator did not release the barrier within 2s'
                )

            return original_encode(sms)

        monkeypatch.setattr(wal_codec, '_encode_strategy_mode_state', barriered_encode)

        mutator_done = threading.Event()
        worker_errors: list[str] = []

        def mutator() -> None:
            if not encoder_inside_barrier.wait(timeout=2):
                worker_errors.append('encoder did not reach barrier within 2s')
                return

            state.strategy_modes['injected_after_iter_started'] = StrategyModeState(
                strategy_id='injected_after_iter_started',
            )
            encoder_release_barrier.set()
            mutator_done.set()

        worker = threading.Thread(target=mutator, daemon=True)
        worker.start()

        payload = serialize_state(state)

        worker.join(timeout=2)

        assert not worker.is_alive(), 'mutator thread did not exit within 2s'
        assert worker_errors == [], f'mutator reported errors: {worker_errors}'
        assert mutator_done.is_set()

        decoded = deserialize_state(payload)

        assert 'injected_after_iter_started' not in decoded.strategy_modes
        assert sorted(decoded.strategy_modes) == [f'anchor_strat_{i}' for i in range(5)]

    def test_serialize_does_not_observe_mid_iteration_risk_per_strategy_insert(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        '''Symmetric guarantee for `state.risk.per_strategy`. The
        v0.53.1 hotfix originally snapshotted only the two top-level
        `state.positions` / `state.strategy_modes` dicts; PR #75 review
        surfaced that `_encode_risk_state` and `_encode_capital_state`
        also iterate per-strategy dicts inside `serialize_state` and
        run without `positions_lock` / `CapitalController._lock` since
        `wal_lock` is innermost in the lock chain. This test pins the
        snapshot inside `_encode_risk_state` using the same barrier
        pattern as the positions / strategy_modes tests: monkey-patch
        `_encode_strategy_risk_state` to block during the first call,
        worker thread inserts a fresh StrategyRiskState into
        `state.risk.per_strategy` and releases the barrier, decoded
        payload must omit the injected key.
        '''

        state = _make_minimal_state()

        for i in range(5):
            state.risk.per_strategy[f'anchor_strat_{i}'] = StrategyRiskState(
                strategy_id=f'anchor_strat_{i}',
            )

        original_encode = wal_codec._encode_strategy_risk_state
        encoder_inside_barrier = threading.Event()
        encoder_release_barrier = threading.Event()
        encode_call_count = 0

        def barriered_encode(srs: StrategyRiskState) -> dict[str, str]:
            nonlocal encode_call_count
            encode_call_count += 1

            if encode_call_count == 1:
                encoder_inside_barrier.set()
                assert encoder_release_barrier.wait(timeout=2), (
                    'mutator did not release the barrier within 2s'
                )

            return original_encode(srs)

        monkeypatch.setattr(wal_codec, '_encode_strategy_risk_state', barriered_encode)

        mutator_done = threading.Event()
        worker_errors: list[str] = []

        def mutator() -> None:
            if not encoder_inside_barrier.wait(timeout=2):
                worker_errors.append('encoder did not reach barrier within 2s')
                return

            state.risk.per_strategy['injected_after_iter_started'] = StrategyRiskState(
                strategy_id='injected_after_iter_started',
            )
            encoder_release_barrier.set()
            mutator_done.set()

        worker = threading.Thread(target=mutator, daemon=True)
        worker.start()

        payload = serialize_state(state)

        worker.join(timeout=2)

        assert not worker.is_alive(), 'mutator thread did not exit within 2s'
        assert worker_errors == [], f'mutator reported errors: {worker_errors}'
        assert mutator_done.is_set()

        decoded = deserialize_state(payload)

        assert 'injected_after_iter_started' not in decoded.risk.per_strategy
        assert sorted(decoded.risk.per_strategy) == [
            f'anchor_strat_{i}' for i in range(5)
        ]

    def test_serialize_round_trips_capital_per_strategy_deployed(self) -> None:
        '''Coverage for the symmetric snapshot at
        `_encode_capital_state` line 126. The comprehension over
        `cs.per_strategy_deployed` has no per-item helper to patch
        (it calls `str(deployed)` directly), so a barrier-style
        test cannot be written without refactoring production code
        for testability. This round-trip test exercises the encode +
        decode path with populated `per_strategy_deployed` and pins
        that the snapshot does not corrupt the encoded payload; the
        snapshot mechanism itself is proven by the analogous barrier
        tests for positions, strategy_modes, and risk.per_strategy.
        '''

        state = _make_minimal_state()

        state.capital.per_strategy_deployed['strat_a'] = Decimal('1234.56')
        state.capital.per_strategy_deployed['strat_b'] = Decimal('7890.12')
        state.capital.per_strategy_deployed['strat_c'] = Decimal('0')

        payload = serialize_state(state)
        decoded = deserialize_state(payload)

        assert dict(decoded.capital.per_strategy_deployed) == {
            'strat_a': Decimal('1234.56'),
            'strat_b': Decimal('7890.12'),
            'strat_c': Decimal('0'),
        }


class TestAccountDustRoundtrip:
    '''Verify `InstanceState.account_dust` roundtrips through WAL codec.

    Covers Vaquum/Nexus#82 — sub-lot residue accumulated via
    `_reduce_position` or `close_as_dust` must survive WAL replay and
    snapshot reload.
    '''

    def test_empty_account_dust_roundtrips(self) -> None:
        state = _make_minimal_state()
        data = serialize_state(state)
        restored = deserialize_state(data)
        assert restored.account_dust == {}

    def test_populated_account_dust_roundtrips(self) -> None:
        state = _make_minimal_state()
        state.account_dust['BTCUSDT'] = Decimal('0.00000842')
        state.account_dust['ETHUSDT'] = Decimal('0.00012345')

        data = serialize_state(state)
        restored = deserialize_state(data)

        assert restored.account_dust == {
            'BTCUSDT': Decimal('0.00000842'),
            'ETHUSDT': Decimal('0.00012345'),
        }

    def test_old_snapshot_without_account_dust_defaults_empty(self) -> None:
        '''Simulate an old snapshot by serializing a fresh state and then
        stripping the `account_dust` field — the codec should then default
        the missing field to `{}` on deserialize.'''

        state = _make_minimal_state()
        data = serialize_state(state)
        d = cast(dict[str, Any], msgpack.unpackb(data, raw=False))
        d.pop('account_dust', None)
        legacy_data: bytes = cast(bytes, msgpack.packb(d))

        restored = deserialize_state(legacy_data)
        assert restored.account_dust == {}


def test_decode_capital_state_snaps_subulp_negative_residue() -> None:
    '''A sub-ULP negative residue in a non-negative aggregate is snapped
    to zero on decode so recovery does not brick on a persisted residue
    (e.g. working_order_notional == -1E-27 from fee-division rounding).'''

    decoded = wal_codec._decode_capital_state({
        'capital_pool': '80000',
        'position_notional': '6005.999999999999999999999999',
        'working_order_notional': '-1E-27',
        'in_flight_order_notional': '0',
        'fee_reserve': '1E-27',
        'reservation_notional': '0',
        'per_strategy_deployed': {},
    })

    assert decoded.working_order_notional == Decimal('0')


def test_decode_capital_state_rejects_meaningful_negative() -> None:
    '''A negative beyond the sub-ULP tolerance is left intact so
    CapitalState still rejects genuinely-broken persisted state.'''

    with pytest.raises(ValueError):
        wal_codec._decode_capital_state({
            'capital_pool': '80000',
            'position_notional': '0',
            'working_order_notional': '-0.5',
            'in_flight_order_notional': '0',
            'fee_reserve': '0',
            'reservation_notional': '0',
            'per_strategy_deployed': {},
        })


class TestProcessedDedupIds:
    '''Verify the durable outcome / dust-close dedup sets round-trip.'''

    def test_processed_ids_round_trip(self) -> None:
        '''Verify both dedup sets survive serialize → deserialize.'''

        original = InstanceState(
            capital=CapitalState(capital_pool=Decimal('10000')),
            processed_outcome_ids={'oc-1', 'oc-2', 'oc-3'},
            processed_dust_close_ids={'dust-1', 'dust-2'},
        )
        restored = deserialize_state(serialize_state(original))

        assert restored.processed_outcome_ids == {'oc-1', 'oc-2', 'oc-3'}
        assert restored.processed_dust_close_ids == {'dust-1', 'dust-2'}

    def test_processed_ids_default_empty(self) -> None:
        '''Verify a state with no dedup ids round-trips to empty sets.'''

        restored = deserialize_state(serialize_state(_make_minimal_state()))

        assert restored.processed_outcome_ids == set()
        assert restored.processed_dust_close_ids == set()

    def test_processed_ids_back_compat_missing_keys(self) -> None:
        '''Verify a pre-Nexus#86 payload (no dedup keys) decodes to empty sets.'''

        payload = msgpack.unpackb(serialize_state(_make_minimal_state()), raw=False)
        del payload['processed_outcome_ids']
        del payload['processed_dust_close_ids']

        restored = deserialize_state(cast(bytes, msgpack.packb(payload)))

        assert restored.processed_outcome_ids == set()
        assert restored.processed_dust_close_ids == set()


class TestModeHoldsRoundTrip:
    '''Verify mode_holds survive serialize → deserialize.'''

    def test_active_holds_survive_round_trip(self) -> None:
        original = _make_minimal_state()
        original.mode_holds.manual_hold = HaltHold(
            active=True,
            reason='operator stop',
            since=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        original.mode_holds.risk_drawdown = HaltHold(active=True, reason='drawdown breach')

        restored = deserialize_state(serialize_state(original))

        assert restored.mode_holds.manual_hold.active
        assert restored.mode_holds.manual_hold.reason == 'operator stop'
        assert restored.mode_holds.manual_hold.since == datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert restored.mode_holds.risk_drawdown.active
        assert restored.mode_holds.risk_drawdown.since is None
        assert not restored.mode_holds.risk_daily_loss.active

    def test_pre_field_snapshot_defaults_to_empty_holds(self) -> None:
        original = _make_minimal_state()
        encoded = msgpack.unpackb(serialize_state(original))
        del encoded['mode_holds']

        restored = deserialize_state(msgpack.packb(encoded))

        assert not restored.mode_holds.any_active()
