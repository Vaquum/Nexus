from datetime import datetime, timezone
from decimal import Decimal

import pytest

from nexus.infrastructure.praxis_connector.trade_outcome import TradeOutcome
from nexus.infrastructure.praxis_connector.trade_outcome_type import TradeOutcomeType


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _ack_outcome() -> TradeOutcome:
    return TradeOutcome(
        outcome_id='out_001',
        command_id='cmd_001',
        outcome_type=TradeOutcomeType.ACK,
        timestamp=_now(),
        remaining_size=Decimal('0.01'),
    )


def _fill_outcome(
    outcome_type: TradeOutcomeType = TradeOutcomeType.FILLED,
) -> TradeOutcome:
    return TradeOutcome(
        outcome_id='out_001',
        command_id='cmd_001',
        outcome_type=outcome_type,
        timestamp=_now(),
        fill_size=Decimal('0.01'),
        fill_price=Decimal('50000'),
        fill_notional=Decimal('500'),
        actual_fees=Decimal('0.5'),
    )


class TestTradeOutcomeConstruction:
    def test_ack_outcome_valid(self) -> None:
        outcome = _ack_outcome()
        assert outcome.outcome_type == TradeOutcomeType.ACK
        assert outcome.remaining_size == Decimal('0.01')

    def test_filled_outcome_valid(self) -> None:
        outcome = _fill_outcome()
        assert outcome.outcome_type == TradeOutcomeType.FILLED
        assert outcome.fill_size == Decimal('0.01')
        assert outcome.fill_price == Decimal('50000')
        assert outcome.fill_notional == Decimal('500')
        assert outcome.actual_fees == Decimal('0.5')

    def test_partial_outcome_valid(self) -> None:
        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.PARTIAL,
            timestamp=_now(),
            fill_size=Decimal('0.005'),
            fill_price=Decimal('50000'),
            fill_notional=Decimal('250'),
            actual_fees=Decimal('0.25'),
            remaining_size=Decimal('0.005'),
        )
        assert outcome.outcome_type == TradeOutcomeType.PARTIAL
        assert outcome.remaining_size == Decimal('0.005')

    def test_rejected_outcome_valid(self) -> None:
        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.REJECTED,
            timestamp=_now(),
            reject_reason='Insufficient balance',
        )
        assert outcome.outcome_type == TradeOutcomeType.REJECTED
        assert outcome.reject_reason == 'Insufficient balance'

    def test_expired_outcome_valid(self) -> None:
        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.EXPIRED,
            timestamp=_now(),
        )
        assert outcome.outcome_type == TradeOutcomeType.EXPIRED

    def test_canceled_outcome_valid(self) -> None:
        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.CANCELED,
            timestamp=_now(),
            cancel_reason='User requested',
        )
        assert outcome.outcome_type == TradeOutcomeType.CANCELED
        assert outcome.cancel_reason == 'User requested'

    def test_canceled_outcome_without_reason_valid(self) -> None:
        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.CANCELED,
            timestamp=_now(),
        )
        assert outcome.cancel_reason is None

    def test_frozen(self) -> None:
        outcome = _ack_outcome()
        with pytest.raises(AttributeError):
            outcome.outcome_id = 'changed'  # type: ignore[misc]


class TestTradeOutcomeIdValidation:
    def test_empty_outcome_id_rejected(self) -> None:
        with pytest.raises(ValueError, match='outcome_id must be a non-empty string'):
            TradeOutcome(
                outcome_id='',
                command_id='cmd_001',
                outcome_type=TradeOutcomeType.ACK,
                timestamp=_now(),
            )

    def test_whitespace_outcome_id_rejected(self) -> None:
        with pytest.raises(ValueError, match='outcome_id must be a non-empty string'):
            TradeOutcome(
                outcome_id='   ',
                command_id='cmd_001',
                outcome_type=TradeOutcomeType.ACK,
                timestamp=_now(),
            )

    def test_empty_command_id_rejected(self) -> None:
        with pytest.raises(ValueError, match='command_id must be a non-empty string'):
            TradeOutcome(
                outcome_id='out_001',
                command_id='',
                outcome_type=TradeOutcomeType.ACK,
                timestamp=_now(),
            )

    def test_whitespace_command_id_rejected(self) -> None:
        with pytest.raises(ValueError, match='command_id must be a non-empty string'):
            TradeOutcome(
                outcome_id='out_001',
                command_id='   ',
                outcome_type=TradeOutcomeType.ACK,
                timestamp=_now(),
            )


