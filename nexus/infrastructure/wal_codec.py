'''Serialization codec for InstanceState and StrategyEvent via msgpack.

Explicit per-type encode/decode for full type safety. Each domain
dataclass has a paired _encode / _decode function. Codec version
is embedded for forward compatibility.
'''

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, cast

import msgpack

from nexus.core.domain.capital_state import SUB_ULP_TOLERANCE as _SUB_ULP_TOLERANCE
from nexus.core.domain.capital_state import CapitalState
from nexus.core.domain.enums import OperationalMode, OrderSide
from nexus.core.domain.instance_state import InstanceState
from nexus.core.domain.operational_mode import (
    HaltHold,
    ModeState,
    OperationalHolds,
    ReduceOnlyHolds,
    StrategyModeState,
)
from nexus.core.domain.position import Position
from nexus.core.domain.risk_state import RiskState, StrategyRiskState
from nexus.infrastructure.strategy_event import StrategyEvent

__all__ = [
    'deserialize_event',
    'deserialize_state',
    'serialize_event',
    'serialize_state',
]

_ZERO = Decimal(0)


def _clamp_subulp_negative(value: Decimal) -> Decimal:
    '''Snap a sub-tolerance negative aggregate to zero, else return as-is.

    Capital reserve/release arithmetic can leave a sub-ULP negative
    Decimal residue (e.g. `-1E-27`) in a field that must stay
    non-negative. Persisting it bricks recovery because
    `CapitalState.__post_init__` rejects any negative. Snap only residues
    within `_SUB_ULP_TOLERANCE`; a meaningful negative is left intact so
    the domain invariant still rejects genuinely-broken state.
    '''

    if -_SUB_ULP_TOLERANCE <= value < _ZERO:
        return _ZERO

    return value

_CODEC_VERSION_1 = 1
_CODEC_VERSION_LATEST = _CODEC_VERSION_1

_EVENT_CODEC_VERSION_1 = 1
_EVENT_CODEC_VERSION_2 = 2
_EVENT_CODEC_VERSION_LATEST = _EVENT_CODEC_VERSION_2


def serialize_state(state: InstanceState) -> bytes:
    '''Serialize InstanceState to compact binary format.

    Args:
        state: The instance state to serialize.

    Returns:
        Msgpack-encoded bytes.
    '''

    positions_snapshot = dict(state.positions)
    strategy_modes_snapshot = dict(state.strategy_modes)
    account_dust_snapshot = dict(state.account_dust)
    processed_outcome_ids_snapshot = set(state.processed_outcome_ids)
    processed_dust_close_ids_snapshot = set(state.processed_dust_close_ids)

    d: dict[str, Any] = {
        '_v': _CODEC_VERSION_LATEST,
        'capital': _encode_capital_state(state.capital),
        'risk': _encode_risk_state(state.risk),
        'positions': {k: _encode_position(v) for k, v in positions_snapshot.items()},
        'mode': _encode_mode_state(state.mode),
        'health_mode': state.health_mode.value,
        'mode_holds': _encode_operational_holds(state.mode_holds),
        'reduce_only_holds': _encode_reduce_only_holds(state.reduce_only_holds),
        'strategy_modes': {
            k: _encode_strategy_mode_state(v) for k, v in strategy_modes_snapshot.items()
        },
        'account_dust': {k: str(v) for k, v in account_dust_snapshot.items()},
        'processed_outcome_ids': sorted(processed_outcome_ids_snapshot),
        'processed_dust_close_ids': sorted(processed_dust_close_ids_snapshot),
    }
    return cast(bytes, msgpack.packb(d))


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
    version = d.get('_v', _CODEC_VERSION_1)

    if version == _CODEC_VERSION_1:
        return _decode_state_v1(d)

    msg = f'Unsupported WAL codec version: {version}'
    raise ValueError(msg)


def _decode_state_v1(d: dict[str, Any]) -> InstanceState:
    '''Decode v1 state payload to InstanceState.

    Args:
        d: Decoded msgpack dict with v1 schema.

    Returns:
        Reconstructed InstanceState.
    '''

    try:
        raw_account_dust = d.get('account_dust', {})
        account_dust = {k: Decimal(v) for k, v in raw_account_dust.items()}

        return InstanceState(
            capital=_decode_capital_state(d['capital']),
            risk=_decode_risk_state(d['risk']),
            positions={k: _decode_position(v) for k, v in d['positions'].items()},
            mode=_decode_mode_state(d['mode']),
            health_mode=_decode_health_mode(d.get('health_mode')),
            mode_holds=_decode_operational_holds(d.get('mode_holds')),
            reduce_only_holds=_decode_reduce_only_holds(d.get('reduce_only_holds')),
            strategy_modes={
                k: _decode_strategy_mode_state(v)
                for k, v in d['strategy_modes'].items()
            },
            account_dust=account_dust,
            processed_outcome_ids=set(d.get('processed_outcome_ids', [])),
            processed_dust_close_ids=set(d.get('processed_dust_close_ids', [])),
        )
    except (KeyError, TypeError, AttributeError, ValueError, InvalidOperation) as exc:
        msg = f'Malformed WAL codec payload: {exc}'
        raise ValueError(msg) from exc


