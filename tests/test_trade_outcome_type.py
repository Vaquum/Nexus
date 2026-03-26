from nexus.infrastructure.praxis_connector.trade_outcome_type import TradeOutcomeType


def test_enum_has_six_members() -> None:
    assert len(TradeOutcomeType) == 6


def test_enum_values() -> None:
    assert TradeOutcomeType.ACK.value == 'ACK'
    assert TradeOutcomeType.PARTIAL.value == 'PARTIAL'
    assert TradeOutcomeType.FILLED.value == 'FILLED'
    assert TradeOutcomeType.REJECTED.value == 'REJECTED'
    assert TradeOutcomeType.EXPIRED.value == 'EXPIRED'
    assert TradeOutcomeType.CANCELED.value == 'CANCELED'


def test_is_terminal_for_terminal_outcomes() -> None:
    assert TradeOutcomeType.FILLED.is_terminal is True
    assert TradeOutcomeType.REJECTED.is_terminal is True
    assert TradeOutcomeType.EXPIRED.is_terminal is True
    assert TradeOutcomeType.CANCELED.is_terminal is True


def test_is_terminal_for_non_terminal_outcomes() -> None:
    assert TradeOutcomeType.ACK.is_terminal is False
    assert TradeOutcomeType.PARTIAL.is_terminal is False


def test_is_fill_for_fill_outcomes() -> None:
    assert TradeOutcomeType.PARTIAL.is_fill is True
    assert TradeOutcomeType.FILLED.is_fill is True


def test_is_fill_for_non_fill_outcomes() -> None:
    assert TradeOutcomeType.ACK.is_fill is False
    assert TradeOutcomeType.REJECTED.is_fill is False
    assert TradeOutcomeType.EXPIRED.is_fill is False
    assert TradeOutcomeType.CANCELED.is_fill is False