class TestTradeOutcomeTimestampValidation:
    def test_non_datetime_timestamp_rejected(self) -> None:
        with pytest.raises(ValueError, match='timestamp must be a datetime instance'):
            TradeOutcome(
                outcome_id='out_001',
                command_id='cmd_001',
                outcome_type=TradeOutcomeType.ACK,
                timestamp='2024-01-01T00:00:00Z',  # type: ignore[arg-type]
            )

    def test_naive_timestamp_rejected(self) -> None:
        with pytest.raises(ValueError, match='timestamp must be timezone-aware'):
            TradeOutcome(
                outcome_id='out_001',
                command_id='cmd_001',
                outcome_type=TradeOutcomeType.ACK,
                timestamp=datetime.now(),
            )


class TestTradeOutcomeFillValidation:
    def test_filled_missing_fill_size_rejected(self) -> None:
        with pytest.raises(ValueError, match='fill_size required for fill outcomes'):
            TradeOutcome(
                outcome_id='out_001',
                command_id='cmd_001',
                outcome_type=TradeOutcomeType.FILLED,
                timestamp=_now(),
                fill_price=Decimal('50000'),
                fill_notional=Decimal('500'),
                actual_fees=Decimal('0.5'),
            )

    def test_filled_missing_fill_price_rejected(self) -> None:
        with pytest.raises(ValueError, match='fill_price required for fill outcomes'):
            TradeOutcome(
                outcome_id='out_001',
                command_id='cmd_001',
                outcome_type=TradeOutcomeType.FILLED,
                timestamp=_now(),
                fill_size=Decimal('0.01'),
                fill_notional=Decimal('500'),
                actual_fees=Decimal('0.5'),
            )

    def test_filled_missing_fill_notional_rejected(self) -> None:
        with pytest.raises(
            ValueError, match='fill_notional required for fill outcomes'
        ):
            TradeOutcome(
                outcome_id='out_001',
                command_id='cmd_001',
                outcome_type=TradeOutcomeType.FILLED,
                timestamp=_now(),
                fill_size=Decimal('0.01'),
                fill_price=Decimal('50000'),
                actual_fees=Decimal('0.5'),
            )

    def test_filled_missing_actual_fees_rejected(self) -> None:
        with pytest.raises(ValueError, match='actual_fees required for fill outcomes'):
            TradeOutcome(
                outcome_id='out_001',
                command_id='cmd_001',
                outcome_type=TradeOutcomeType.FILLED,
                timestamp=_now(),
                fill_size=Decimal('0.01'),
                fill_price=Decimal('50000'),
                fill_notional=Decimal('500'),
            )

    def test_partial_missing_fill_fields_rejected(self) -> None:
        with pytest.raises(ValueError, match='fill_size required for fill outcomes'):
            TradeOutcome(
                outcome_id='out_001',
                command_id='cmd_001',
                outcome_type=TradeOutcomeType.PARTIAL,
                timestamp=_now(),
            )

    def test_fill_size_zero_rejected(self) -> None:
        with pytest.raises(ValueError, match='fill_size must be positive'):
            TradeOutcome(
                outcome_id='out_001',
                command_id='cmd_001',
                outcome_type=TradeOutcomeType.FILLED,
                timestamp=_now(),
                fill_size=Decimal('0'),
                fill_price=Decimal('50000'),
                fill_notional=Decimal('500'),
                actual_fees=Decimal('0.5'),
            )

    def test_fill_size_negative_rejected(self) -> None:
        with pytest.raises(ValueError, match='fill_size must be positive'):
            TradeOutcome(
                outcome_id='out_001',
                command_id='cmd_001',
                outcome_type=TradeOutcomeType.FILLED,
                timestamp=_now(),
                fill_size=Decimal('-0.01'),
                fill_price=Decimal('50000'),
                fill_notional=Decimal('500'),
                actual_fees=Decimal('0.5'),
            )

    def test_fill_size_nan_rejected(self) -> None:
        with pytest.raises(ValueError, match='fill_size must be a finite Decimal'):
            TradeOutcome(
                outcome_id='out_001',
                command_id='cmd_001',
                outcome_type=TradeOutcomeType.FILLED,
                timestamp=_now(),
                fill_size=Decimal('NaN'),
                fill_price=Decimal('50000'),
                fill_notional=Decimal('500'),
                actual_fees=Decimal('0.5'),
            )

    def test_fill_price_zero_rejected(self) -> None:
        with pytest.raises(ValueError, match='fill_price must be positive'):
            TradeOutcome(
                outcome_id='out_001',
                command_id='cmd_001',
                outcome_type=TradeOutcomeType.FILLED,
                timestamp=_now(),
                fill_size=Decimal('0.01'),
                fill_price=Decimal('0'),
                fill_notional=Decimal('500'),
                actual_fees=Decimal('0.5'),
            )

    def test_fill_notional_zero_rejected(self) -> None:
        with pytest.raises(ValueError, match='fill_notional must be positive'):
            TradeOutcome(
                outcome_id='out_001',
                command_id='cmd_001',
                outcome_type=TradeOutcomeType.FILLED,
                timestamp=_now(),
                fill_size=Decimal('0.01'),
                fill_price=Decimal('50000'),
                fill_notional=Decimal('0'),
                actual_fees=Decimal('0.5'),
            )

    def test_actual_fees_negative_rejected(self) -> None:
        with pytest.raises(ValueError, match='actual_fees must be non-negative'):
            TradeOutcome(
                outcome_id='out_001',
                command_id='cmd_001',
                outcome_type=TradeOutcomeType.FILLED,
                timestamp=_now(),
                fill_size=Decimal('0.01'),
                fill_price=Decimal('50000'),
                fill_notional=Decimal('500'),
                actual_fees=Decimal('-0.5'),
            )

    def test_actual_fees_zero_valid(self) -> None:
        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.FILLED,
            timestamp=_now(),
            fill_size=Decimal('0.01'),
            fill_price=Decimal('50000'),
            fill_notional=Decimal('500'),
            actual_fees=Decimal('0'),
        )
        assert outcome.actual_fees == Decimal('0')


