'''Tests for strategy event dispatch types.'''

from __future__ import annotations

import math
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from nexus.core.domain.enums import OperationalMode, OrderSide
from nexus.core.domain.position import Position
from nexus.strategy import Action, ActionType, StrategyContext, StrategyParams
from nexus.strategy.signal import Signal


class TestStrategyParams:
    '''Tests for StrategyParams dataclass.'''

    def test_get_returns_value(self) -> None:
        '''get() returns value for existing key.'''

        params = StrategyParams(raw={'threshold': 0.5, 'window': 14})

        assert params.get('threshold') == 0.5
        assert params.get('window') == 14

    def test_get_returns_default_for_missing_key(self) -> None:
        '''get() returns default for missing key.'''

        params = StrategyParams(raw={'threshold': 0.5})

        assert params.get('missing') is None
        assert params.get('missing', 100) == 100

    def test_raw_must_be_dict(self) -> None:
        '''raw must be a dict.'''

        with pytest.raises(ValueError, match='must be a dict'):
            StrategyParams(raw='not a dict')  # type: ignore[arg-type]

    def test_empty_dict_valid(self) -> None:
        '''Empty dict is valid.'''

        params = StrategyParams(raw={})

        assert params.get('anything') is None

    def test_raw_is_immutable(self) -> None:
        '''raw dict is defensively copied and immutable.'''

        original = {'key': 'value'}
        params = StrategyParams(raw=original)

        original['key'] = 'mutated'

        assert params.raw['key'] == 'value'

        with pytest.raises(TypeError):
            params.raw['new'] = 'fail'  # type: ignore[index]


class TestSignal:
    '''Tests for Signal dataclass.'''

    def test_valid_signal(self) -> None:
        '''Valid signal constructs successfully.'''

        ts = datetime.now(timezone.utc)
        signal = Signal(
            predictor_fn_id='momentum_v1',
            values={'CAN_ENTER': 1, 'confidence': 0.85},
            timestamp=ts,
        )

        assert signal.predictor_fn_id == 'momentum_v1'
        assert signal.values == {'CAN_ENTER': 1, 'confidence': 0.85}
        assert signal.timestamp == ts

    def test_get_returns_value(self) -> None:
        '''get() returns value for existing key.'''

        signal = Signal(
            predictor_fn_id='test',
            values={'CAN_ENTER': 1, 'confidence': 0.85},
            timestamp=datetime.now(timezone.utc),
        )

        assert signal.get('CAN_ENTER') == 1
        assert signal.get('confidence') == 0.85

    def test_get_returns_default_for_missing_key(self) -> None:
        '''get() returns default for missing key.'''

        signal = Signal(
            predictor_fn_id='test',
            values={'CAN_ENTER': 1},
            timestamp=datetime.now(timezone.utc),
        )

        assert signal.get('missing') is None
        assert signal.get('missing', 0) == 0

    def test_empty_predictor_fn_id_raises(self) -> None:
        '''Empty predictor_fn_id raises ValueError.'''

        with pytest.raises(ValueError, match='non-empty string'):
            Signal(
                predictor_fn_id='',
                values={'CAN_ENTER': 1},
                timestamp=datetime.now(timezone.utc),
            )

    def test_whitespace_predictor_fn_id_raises(self) -> None:
        '''Whitespace-only predictor_fn_id raises ValueError.'''

        with pytest.raises(ValueError, match='non-empty string'):
            Signal(
                predictor_fn_id='   ',
                values={'CAN_ENTER': 1},
                timestamp=datetime.now(timezone.utc),
            )

    def test_non_finite_value_raises(self) -> None:
        '''Non-finite numeric value raises ValueError.'''

        with pytest.raises(ValueError, match='must be finite'):
            Signal(
                predictor_fn_id='test',
                values={'confidence': float('inf')},
                timestamp=datetime.now(timezone.utc),
            )

    def test_nan_value_raises(self) -> None:
        '''NaN value raises ValueError.'''

        with pytest.raises(ValueError, match='must be finite'):
            Signal(
                predictor_fn_id='test',
                values={'confidence': math.nan},
                timestamp=datetime.now(timezone.utc),
            )

    def test_decimal_nan_raises(self) -> None:
        '''Decimal NaN raises ValueError.'''

        with pytest.raises(ValueError, match='must be finite'):
            Signal(
                predictor_fn_id='test',
                values={'confidence': Decimal('NaN')},
                timestamp=datetime.now(timezone.utc),
            )

    def test_decimal_infinity_raises(self) -> None:
        '''Decimal Infinity raises ValueError.'''

        with pytest.raises(ValueError, match='must be finite'):
            Signal(
                predictor_fn_id='test',
                values={'confidence': Decimal('Infinity')},
                timestamp=datetime.now(timezone.utc),
            )

    def test_values_is_immutable(self) -> None:
        '''values dict is defensively copied and immutable.'''

        original = {'CAN_ENTER': 1}
        signal = Signal(
            predictor_fn_id='test',
            values=original,
            timestamp=datetime.now(timezone.utc),
        )

        original['CAN_ENTER'] = 999

        assert signal.values['CAN_ENTER'] == 1

        with pytest.raises(TypeError):
            signal.values['new'] = 'fail'  # type: ignore[index]

    def test_non_string_key_raises(self) -> None:
        '''Non-string key in values raises ValueError.'''

        with pytest.raises(ValueError, match='keys must be strings'):
            Signal(
                predictor_fn_id='test',
                values={123: 1},  # type: ignore[dict-item]
                timestamp=datetime.now(timezone.utc),
            )

    def test_values_must_be_dict(self) -> None:
        '''values must be a dict.'''

        with pytest.raises(ValueError, match='must be a dict'):
            Signal(
                predictor_fn_id='test',
                values='not a dict',  # type: ignore[arg-type]
                timestamp=datetime.now(timezone.utc),
            )

    def test_timestamp_must_be_datetime(self) -> None:
        '''timestamp must be a datetime.'''

        with pytest.raises(ValueError, match='must be a datetime'):
            Signal(
                predictor_fn_id='test',
                values={'CAN_ENTER': 1},
                timestamp='not a datetime',  # type: ignore[arg-type]
            )

    def test_naive_timestamp_raises(self) -> None:
        '''Naive datetime raises ValueError.'''

        from datetime import datetime as dt

        with pytest.raises(ValueError, match='must be UTC'):
            Signal(
                predictor_fn_id='test',
                values={'CAN_ENTER': 1},
                timestamp=dt.now(),
            )

    def test_non_utc_timestamp_rejected(self) -> None:
        '''Non-UTC timezone raises ValueError.'''

        from datetime import datetime as dt, timedelta, timezone as tz

        non_utc = dt(2024, 1, 1, 12, 0, 0, tzinfo=tz(timedelta(hours=5)))
        with pytest.raises(ValueError, match='must be UTC'):
            Signal(
                predictor_fn_id='test',
                values={'CAN_ENTER': 1},
                timestamp=non_utc,
            )


