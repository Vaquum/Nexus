'''Serialization codec for InstanceState and StrategyEvent via msgpack.

Explicit per-type encode/decode for full type safety. Each domain
dataclass has a paired _encode / _decode function. Codec version
is embedded for forward compatibility.
'''

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import msgpack

from nexus.core.domain.capital_state import CapitalState
from nexus.core.domain.enums import OperationalMode, OrderSide
from nexus.core.domain.instance_state import InstanceState
from nexus.core.domain.operational_mode import ModeState, StrategyModeState
from nexus.core.domain.position import Position
from nexus.core.domain.risk_state import RiskState, StrategyRiskState
from nexus.infrastructure.strategy_event import StrategyEvent

__all__ = [
    'deserialize_event',
    'deserialize_state',
    'serialize_event',
    'serialize_state',
]

_CODEC_VERSION = 1
_EVENT_CODEC_VERSION = 1


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
    if not isinstance(d, dict):
        msg = f'Expected dict from WAL payload, got {type(d).__name__}'
        raise ValueError(msg)
    version = d.get('_v', 0)
    if version != _CODEC_VERSION:
        msg = f'Unsupported WAL codec version: {version}'
        raise ValueError(msg)

    try:
        return InstanceState(
            capital=_decode_capital_state(d['capital']),
            risk=_decode_risk_state(d['risk']),
            positions={k: _decode_position(v) for k, v in d['positions'].items()},
            mode=_decode_mode_state(d['mode']),
            strategy_modes={
                k: _decode_strategy_mode_state(v)
                for k, v in d['strategy_modes'].items()
            },
        )
    except (KeyError, TypeError, AttributeError) as exc:
        msg = f'Malformed WAL codec payload: {exc}'
        raise ValueError(msg) from exc


def _encode_capital_state(cs: CapitalState) -> dict[str, str]:
    '''Encode CapitalState to string-valued dict for msgpack.

    Args:
        cs: Capital state to encode.

    Returns:
        String-keyed dict with Decimal values as strings.
    '''

    return {
        'capital_pool': str(cs.capital_pool),
        'position_notional': str(cs.position_notional),
        'working_order_notional': str(cs.working_order_notional),
        'in_flight_order_notional': str(cs.in_flight_order_notional),
        'fee_reserve': str(cs.fee_reserve),
        'reservation_notional': str(cs.reservation_notional),
    }


def _decode_capital_state(d: dict[str, str]) -> CapitalState:
    '''Decode string-valued dict to CapitalState.

    Args:
        d: Encoded capital state dict.

    Returns:
        Reconstructed capital state.
    '''

    return CapitalState(
        capital_pool=Decimal(d['capital_pool']),
        position_notional=Decimal(d['position_notional']),
        working_order_notional=Decimal(d['working_order_notional']),
        in_flight_order_notional=Decimal(d['in_flight_order_notional']),
        fee_reserve=Decimal(d['fee_reserve']),
        reservation_notional=Decimal(d['reservation_notional']),
    )


def _encode_strategy_risk_state(srs: StrategyRiskState) -> dict[str, str]:
    '''Encode StrategyRiskState to string-valued dict for msgpack.

    Args:
        srs: Strategy risk state to encode.

    Returns:
        String-keyed dict with Decimal values as strings.
    '''

    return {
        'strategy_id': srs.strategy_id,
        'high_water_mark': str(srs.high_water_mark),
        'rolling_loss_24h': str(srs.rolling_loss_24h),
        'rolling_loss_7d': str(srs.rolling_loss_7d),
        'rolling_loss_30d': str(srs.rolling_loss_30d),
        'strategy_realized_pnl': str(srs.strategy_realized_pnl),
    }


def _decode_strategy_risk_state(d: dict[str, str]) -> StrategyRiskState:
    '''Decode string-valued dict to StrategyRiskState.

    Args:
        d: Encoded strategy risk state dict.

    Returns:
        Reconstructed strategy risk state.
    '''

    return StrategyRiskState(
        strategy_id=d['strategy_id'],
        high_water_mark=Decimal(d['high_water_mark']),
        rolling_loss_24h=Decimal(d['rolling_loss_24h']),
        rolling_loss_7d=Decimal(d['rolling_loss_7d']),
        rolling_loss_30d=Decimal(d['rolling_loss_30d']),
        strategy_realized_pnl=Decimal(d['strategy_realized_pnl']),
    )