def _encode_capital_state(cs: CapitalState) -> dict[str, Any]:
    '''Encode CapitalState to nested dict for msgpack.

    Args:
        cs: Capital state to encode.

    Returns:
        String-keyed dict with top-level Decimal values as strings and
        ``per_strategy_deployed`` as a nested mapping of
        ``strategy_id -> deployed Decimal`` encoded as strings.
    '''

    per_strategy_deployed_snapshot = dict(cs.per_strategy_deployed)

    return {
        'capital_pool': str(cs.capital_pool),
        'position_notional': str(cs.position_notional),
        'working_order_notional': str(cs.working_order_notional),
        'in_flight_order_notional': str(cs.in_flight_order_notional),
        'fee_reserve': str(cs.fee_reserve),
        'reservation_notional': str(cs.reservation_notional),
        'per_strategy_deployed': {
            strategy_id: str(deployed)
            for strategy_id, deployed in per_strategy_deployed_snapshot.items()
        },
    }


def _decode_capital_state(d: dict[str, Any]) -> CapitalState:
    '''Decode nested capital-state dict to CapitalState.

    Args:
        d: Encoded capital state dict with stringified Decimal fields and
            optional nested ``per_strategy_deployed`` mapping.

    Returns:
        Reconstructed capital state.
    '''

    return CapitalState(
        capital_pool=Decimal(d['capital_pool']),
        position_notional=_clamp_subulp_negative(Decimal(d['position_notional'])),
        working_order_notional=_clamp_subulp_negative(Decimal(d['working_order_notional'])),
        in_flight_order_notional=_clamp_subulp_negative(Decimal(d['in_flight_order_notional'])),
        fee_reserve=_clamp_subulp_negative(Decimal(d['fee_reserve'])),
        reservation_notional=_clamp_subulp_negative(Decimal(d['reservation_notional'])),
        per_strategy_deployed={
            strategy_id: _clamp_subulp_negative(Decimal(deployed))
            for strategy_id, deployed in d.get('per_strategy_deployed', {}).items()
        },
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
        'strategy_unrealized_pnl': str(srs.strategy_unrealized_pnl),
    }


