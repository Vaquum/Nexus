'''Verify price-stage contract and placeholder check behavior.'''

from __future__ import annotations

from decimal import Decimal
from typing import Any, cast

import pytest

from nexus.core.domain.instance_state import InstanceState
from nexus.core.validator import (
    PriceCheckSnapshot,
    PriceFailureConsequence,
    PriceStageLimits,
    ValidationRequestContext,
    ValidationStage,
    build_price_stage_limits_from_config,
    derive_price_failure_consequence,
    validate_price_stage,
)
from nexus.instance_config import InstanceConfig


def _make_context(**overrides: Any) -> ValidationRequestContext:
    config = InstanceConfig(
        account_id='acc_001',
        venue='binance_spot',
        allocated_capital=Decimal('10000'),
    )
    defaults: dict[str, Any] = {
        'strategy_id': 'strat_a',
        'command_id': 'cmd_price_1',
        'order_notional': Decimal('100'),
        'estimated_fees': Decimal('1'),
        'strategy_budget': Decimal('5000'),
        'state': InstanceState.from_config(config),
        'config': config,
    }
    defaults.update(overrides)
    return ValidationRequestContext(**defaults)


class TestPriceContracts:
    def test_rejects_negative_staleness_limit(self) -> None:
        with pytest.raises(ValueError, match='max_staleness_ms'):
            PriceStageLimits(max_staleness_ms=-1)

    def test_rejects_bool_staleness_limit(self) -> None:
        with pytest.raises(ValueError, match='max_staleness_ms'):
            PriceStageLimits(max_staleness_ms=True)

    def test_rejects_negative_spread_limit(self) -> None:
        with pytest.raises(ValueError, match='max_spread_bps'):
            PriceStageLimits(max_spread_bps=Decimal('-1'))

    def test_rejects_negative_snapshot_spread(self) -> None:
        with pytest.raises(ValueError, match='spread_bps'):
            PriceCheckSnapshot(spread_bps=Decimal('-1'))

    def test_rejects_bool_snapshot_times(self) -> None:
        with pytest.raises(ValueError, match='now_ms'):
            PriceCheckSnapshot(now_ms=True)

        with pytest.raises(ValueError, match='book_timestamp_ms'):
            PriceCheckSnapshot(book_timestamp_ms=False)

    def test_rejects_empty_snapshot_reference_source(self) -> None:
        with pytest.raises(ValueError, match='reference_price_source'):
            PriceCheckSnapshot(reference_price_source='   ')

    def test_rejects_empty_limits_reference_source(self) -> None:
        with pytest.raises(ValueError, match='reference_price_source'):
            PriceStageLimits(reference_price_source='')

    def test_build_limits_maps_seconds_to_milliseconds(self) -> None:
        config = InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            allocated_capital=Decimal('10000'),
            book_staleness_max_seconds=3,
            max_spread_bps=Decimal('7'),
            price_deviation_max_bps=Decimal('9'),
            reference_price_source='origo_mid',
        )

        limits = build_price_stage_limits_from_config(config)

        assert limits.max_staleness_ms == 3000
        assert limits.max_spread_bps == Decimal('7')
        assert limits.max_deviation_bps == Decimal('9')
        assert limits.reference_price_source == 'origo_mid'

    def test_build_limits_keeps_none_when_staleness_seconds_missing(self) -> None:
        config = InstanceConfig(
            account_id='acc_001',
            venue='binance_spot',
            allocated_capital=Decimal('10000'),
        )

        limits = build_price_stage_limits_from_config(config)

        assert limits.max_staleness_ms is None

    def test_build_limits_requires_instance_config(self) -> None:
        with pytest.raises(ValueError, match='InstanceConfig'):
            build_price_stage_limits_from_config(
                cast(InstanceConfig, cast(object, None)),
            )


