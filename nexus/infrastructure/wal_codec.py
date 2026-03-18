'''Serialization codec for InstanceState to/from bytes via msgpack.

Explicit per-type encode/decode for full type safety. Each domain
dataclass has a paired _encode / _decode function. Codec version
is embedded for forward compatibility.
'''

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

import msgpack

from nexus.core.domain.capital_state import CapitalState
from nexus.core.domain.enums import OperationalMode, OrderSide
from nexus.core.domain.instance_state import InstanceState
from nexus.core.domain.operational_mode import ModeState, StrategyModeState
from nexus.core.domain.position import Position
from nexus.core.domain.risk_state import RiskState, StrategyRiskState

__all__ = ['deserialize_state', 'serialize_state']

_CODEC_VERSION = 1


def serialize_state(state: InstanceState) -> bytes:
    '''Serialize InstanceState to compact binary format.

    Args:
        state: The instance state to serialize.

    Returns:
        Msgpack-encoded bytes.
    '''

    d: dict[str, Any] = {
        '_v': _CODEC_VERSION,
        'capital': _encode_capital_state(state.capital),
        'risk': _encode_risk_state(state.risk),
        'positions': {k: _encode_position(v) for k, v in state.positions.items()},
        'mode': _encode_mode_state(state.mode),
        'strategy_modes': {
            k: _encode_strategy_mode_state(v) for k, v in state.strategy_modes.items()
        },
    }
    return bytes(msgpack.packb(d))


def deserialize_state(data: bytes) -> InstanceState:
    '''Deserialize InstanceState from compact binary format.

    Args:
        data: Msgpack-encoded bytes.

    Returns:
        Reconstructed InstanceState.
    '''

    d = msgpack.unpackb(data, raw=False)
    version = d.get('_v', 0)
    if version != _CODEC_VERSION:
        msg = f'Unsupported WAL codec version: {version}'
        raise ValueError(msg)

    return InstanceState(
        capital=_decode_capital_state(d['capital']),
        risk=_decode_risk_state(d['risk']),
        positions={k: _decode_position(v) for k, v in d['positions'].items()},
        mode=_decode_mode_state(d['mode']),
        strategy_modes={
            k: _decode_strategy_mode_state(v) for k, v in d['strategy_modes'].items()
        },
    )


def _encode_capital_state(cs: CapitalState) -> dict[str, str]:
    return {
        'capital_pool': str(cs.capital_pool),
        'position_notional': str(cs.position_notional),
        'working_order_notional': str(cs.working_order_notional),
        'in_flight_order_notional': str(cs.in_flight_order_notional),
        'fee_reserve': str(cs.fee_reserve),
        'reservation_notional': str(cs.reservation_notional),
    }


def _decode_capital_state(d: dict[str, str]) -> CapitalState:
    return CapitalState(
        capital_pool=Decimal(d['capital_pool']),
        position_notional=Decimal(d['position_notional']),
        working_order_notional=Decimal(d['working_order_notional']),
        in_flight_order_notional=Decimal(d['in_flight_order_notional']),
        fee_reserve=Decimal(d['fee_reserve']),
        reservation_notional=Decimal(d['reservation_notional']),
    )


def _encode_strategy_risk_state(srs: StrategyRiskState) -> dict[str, str]:
    return {
        'strategy_id': srs.strategy_id,
        'high_water_mark': str(srs.high_water_mark),
        'rolling_loss_24h': str(srs.rolling_loss_24h),
        'rolling_loss_7d': str(srs.rolling_loss_7d),
        'rolling_loss_30d': str(srs.rolling_loss_30d),
        'strategy_realized_pnl': str(srs.strategy_realized_pnl),
    }


def _decode_strategy_risk_state(d: dict[str, str]) -> StrategyRiskState:
    return StrategyRiskState(
        strategy_id=d['strategy_id'],
        high_water_mark=Decimal(d['high_water_mark']),
        rolling_loss_24h=Decimal(d['rolling_loss_24h']),
        rolling_loss_7d=Decimal(d['rolling_loss_7d']),
        rolling_loss_30d=Decimal(d['rolling_loss_30d']),
        strategy_realized_pnl=Decimal(d['strategy_realized_pnl']),
    )


def _encode_risk_state(rs: RiskState) -> dict[str, Any]:
    return {
        'high_water_mark': str(rs.high_water_mark),
        'per_strategy': {
            k: _encode_strategy_risk_state(v) for k, v in rs.per_strategy.items()
        },
    }


def _decode_risk_state(d: dict[str, Any]) -> RiskState:
    return RiskState(
        high_water_mark=Decimal(d['high_water_mark']),
        per_strategy={
            k: _decode_strategy_risk_state(v) for k, v in d['per_strategy'].items()
        },
    )


def _encode_position(pos: Position) -> dict[str, str]:
    return {
        'trade_id': pos.trade_id,
        'strategy_id': pos.strategy_id,
        'symbol': pos.symbol,
        'side': pos.side.value,
        'size': str(pos.size),
        'entry_price': str(pos.entry_price),
        'unrealized_pnl': str(pos.unrealized_pnl),
        'pending_exit': str(pos.pending_exit),
    }


def _decode_position(d: dict[str, str]) -> Position:
    return Position(
        trade_id=d['trade_id'],
        strategy_id=d['strategy_id'],
        symbol=d['symbol'],
        side=OrderSide(d['side']),
        size=Decimal(d['size']),
        entry_price=Decimal(d['entry_price']),
        unrealized_pnl=Decimal(d['unrealized_pnl']),
        pending_exit=Decimal(d['pending_exit']),
    )


def _encode_mode_state(ms: ModeState) -> dict[str, str]:
    return {
        'mode': ms.mode.value,
        'trigger': ms.trigger,
        'transitioned_at': ms.transitioned_at.isoformat(),
    }


def _decode_mode_state(d: dict[str, str]) -> ModeState:
    return ModeState(
        mode=OperationalMode(d['mode']),
        trigger=d['trigger'],
        transitioned_at=datetime.fromisoformat(d['transitioned_at']),
    )


def _encode_strategy_mode_state(sms: StrategyModeState) -> dict[str, Any]:
    return {
        'strategy_id': sms.strategy_id,
        'state': _encode_mode_state(sms.state),
    }


def _decode_strategy_mode_state(d: dict[str, Any]) -> StrategyModeState:
    return StrategyModeState(
        strategy_id=d['strategy_id'],
        state=_decode_mode_state(d['state']),
    )