class TestTradeOutcomeRejectValidation:
    def test_rejected_missing_reason_rejected(self) -> None:
        with pytest.raises(ValueError, match='reject_reason required for REJECTED'):
            TradeOutcome(
                outcome_id='out_001',
                command_id='cmd_001',
                outcome_type=TradeOutcomeType.REJECTED,
                timestamp=_now(),
            )

    def test_rejected_empty_reason_rejected(self) -> None:
        with pytest.raises(ValueError, match='reject_reason required for REJECTED'):
            TradeOutcome(
                outcome_id='out_001',
                command_id='cmd_001',
                outcome_type=TradeOutcomeType.REJECTED,
                timestamp=_now(),
                reject_reason='',
            )

    def test_rejected_whitespace_reason_rejected(self) -> None:
        with pytest.raises(ValueError, match='reject_reason required for REJECTED'):
            TradeOutcome(
                outcome_id='out_001',
                command_id='cmd_001',
                outcome_type=TradeOutcomeType.REJECTED,
                timestamp=_now(),
                reject_reason='   ',
            )


class TestTradeOutcomeRemainingSizeValidation:
    def test_remaining_size_negative_rejected(self) -> None:
        with pytest.raises(ValueError, match='remaining_size must be non-negative'):
            TradeOutcome(
                outcome_id='out_001',
                command_id='cmd_001',
                outcome_type=TradeOutcomeType.ACK,
                timestamp=_now(),
                remaining_size=Decimal('-0.01'),
            )

    def test_remaining_size_nan_rejected(self) -> None:
        with pytest.raises(ValueError, match='remaining_size must be a finite Decimal'):
            TradeOutcome(
                outcome_id='out_001',
                command_id='cmd_001',
                outcome_type=TradeOutcomeType.ACK,
                timestamp=_now(),
                remaining_size=Decimal('NaN'),
            )

    def test_remaining_size_zero_valid(self) -> None:
        outcome = TradeOutcome(
            outcome_id='out_001',
            command_id='cmd_001',
            outcome_type=TradeOutcomeType.ACK,
            timestamp=_now(),
            remaining_size=Decimal('0'),
        )
        assert outcome.remaining_size == Decimal('0')
