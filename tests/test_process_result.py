import pytest

from nexus.infrastructure.praxis_connector.process_result import ProcessResult
from nexus.infrastructure.praxis_connector.trade_outcome_type import TradeOutcomeType


class TestProcessResultConstruction:

    def test_success_result_valid(self) -> None:
        result = ProcessResult(
            success=True,
            outcome_type=TradeOutcomeType.FILLED,
            position_updated=True,
            capital_updated=True,
        )
        assert result.success is True
        assert result.outcome_type == TradeOutcomeType.FILLED
        assert result.position_updated is True
        assert result.capital_updated is True
        assert result.error_reason is None

    def test_failure_result_valid(self) -> None:
        result = ProcessResult(
            success=False,
            outcome_type=TradeOutcomeType.FILLED,
            error_reason='Insufficient fee_reserve',
        )
        assert result.success is False
        assert result.error_reason == 'Insufficient fee_reserve'

    def test_ack_result_valid(self) -> None:
        result = ProcessResult(
            success=True,
            outcome_type=TradeOutcomeType.ACK,
            capital_updated=True,
        )
        assert result.outcome_type == TradeOutcomeType.ACK
        assert result.capital_updated is True
        assert result.position_updated is False

    def test_frozen(self) -> None:
        result = ProcessResult(
            success=True,
            outcome_type=TradeOutcomeType.ACK,
        )
        with pytest.raises(AttributeError):
            result.success = False  # type: ignore[misc]


class TestProcessResultValidation:

    def test_invalid_outcome_type_rejected(self) -> None:
        with pytest.raises(ValueError, match='outcome_type must be a TradeOutcomeType'):
            ProcessResult(
                success=True,
                outcome_type='FILLED',  # type: ignore[arg-type]
            )

    def test_failure_without_error_reason_rejected(self) -> None:
        with pytest.raises(ValueError, match='error_reason required when success is False'):
            ProcessResult(
                success=False,
                outcome_type=TradeOutcomeType.REJECTED,
            )

    def test_success_with_error_reason_rejected(self) -> None:
        with pytest.raises(ValueError, match='error_reason must be None when success is True'):
            ProcessResult(
                success=True,
                outcome_type=TradeOutcomeType.ACK,
                error_reason='Should not be here',
            )


class TestProcessResultDefaults:

    def test_defaults_for_minimal_success(self) -> None:
        result = ProcessResult(
            success=True,
            outcome_type=TradeOutcomeType.ACK,
        )
        assert result.position_updated is False
        assert result.capital_updated is False
        assert result.error_reason is None

    def test_defaults_for_minimal_failure(self) -> None:
        result = ProcessResult(
            success=False,
            outcome_type=TradeOutcomeType.REJECTED,
            error_reason='Order rejected',
        )
        assert result.position_updated is False
        assert result.capital_updated is False