def _encode_risk_state(rs: RiskState) -> dict[str, Any]:
    '''Encode RiskState to nested dict for msgpack.

    Args:
        rs: Risk state to encode.

    Returns:
        Nested dict with per-strategy risk states.
    '''

    return {
        'high_water_mark': str(rs.high_water_mark),
        'per_strategy': {
            k: _encode_strategy_risk_state(v) for k, v in rs.per_strategy.items()
        },
    }


def _decode_risk_state(d: dict[str, Any]) -> RiskState:
    '''Decode nested dict to RiskState.

    Args:
        d: Encoded risk state dict.

    Returns:
        Reconstructed risk state with per-strategy entries.
    '''

    return RiskState(
        high_water_mark=Decimal(d['high_water_mark']),
        per_strategy={
            k: _decode_strategy_risk_state(v) for k, v in d['per_strategy'].items()
        },
    )


def _encode_position(pos: Position) -> dict[str, str]:
    '''Encode Position to string-valued dict for msgpack.

    Args:
        pos: Position to encode.

    Returns:
        String-keyed dict with Decimal and enum values as strings.
    '''

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
    '''Decode string-valued dict to Position.

    Args:
        d: Encoded position dict.

    Returns:
        Reconstructed position.
    '''

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
    '''Encode ModeState to string-valued dict for msgpack.

    Args:
        ms: Mode state to encode.

    Returns:
        String-keyed dict with enum and datetime as strings.
    '''

    return {
        'mode': ms.mode.value,
        'trigger': ms.trigger,
        'transitioned_at': ms.transitioned_at.isoformat(),
    }


def _decode_mode_state(d: dict[str, str]) -> ModeState:
    '''Decode string-valued dict to ModeState.

    Args:
        d: Encoded mode state dict.

    Returns:
        Reconstructed mode state.
    '''

    return ModeState(
        mode=OperationalMode(d['mode']),
        trigger=d['trigger'],
        transitioned_at=datetime.fromisoformat(d['transitioned_at']),
    )


def _encode_strategy_mode_state(sms: StrategyModeState) -> dict[str, Any]:
    '''Encode StrategyModeState to nested dict for msgpack.

    Args:
        sms: Strategy mode state to encode.

    Returns:
        Nested dict with encoded mode state.
    '''

    return {
        'strategy_id': sms.strategy_id,
        'state': _encode_mode_state(sms.state),
    }


def _decode_strategy_mode_state(d: dict[str, Any]) -> StrategyModeState:
    '''Decode nested dict to StrategyModeState.

    Args:
        d: Encoded strategy mode state dict.

    Returns:
        Reconstructed strategy mode state.
    '''

    return StrategyModeState(
        strategy_id=d['strategy_id'],
        state=_decode_mode_state(d['state']),
    )


def serialize_event(event: StrategyEvent) -> bytes:
    '''Serialize a StrategyEvent to compact binary format.

    Args:
        event: The strategy event to serialize.

    Returns:
        Msgpack-encoded bytes.
    '''

    d: dict[str, str | int] = {
        '_v': _EVENT_CODEC_VERSION,
        'strategy_id': event.strategy_id,
        'event_type': event.event_type,
        'realized_pnl': str(event.realized_pnl),
        'timestamp': event.timestamp.isoformat(),
    }
    return bytes(msgpack.packb(d))


def deserialize_event(data: bytes) -> StrategyEvent:
    '''Deserialize a StrategyEvent from compact binary format.

    Args:
        data: Msgpack-encoded bytes.

    Returns:
        Reconstructed StrategyEvent.
    '''

    d = msgpack.unpackb(data, raw=False)
    if not isinstance(d, dict):
        msg = f'Expected dict from event payload, got {type(d).__name__}'
        raise ValueError(msg)
    try:
        version = int(d.get('_v', 0))
    except (ValueError, TypeError) as exc:
        msg = f'Malformed event codec version: {exc}'
        raise ValueError(msg) from exc

    if version != _EVENT_CODEC_VERSION:
        msg = f'Unsupported event codec version: {version}'
        raise ValueError(msg)

    try:
        return StrategyEvent(
            strategy_id=d['strategy_id'],
            event_type=d['event_type'],
            realized_pnl=Decimal(d['realized_pnl']),
            timestamp=datetime.fromisoformat(d['timestamp']),
        )
    except (KeyError, TypeError, AttributeError, ValueError, InvalidOperation) as exc:
        msg = f'Malformed event codec payload: {exc}'
        raise ValueError(msg) from exc
