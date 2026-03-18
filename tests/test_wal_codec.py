'''Verify WAL codec round-trip serialization for InstanceState.'''

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from nexus.core.domain.capital_state import CapitalState
from nexus.core.domain.enums import OperationalMode, OrderSide
from nexus.core.domain.instance_state import InstanceState
from nexus.core.domain.operational_mode import ModeState, StrategyModeState
from nexus.core.domain.position import Position
from nexus.core.domain.risk_state import RiskState, StrategyRiskState
from nexus.infrastructure.wal_codec import deserialize_state, serialize_state


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
        ),
        risk=RiskState(
            high_water_mark=Decimal('110000'),
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

    def test_full_state_risk(self) -> None:
        '''Verify risk fields survive round-trip.'''

        original = _make_full_state()
        restored = deserialize_state(serialize_state(original))

        assert restored.risk.high_water_mark == original.risk.high_water_mark
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

        import msgpack

        bad_data = msgpack.packb({'_v': 999})
        with pytest.raises(ValueError, match='Unsupported WAL codec version'):
            deserialize_state(bad_data)

    def test_missing_version_rejected(self) -> None:
        '''Verify deserialize rejects payload with no version field.'''

        import msgpack

        bad_data = msgpack.packb({'capital': {}})
        with pytest.raises(ValueError, match='Unsupported WAL codec version'):
            deserialize_state(bad_data)


class TestMalformedPayload:
    '''Verify deserialize_state rejects non-dict payloads.'''

    def test_non_dict_payload_raises(self) -> None:
        '''Verify non-dict msgpack payload raises ValueError.'''

        import msgpack

        bad_data = bytes(msgpack.packb([1, 2, 3]))

        with pytest.raises(ValueError, match='Expected dict from WAL payload'):
            deserialize_state(bad_data)


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

        import msgpack

        state = _make_minimal_state()
        result = serialize_state(state)
        unpacked = msgpack.unpackb(result, raw=False)
        assert isinstance(unpacked, dict)
        assert unpacked['_v'] == 1
