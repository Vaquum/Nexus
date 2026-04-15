'''Tests for strategy loader.'''

from __future__ import annotations

import tempfile
from decimal import Decimal
from pathlib import Path

import pytest

from nexus.infrastructure.manifest import SensorSpec, StrategySpec
from nexus.strategy import Strategy
from nexus.strategy.loader import instantiate_strategy, load_strategy_class


def _write_strategy_file(base: Path, rel_path: str, content: str) -> None:
    '''Create a strategy .py file.'''

    file_path = base / rel_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding='utf-8')


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

MISSING_ON_SAVE = '''
from nexus.strategy import Strategy as BaseStrategy

class Strategy(BaseStrategy):
    def on_load(self, data: bytes) -> None:
        pass
'''

MISSING_STRATEGY_CLASS = '''
class SomeOtherClass:
    pass
'''

NOT_A_CLASS = '''
Strategy = "not a class"
'''

EXPORTS_ABC = '''
from nexus.strategy import Strategy
'''

NOT_SUBCLASS = '''
class Strategy:
    def on_save(self) -> bytes:
        return b''

    def on_load(self, data: bytes) -> None:
        pass
'''

RUNTIME_ERROR = '''
import nonexistent_module
'''


class TestLoadStrategyClass:
    '''Tests for load_strategy_class function.'''

    def test_loads_valid_strategy(self) -> None:
        '''Valid strategy file loads successfully.'''

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_strategy_file(tmp_path, 'strategies/momentum.py', VALID_STRATEGY)

            strategy_class = load_strategy_class(
                Path('strategies/momentum.py'),
                tmp_path,
            )

            assert issubclass(strategy_class, Strategy)
            assert strategy_class is not Strategy

    def test_instantiate_loaded_class(self) -> None:
        '''Loaded strategy class can be instantiated.'''

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_strategy_file(tmp_path, 'strategies/momentum.py', VALID_STRATEGY)

            strategy_class = load_strategy_class(
                Path('strategies/momentum.py'),
                tmp_path,
            )

            instance = strategy_class('test_id')

            assert instance.strategy_id == 'test_id'
            assert instance.on_save() == b''

    def test_absolute_path_raises(self) -> None:
        '''Absolute file path raises ValueError.'''

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            with pytest.raises(ValueError, match='must be relative'):
                load_strategy_class(Path('/absolute/path.py'), tmp_path)

    def test_path_traversal_raises(self) -> None:
        '''Path traversal attempt raises ValueError.'''

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_strategy_file(tmp_path, 'strategies/momentum.py', VALID_STRATEGY)

            with pytest.raises(ValueError, match='escapes base path'):
                load_strategy_class(Path('../outside.py'), tmp_path)

    def test_file_not_found_raises(self) -> None:
        '''Missing file raises ValueError.'''

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            with pytest.raises(ValueError, match='not found'):
                load_strategy_class(Path('missing.py'), tmp_path)

    def test_missing_strategy_class_raises(self) -> None:
        '''File without Strategy class raises ValueError.'''

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_strategy_file(tmp_path, 'bad.py', MISSING_STRATEGY_CLASS)

            with pytest.raises(ValueError, match='missing Strategy class'):
                load_strategy_class(Path('bad.py'), tmp_path)

    def test_strategy_not_a_class_raises(self) -> None:
        '''Strategy attribute that is not a class raises ValueError.'''

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_strategy_file(tmp_path, 'bad.py', NOT_A_CLASS)

            with pytest.raises(ValueError, match='not a class'):
                load_strategy_class(Path('bad.py'), tmp_path)

    def test_strategy_not_subclass_raises(self) -> None:
        '''Strategy class not inheriting ABC raises ValueError.'''

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_strategy_file(tmp_path, 'bad.py', NOT_SUBCLASS)

            with pytest.raises(ValueError, match='does not inherit'):
                load_strategy_class(Path('bad.py'), tmp_path)

    def test_exports_abc_raises(self) -> None:
        '''File that exports the ABC itself raises ValueError.'''

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_strategy_file(tmp_path, 'bad.py', EXPORTS_ABC)

            with pytest.raises(ValueError, match='exports the ABC'):
                load_strategy_class(Path('bad.py'), tmp_path)

    def test_missing_abstract_method_raises_on_instantiate(self) -> None:
        '''Strategy missing abstract method raises TypeError on instantiation.'''

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_strategy_file(tmp_path, 'bad.py', MISSING_ON_SAVE)

            strategy_class = load_strategy_class(Path('bad.py'), tmp_path)

            with pytest.raises(TypeError, match='abstract'):
                strategy_class('test')

    def test_nested_path_loads(self) -> None:
        '''Nested directory path loads successfully.'''

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_strategy_file(
                tmp_path,
                'strategies/btc/momentum_v2.py',
                VALID_STRATEGY,
            )

            strategy_class = load_strategy_class(
                Path('strategies/btc/momentum_v2.py'),
                tmp_path,
            )

            assert issubclass(strategy_class, Strategy)

    def test_non_py_suffix_raises(self) -> None:
        '''Non-.py file suffix raises ValueError.'''

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            with pytest.raises(ValueError, match=r'must be a \.py file'):
                load_strategy_class(Path('strategy.txt'), tmp_path)

    def test_runtime_error_raises_valueerror(self) -> None:
        '''Runtime error in strategy module raises ValueError.'''

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_strategy_file(tmp_path, 'bad.py', RUNTIME_ERROR)

            with pytest.raises(ValueError, match='Failed to execute'):
                load_strategy_class(Path('bad.py'), tmp_path)


