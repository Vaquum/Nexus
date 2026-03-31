'''Tests for Strategy abstract base class.'''

from __future__ import annotations

import pytest

from nexus.strategy import Strategy


class ConcreteStrategy(Strategy):
    '''Valid concrete strategy for testing.'''

    def __init__(self, strategy_id: str) -> None:
        super().__init__(strategy_id)
        self._state: bytes = b''

    def on_save(self) -> bytes:
        return self._state

    def on_load(self, data: bytes) -> None:
        self._state = data


class TestStrategyABC:
    '''Tests for Strategy abstract base class.'''

    def test_cannot_instantiate_abc_directly(self) -> None:
        '''Strategy ABC cannot be instantiated directly.'''

        with pytest.raises(TypeError, match='abstract'):
            Strategy('test')  # type: ignore[abstract]

    def test_concrete_strategy_instantiates(self) -> None:
        '''Concrete strategy with all methods instantiates successfully.'''

        strategy = ConcreteStrategy('momentum_v1')

        assert strategy.strategy_id == 'momentum_v1'

    def test_strategy_id_property_returns_id(self) -> None:
        '''strategy_id property returns the id passed at construction.'''

        strategy = ConcreteStrategy('momentum_btc')

        assert strategy.strategy_id == 'momentum_btc'

    def test_empty_strategy_id_raises(self) -> None:
        '''Empty strategy_id raises ValueError.'''

        with pytest.raises(ValueError, match='non-empty string'):
            ConcreteStrategy('')

    def test_whitespace_strategy_id_raises(self) -> None:
        '''Whitespace-only strategy_id raises ValueError.'''

        with pytest.raises(ValueError, match='non-empty string'):
            ConcreteStrategy('   ')

    def test_strategy_id_normalized(self) -> None:
        '''strategy_id is stripped of leading/trailing whitespace.'''

        strategy = ConcreteStrategy('  momentum_v1  ')

        assert strategy.strategy_id == 'momentum_v1'

    def test_non_string_strategy_id_raises(self) -> None:
        '''Non-string strategy_id raises ValueError.'''

        with pytest.raises(ValueError, match='non-empty string'):
            ConcreteStrategy(123)  # type: ignore[arg-type]


class TestOnSaveOnLoad:
    '''Tests for on_save/on_load lifecycle.'''

    def test_on_save_returns_bytes(self) -> None:
        '''on_save returns bytes.'''

        strategy = ConcreteStrategy('test')
        strategy._state = b'test state'

        result = strategy.on_save()

        assert result == b'test state'
        assert isinstance(result, bytes)

    def test_on_load_restores_state(self) -> None:
        '''on_load restores state from bytes.'''

        strategy = ConcreteStrategy('test')

        strategy.on_load(b'restored state')

        assert strategy._state == b'restored state'

    def test_on_save_on_load_round_trip(self) -> None:
        '''on_save followed by on_load preserves state.'''

        strategy1 = ConcreteStrategy('test')
        strategy1._state = b'important data'

        saved = strategy1.on_save()

        strategy2 = ConcreteStrategy('test')
        strategy2.on_load(saved)

        assert strategy2._state == strategy1._state

    def test_on_save_on_load_empty_bytes(self) -> None:
        '''on_save/on_load handle empty bytes.'''

        strategy = ConcreteStrategy('test')
        strategy._state = b''

        saved = strategy.on_save()

        assert saved == b''

        strategy.on_load(b'')

        assert strategy._state == b''


class MissingOnSaveStrategy(Strategy):
    '''Strategy missing on_save implementation.'''

    def on_load(self, data: bytes) -> None:
        pass


class MissingOnLoadStrategy(Strategy):
    '''Strategy missing on_load implementation.'''

    def on_save(self) -> bytes:
        return b''


class TestMissingMethods:
    '''Tests for strategies missing required methods.'''

    def test_missing_on_save_raises(self) -> None:
        '''Strategy missing on_save cannot be instantiated.'''

        with pytest.raises(TypeError, match='abstract'):
            MissingOnSaveStrategy('test')  # type: ignore[abstract]

    def test_missing_on_load_raises(self) -> None:
        '''Strategy missing on_load cannot be instantiated.'''

        with pytest.raises(TypeError, match='abstract'):
            MissingOnLoadStrategy('test')  # type: ignore[abstract]