class TestPriceStage:
    def test_allows_with_no_limits(self) -> None:
        decision = validate_price_stage(
            _make_context(),
            PriceStageLimits(),
            snapshot=None,
        )
        assert decision.allowed is True

    def test_denies_when_stale(self) -> None:
        decision = validate_price_stage(
            _make_context(),
            PriceStageLimits(max_staleness_ms=100),
            snapshot=PriceCheckSnapshot(now_ms=1200, book_timestamp_ms=1000),
        )
        assert decision.allowed is False
        assert decision.failed_stage == ValidationStage.PRICE
        assert decision.reason_code == 'PRICE_BOOK_STALE'

    def test_denies_when_spread_exceeds_limit(self) -> None:
        decision = validate_price_stage(
            _make_context(),
            PriceStageLimits(max_spread_bps=Decimal('5')),
            snapshot=PriceCheckSnapshot(spread_bps=Decimal('8')),
        )
        assert decision.allowed is False
        assert decision.reason_code == 'PRICE_SPREAD_LIMIT'

    def test_denies_when_deviation_exceeds_limit(self) -> None:
        decision = validate_price_stage(
            _make_context(),
            PriceStageLimits(
                max_deviation_bps=Decimal('10'),
                reference_price_source='origo_mid',
            ),
            snapshot=PriceCheckSnapshot(
                deviation_bps=Decimal('11'),
                reference_price_source='origo_mid',
            ),
        )
        assert decision.allowed is False
        assert decision.reason_code == 'PRICE_DEVIATION_LIMIT'

    def test_denies_when_snapshot_missing_for_configured_limit(self) -> None:
        decision = validate_price_stage(
            _make_context(),
            PriceStageLimits(max_spread_bps=Decimal('5')),
            snapshot=None,
        )
        assert decision.allowed is False
        assert decision.reason_code == 'PRICE_SYSTEM_DATA_UNAVAILABLE'

    def test_denies_when_staleness_inputs_missing(self) -> None:
        decision = validate_price_stage(
            _make_context(),
            PriceStageLimits(max_staleness_ms=100),
            snapshot=PriceCheckSnapshot(now_ms=None, book_timestamp_ms=1000),
        )
        assert decision.allowed is False
        assert decision.reason_code == 'PRICE_SYSTEM_DATA_UNAVAILABLE'

    def test_allows_when_within_limits(self) -> None:
        decision = validate_price_stage(
            _make_context(),
            PriceStageLimits(
                max_staleness_ms=500,
                max_spread_bps=Decimal('10'),
                max_deviation_bps=Decimal('15'),
                reference_price_source='origo_mid',
            ),
            snapshot=PriceCheckSnapshot(
                now_ms=1500,
                book_timestamp_ms=1200,
                spread_bps=Decimal('6'),
                deviation_bps=Decimal('8'),
                reference_price_source='origo_mid',
            ),
        )
        assert decision.allowed is True

    def test_denies_when_deviation_limit_has_no_reference_source(self) -> None:
        decision = validate_price_stage(
            _make_context(),
            PriceStageLimits(max_deviation_bps=Decimal('10')),
            snapshot=PriceCheckSnapshot(
                deviation_bps=Decimal('5'),
                reference_price_source='origo_mid',
            ),
        )
        assert decision.allowed is False
        assert decision.failed_stage == ValidationStage.PRICE
        assert decision.reason_code == 'PRICE_SYSTEM_DATA_UNAVAILABLE'
        assert decision.message == (
            'Price system data unavailable: reference_price_source missing '
            'for deviation validation'
        )

    def test_denies_when_deviation_snapshot_source_missing(self) -> None:
        decision = validate_price_stage(
            _make_context(),
            PriceStageLimits(
                max_deviation_bps=Decimal('10'),
                reference_price_source='origo_mid',
            ),
            snapshot=PriceCheckSnapshot(deviation_bps=Decimal('5')),
        )
        assert decision.allowed is False
        assert decision.failed_stage == ValidationStage.PRICE
        assert decision.reason_code == 'PRICE_SYSTEM_DATA_UNAVAILABLE'
        assert decision.message == (
            'Price system data unavailable: deviation_bps/reference_price_source '
            'missing'
        )

    def test_denies_when_deviation_snapshot_source_mismatches(self) -> None:
        decision = validate_price_stage(
            _make_context(),
            PriceStageLimits(
                max_deviation_bps=Decimal('10'),
                reference_price_source='origo_mid',
            ),
            snapshot=PriceCheckSnapshot(
                deviation_bps=Decimal('5'),
                reference_price_source='origo_last',
            ),
        )
        assert decision.allowed is False
        assert decision.failed_stage == ValidationStage.PRICE
        assert decision.reason_code == 'PRICE_SNAPSHOT_INVALID'
        assert decision.message == 'Price snapshot reference source is inconsistent'


class TestPriceFailureConsequence:
    def test_system_data_unavailable_routes_to_platform(self) -> None:
        decision = validate_price_stage(
            _make_context(),
            PriceStageLimits(max_spread_bps=Decimal('5')),
            snapshot=None,
        )
        consequence = derive_price_failure_consequence(decision)

        assert consequence == PriceFailureConsequence(
            notify_strategy_owner=True,
            notify_platform_ops=True,
            severity='critical',
        )

    def test_spread_limit_stays_strategy_scoped(self) -> None:
        decision = validate_price_stage(
            _make_context(),
            PriceStageLimits(max_spread_bps=Decimal('5')),
            snapshot=PriceCheckSnapshot(spread_bps=Decimal('7')),
        )
        consequence = derive_price_failure_consequence(decision)

        assert consequence == PriceFailureConsequence(
            notify_strategy_owner=True,
            notify_platform_ops=False,
            severity='warning',
        )

    def test_allowed_decision_has_no_consequence(self) -> None:
        allow = validate_price_stage(_make_context(), PriceStageLimits(), snapshot=None)
        assert allow.allowed is True
        assert derive_price_failure_consequence(allow) is None