def _decode_strategy_risk_state(d: dict[str, str]) -> StrategyRiskState:
    '''Decode string-valued dict to StrategyRiskState.

    `strategy_unrealized_pnl` defaults to zero for snapshots/WAL
    entries written before the field was added (pre-v0.54.0); MtmLoop
    overwrites the zero on the next tick.

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
        strategy_unrealized_pnl=Decimal(d.get('strategy_unrealized_pnl', '0')),
    )


def _encode_risk_state(rs: RiskState) -> dict[str, Any]:
    '''Encode RiskState to nested dict for msgpack.

    Args:
        rs: Risk state to encode.

    Returns:
        Nested dict with per-strategy risk states.
    '''

    per_strategy_snapshot = dict(rs.per_strategy)

    return {
        'high_water_mark': str(rs.high_water_mark),
        'starting_capital': str(rs.starting_capital),
        'cumulative_realized_pnl': str(rs.cumulative_realized_pnl),
        'unrealized_pnl': str(rs.unrealized_pnl),
        'equity': str(rs.equity),
        'equity_hwm': str(rs.equity_hwm),
        'realized_equity_hwm': str(rs.realized_equity_hwm),
        'total_drawdown': str(rs.total_drawdown),
        'total_drawdown_pct': str(rs.total_drawdown_pct),
        'realized_drawdown': str(rs.realized_drawdown),
        'unrealized_drawdown': str(rs.unrealized_drawdown),
        'max_drawdown': str(rs.max_drawdown),
        'max_drawdown_pct': str(rs.max_drawdown_pct),
        'max_total_drawdown': str(rs.max_total_drawdown),
        'max_total_drawdown_pct': str(rs.max_total_drawdown_pct),
        'per_strategy': {
            k: _encode_strategy_risk_state(v) for k, v in per_strategy_snapshot.items()
        },
    }


def _decode_risk_state(d: dict[str, Any]) -> RiskState:
    '''Decode nested dict to RiskState.

    Args:
        d: Encoded risk state dict.

    Returns:
        Reconstructed risk state with per-strategy entries.
    '''

    legacy_hwm = Decimal(d.get('high_water_mark', d.get('equity_hwm', '0')))
    equity_hwm = Decimal(d.get('equity_hwm', str(legacy_hwm)))
    high_water_mark = Decimal(d.get('high_water_mark', str(equity_hwm)))
    starting_capital = Decimal(d.get('starting_capital', str(legacy_hwm)))

    return RiskState(
        high_water_mark=high_water_mark,
        starting_capital=starting_capital,
        cumulative_realized_pnl=Decimal(d.get('cumulative_realized_pnl', '0')),
        unrealized_pnl=Decimal(d.get('unrealized_pnl', '0')),
        equity=Decimal(d.get('equity', str(starting_capital))),
        equity_hwm=equity_hwm,
        realized_equity_hwm=Decimal(d.get('realized_equity_hwm', str(equity_hwm))),
        total_drawdown=Decimal(d.get('total_drawdown', '0')),
        total_drawdown_pct=Decimal(d.get('total_drawdown_pct', '0')),
        realized_drawdown=Decimal(d.get('realized_drawdown', '0')),
        unrealized_drawdown=Decimal(d.get('unrealized_drawdown', '0')),
        max_drawdown=Decimal(d.get('max_drawdown', '0')),
        max_drawdown_pct=Decimal(d.get('max_drawdown_pct', '0')),
        max_total_drawdown=Decimal(d.get('max_total_drawdown', '0')),
        max_total_drawdown_pct=Decimal(d.get('max_total_drawdown_pct', '0')),
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
        'avg_cost_basis': str(pos.avg_cost_basis),
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
        avg_cost_basis=Decimal(d['avg_cost_basis']) if 'avg_cost_basis' in d else Decimal(d['entry_price']),
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


def _encode_operational_holds(holds: OperationalHolds) -> dict[str, Any]:
    '''Encode OperationalHolds to a nested dict for msgpack.

    Args:
        holds: Operational holds to encode.

    Returns:
        Dict of hold name to encoded HaltHold.
    '''

    return {
        'manual_hold': _encode_halt_hold(holds.manual_hold),
        'risk_daily_loss': _encode_halt_hold(holds.risk_daily_loss),
        'risk_drawdown': _encode_halt_hold(holds.risk_drawdown),
        'shutdown_hold': _encode_halt_hold(holds.shutdown_hold),
        'reconciliation_hold': _encode_halt_hold(holds.reconciliation_hold),
    }


def _decode_operational_holds(d: dict[str, Any] | None) -> OperationalHolds:
    '''Decode OperationalHolds, defaulting to empty for pre-field snapshots.

    Args:
        d: Encoded holds dict, or `None` when absent from an old snapshot.

    Returns:
        Reconstructed operational holds.
    '''

    if d is None:
        return OperationalHolds()

    raw_shutdown = d.get('shutdown_hold')
    raw_reconciliation = d.get('reconciliation_hold')

    return OperationalHolds(
        manual_hold=_decode_halt_hold(d['manual_hold']),
        risk_daily_loss=_decode_halt_hold(d['risk_daily_loss']),
        risk_drawdown=_decode_halt_hold(d['risk_drawdown']),
        shutdown_hold=_decode_halt_hold(raw_shutdown) if raw_shutdown is not None else HaltHold(),
        reconciliation_hold=(
            _decode_halt_hold(raw_reconciliation)
            if raw_reconciliation is not None else HaltHold()
        ),
    )


def _encode_reduce_only_holds(holds: ReduceOnlyHolds) -> dict[str, Any]:
    '''Encode ReduceOnlyHolds to a nested dict for msgpack.'''

    return {'reconciliation': _encode_halt_hold(holds.reconciliation)}


def _decode_reduce_only_holds(d: dict[str, Any] | None) -> ReduceOnlyHolds:
    '''Decode ReduceOnlyHolds, defaulting to empty for pre-field snapshots.'''

    if d is None:
        return ReduceOnlyHolds()

    raw_reconciliation = d.get('reconciliation')

    return ReduceOnlyHolds(
        reconciliation=(
            _decode_halt_hold(raw_reconciliation)
            if raw_reconciliation is not None else HaltHold()
        ),
    )


def _decode_health_mode(value: str | None) -> OperationalMode:
    '''Decode the persisted health mode, defaulting for pre-field snapshots.

    Args:
        value: Encoded OperationalMode value, or `None` when absent from
            an old snapshot.

    Returns:
        The health-derived mode, or ACTIVE when absent.
    '''

    if value is None:
        return OperationalMode.ACTIVE

    return OperationalMode(value)


def _encode_halt_hold(hold: HaltHold) -> dict[str, Any]:
    '''Encode a single HaltHold, `since` as an ISO string or `None`.'''

    return {
        'active': hold.active,
        'reason': hold.reason,
        'since': hold.since.isoformat() if hold.since is not None else None,
    }


def _decode_halt_hold(d: dict[str, Any]) -> HaltHold:
    '''Decode a single HaltHold from its encoded dict.'''

    since = d['since']

    return HaltHold(
        active=d['active'],
        reason=d['reason'],
        since=datetime.fromisoformat(since) if since is not None else None,
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

    Encodes as v2 when `outcome_id` is non-empty so the recovery
    deduper has a stable key; falls back to v1 (legacy, no dedup)
    when `outcome_id` is empty so callers that predate FINAL-TD-02
    keep working. PR #55 review: never tag a payload as v2 without a
    real `outcome_id`, since the strict v2 decoder rejects empty /
    missing values to prevent silent double-counting on WAL
    corruption.

    Args:
        event: The strategy event to serialize.

    Returns:
        Msgpack-encoded bytes.
    '''

    d: dict[str, str | int]
    if event.outcome_id:
        d = {
            '_v': _EVENT_CODEC_VERSION_LATEST,
            'strategy_id': event.strategy_id,
            'event_type': event.event_type,
            'realized_pnl': str(event.realized_pnl),
            'timestamp': event.timestamp.isoformat(),
            'outcome_id': event.outcome_id,
        }
    else:
        d = {
            '_v': _EVENT_CODEC_VERSION_1,
            'strategy_id': event.strategy_id,
            'event_type': event.event_type,
            'realized_pnl': str(event.realized_pnl),
            'timestamp': event.timestamp.isoformat(),
        }
    return cast(bytes, msgpack.packb(d))


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
        version = int(d.get('_v', _EVENT_CODEC_VERSION_1))
    except (ValueError, TypeError) as exc:
        msg = f'Malformed event codec version: {exc}'
        raise ValueError(msg) from exc

    if version == _EVENT_CODEC_VERSION_1:
        return _decode_event_v1(d)

    if version == _EVENT_CODEC_VERSION_2:
        return _decode_event_v2(d)

    msg = f'Unsupported event codec version: {version}'
    raise ValueError(msg)