class TestActionType:
    '''Tests for ActionType enum.'''

    def test_all_action_types_exist(self) -> None:
        '''All expected action types exist.'''

        assert ActionType.ENTER.value == 'enter'
        assert ActionType.EXIT.value == 'exit'
        assert ActionType.MODIFY.value == 'modify'
        assert ActionType.ABORT.value == 'abort'


class TestAction:
    '''Tests for Action dataclass.'''

    def test_action_with_each_type(self) -> None:
        '''Action constructs with each ActionType.'''

        for action_type in ActionType:
            action = Action(action_type=action_type)

            assert action.action_type == action_type

    def test_action_type_must_be_actiontype(self) -> None:
        '''action_type must be an ActionType.'''

        with pytest.raises(ValueError, match='must be an ActionType'):
            Action(action_type='enter')  # type: ignore[arg-type]


class TestStrategyContext:
    '''Tests for StrategyContext dataclass.'''

    def _make_position(self) -> Position:
        '''Create a valid position for testing.'''

        return Position(
            trade_id='trade_001',
            strategy_id='momentum_v1',
            symbol='BTC-USDT',
            side=OrderSide.BUY,
            size=Decimal('0.1'),
            entry_price=Decimal('50000'),
        )

    def test_valid_context(self) -> None:
        '''Valid context constructs successfully.'''

        pos = self._make_position()
        ctx = StrategyContext(
            positions=(pos,),
            capital_available=Decimal('10000'),
            operational_mode=OperationalMode.ACTIVE,
        )

        assert ctx.positions == (pos,)
        assert ctx.capital_available == Decimal('10000')
        assert ctx.operational_mode == OperationalMode.ACTIVE

    def test_empty_positions_valid(self) -> None:
        '''Empty positions tuple is valid.'''

        ctx = StrategyContext(
            positions=(),
            capital_available=Decimal('10000'),
            operational_mode=OperationalMode.ACTIVE,
        )

        assert ctx.positions == ()

    def test_positions_must_be_tuple(self) -> None:
        '''positions must be a tuple.'''

        with pytest.raises(ValueError, match='must be a tuple'):
            StrategyContext(
                positions=[],  # type: ignore[arg-type]
                capital_available=Decimal('10000'),
                operational_mode=OperationalMode.ACTIVE,
            )

    def test_positions_must_contain_position_instances(self) -> None:
        '''positions must contain only Position instances.'''

        with pytest.raises(ValueError, match='Position instances'):
            StrategyContext(
                positions=('not a position',),  # type: ignore[arg-type]
                capital_available=Decimal('10000'),
                operational_mode=OperationalMode.ACTIVE,
            )

    def test_capital_must_be_decimal(self) -> None:
        '''capital_available must be a Decimal.'''

        with pytest.raises(ValueError, match='must be a Decimal'):
            StrategyContext(
                positions=(),
                capital_available=10000,  # type: ignore[arg-type]
                operational_mode=OperationalMode.ACTIVE,
            )

    def test_capital_must_be_non_negative(self) -> None:
        '''capital_available must be non-negative.'''

        with pytest.raises(ValueError, match='non-negative'):
            StrategyContext(
                positions=(),
                capital_available=Decimal('-100'),
                operational_mode=OperationalMode.ACTIVE,
            )

    def test_operational_mode_must_be_enum(self) -> None:
        '''operational_mode must be an OperationalMode.'''

        with pytest.raises(ValueError, match='must be an OperationalMode'):
            StrategyContext(
                positions=(),
                capital_available=Decimal('10000'),
                operational_mode='ACTIVE',  # type: ignore[arg-type]
            )