_EXP_DIR_HANDLE = tempfile.TemporaryDirectory()
_EXP_DIR = Path(_EXP_DIR_HANDLE.name)


def _make_spec(
    strategy_id: str = 'test_strategy',
    file: str = 'strategies/momentum.py',
) -> StrategySpec:
    '''Create a StrategySpec for testing.'''

    pfn = SensorSpec(
        experiment_dir=_EXP_DIR,
        permutation_ids=(1,),
        interval_seconds=60,
    )
    return StrategySpec(
        strategy_id=strategy_id,
        file=file,
        sensors=(pfn,),
        capital_pct=Decimal('50'),
    )


class TestInstantiateStrategy:
    '''Tests for instantiate_strategy function.'''

    def test_instantiates_from_spec(self) -> None:
        '''instantiate_strategy returns working Strategy instance.'''

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_strategy_file(tmp_path, 'strategies/momentum.py', VALID_STRATEGY)

            spec = _make_spec('momentum_v1', 'strategies/momentum.py')
            strategy = instantiate_strategy(spec, tmp_path)

            assert isinstance(strategy, Strategy)
            assert strategy.strategy_id == 'momentum_v1'

    def test_strategy_id_matches_spec(self) -> None:
        '''Instantiated strategy has strategy_id from spec.'''

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_strategy_file(tmp_path, 'my_strategy.py', VALID_STRATEGY)

            spec = _make_spec('custom_id_123', 'my_strategy.py')
            strategy = instantiate_strategy(spec, tmp_path)

            assert strategy.strategy_id == 'custom_id_123'

    def test_on_save_on_load_work(self) -> None:
        '''Instantiated strategy has working on_save/on_load.'''

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_strategy_file(tmp_path, 'strat.py', VALID_STRATEGY)

            spec = _make_spec(file='strat.py')
            strategy = instantiate_strategy(spec, tmp_path)

            assert strategy.on_save() == b''
            strategy.on_load(b'data')

    def test_invalid_file_raises(self) -> None:
        '''instantiate_strategy with missing file raises ValueError.'''

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            spec = _make_spec(file='missing.py')

            with pytest.raises(ValueError, match='not found'):
                instantiate_strategy(spec, tmp_path)