def _decode_event_v1(d: dict[str, Any]) -> StrategyEvent:
    '''Decode v1 event payload to StrategyEvent.

    Legacy events predate FINAL-TD-02 — they have no `outcome_id`,
    so dedup is impossible for them. The default empty `outcome_id`
    is preserved; `derive_rolling_losses` skips dedup on empty ids.

    Mixed-WAL caveat: a WAL containing both v1 and v2 entries for the
    same outcome (transition window from before the v2 codec landed)
    will count the v1 instance unfiltered. Bounded by checkpoint
    truncation that drops pre-v2 events; tracked as a TD entry.

    Args:
        d: Decoded msgpack dict with v1 schema.

    Returns:
        Reconstructed StrategyEvent with `outcome_id=''`.
    '''

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


def _decode_event_v2(d: dict[str, Any]) -> StrategyEvent:
    '''Decode v2 event payload to StrategyEvent.

    v2 adds `outcome_id` (FINAL-TD-02) carried from
    `TradeOutcome.outcome_id` so duplicate venue re-deliveries can
    be filtered in `derive_rolling_losses`.

    Args:
        d: Decoded msgpack dict with v2 schema.

    Returns:
        Reconstructed StrategyEvent.
    '''

    try:
        outcome_id = d['outcome_id']
        if not isinstance(outcome_id, str) or not outcome_id or not outcome_id.strip():
            msg = (
                f'v2 event payload requires a non-blank `outcome_id` string; '
                f'got {outcome_id!r}'
            )
            raise ValueError(msg)
        return StrategyEvent(
            strategy_id=d['strategy_id'],
            event_type=d['event_type'],
            realized_pnl=Decimal(d['realized_pnl']),
            timestamp=datetime.fromisoformat(d['timestamp']),
            outcome_id=outcome_id,
        )
    except (KeyError, TypeError, AttributeError, ValueError, InvalidOperation) as exc:
        msg = f'Malformed event codec payload: {exc}'
        raise ValueError(msg) from exc
