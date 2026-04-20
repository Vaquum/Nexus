'''Runtime configuration for a Manager instance.

Frozen dataclass holding identity and validator-level tunables for a
single Manager instance (intake thresholds, STP mode, capital-pct
mapping, shutdown timeouts). Capital ceiling and operational allocation
are NOT tracked here — both `allocated_capital` and `capital_pool`
live on the strategy `Manifest` (see
`nexus.infrastructure.manifest.Manifest`). Additional fields (risk
limits, health policy, etc.) are added as their respective phases land.
'''

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType

from nexus.core.stp_mode import STPMode

__all__ = ['InstanceConfig']

_ZERO = Decimal('0')
_ONE_HUNDRED = Decimal('100')
_ALLOWED_REFERENCE_PRICE_SOURCES = frozenset({'origo_mid'})


@dataclass(frozen=True)
class InstanceConfig:
    '''Immutable runtime/validator config for one Manager instance.

    Identity and validator-level tunables. The capital ceiling
    (``allocated_capital``) and operational allocation (``capital_pool``)
    are NOT fields here — both live on the strategy
    `Manifest` and are sourced from YAML at startup.

    Args:
        account_id: Unique identifier for this instance's trading account.
        venue: Which venue to trade on (e.g. ``binance_spot``).
        duplicate_window_ms: Duplicate-order detection window for intake
            checks, in milliseconds.
        max_order_rate: Optional per-process cap on ENTER actions per second
            for intake rate-limiting within this Manager process. This is not
            a distributed/global limit across multiple processes or hosts.
            ``None`` disables the rate check.
        book_staleness_max_seconds: Optional Stage-3 price staleness threshold
            in seconds.
        max_spread_bps: Optional Stage-3 max spread threshold in bps.
        price_deviation_max_bps: Optional Stage-3 max deviation threshold in
            bps.
        reference_price_source: Optional Stage-3 reference price source
            identifier used for deviation checks.
        stp_mode: Self-trade prevention mode for order submission. Determines
            behavior when a new order would match the account's own resting
            order. Defaults to ``CANCEL_TAKER``.
        capital_pct: Strategy capital-allocation percentages keyed by
            strategy_id.
        shutdown_wait_timeout_seconds: Max seconds to wait for commands to
            reach terminal state during shutdown. Defaults to 30.
        shutdown_abort_timeout_seconds: Max seconds to wait after issuing
            ABORT commands during shutdown. Defaults to 10.
    '''

    account_id: str
    venue: str
    duplicate_window_ms: int = 1000
    max_order_rate: int | None = None
    book_staleness_max_seconds: int | None = None
    max_spread_bps: Decimal | None = None
    price_deviation_max_bps: Decimal | None = None
    reference_price_source: str | None = None
    stp_mode: STPMode = STPMode.CANCEL_TAKER
    capital_pct: Mapping[str, Decimal] = field(default_factory=dict)
    shutdown_wait_timeout_seconds: float = 30.0
    shutdown_abort_timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        '''Validate configuration invariants.'''

        if not self.account_id or not self.account_id.strip():
            msg = 'InstanceConfig.account_id must be a non-empty string'
            raise ValueError(msg)

        if not self.venue or not self.venue.strip():
            msg = 'InstanceConfig.venue must be a non-empty string'
            raise ValueError(msg)

        if isinstance(self.duplicate_window_ms, bool) or not isinstance(
            self.duplicate_window_ms,
            int,
        ):
            msg = 'InstanceConfig.duplicate_window_ms must be an integer'
            raise ValueError(msg)

        if self.duplicate_window_ms <= 0:
            msg = 'InstanceConfig.duplicate_window_ms must be a positive integer'
            raise ValueError(msg)

        if self.max_order_rate is not None:
            if isinstance(self.max_order_rate, bool) or not isinstance(
                self.max_order_rate,
                int,
            ):
                msg = 'InstanceConfig.max_order_rate must be an integer'
                raise ValueError(msg)

            if self.max_order_rate <= 0:
                msg = 'InstanceConfig.max_order_rate must be a positive integer'
                raise ValueError(msg)

        if self.book_staleness_max_seconds is not None:
            if isinstance(self.book_staleness_max_seconds, bool) or not isinstance(
                self.book_staleness_max_seconds,
                int,
            ):
                msg = 'InstanceConfig.book_staleness_max_seconds must be an integer'
                raise ValueError(msg)

            if self.book_staleness_max_seconds <= 0:
                msg = (
                    'InstanceConfig.book_staleness_max_seconds must be a positive '
                    'integer'
                )
                raise ValueError(msg)

        for field_name in ('max_spread_bps', 'price_deviation_max_bps'):
            value = getattr(self, field_name)
            if value is None:
                continue
            if not isinstance(value, Decimal) or not value.is_finite() or value < _ZERO:
                msg = (
                    f'InstanceConfig.{field_name} must be a finite non-negative Decimal'
                )
                raise ValueError(msg)

        if self.reference_price_source is not None:
            if not isinstance(self.reference_price_source, str):
                msg = 'InstanceConfig.reference_price_source must be a string'
                raise ValueError(msg)

            normalized_reference_price_source = (
                self.reference_price_source.strip().lower()
            )
            if not normalized_reference_price_source:
                msg = 'InstanceConfig.reference_price_source must be a non-empty string'
                raise ValueError(msg)

            if (
                normalized_reference_price_source
                not in _ALLOWED_REFERENCE_PRICE_SOURCES
            ):
                allowed_values = ', '.join(sorted(_ALLOWED_REFERENCE_PRICE_SOURCES))
                msg = (
                    'InstanceConfig.reference_price_source must be one of '
                    f'{allowed_values}'
                )
                raise ValueError(msg)

            object.__setattr__(
                self,
                'reference_price_source',
                normalized_reference_price_source,
            )

        if (
            self.price_deviation_max_bps is not None
            and self.reference_price_source is None
        ):
            msg = (
                'InstanceConfig.reference_price_source is required when '
                'price_deviation_max_bps is set'
            )
            raise ValueError(msg)

        if not isinstance(self.stp_mode, STPMode):
            msg = 'InstanceConfig.stp_mode must be an STPMode member'
            raise ValueError(msg)

        if not isinstance(self.capital_pct, Mapping):
            msg = (
                'InstanceConfig.capital_pct must be a mapping of strategy_id to Decimal'
            )
            raise ValueError(msg)

        normalized_capital_pct: dict[str, Decimal] = {}
        total_pct = _ZERO
        for raw_strategy_id, pct in self.capital_pct.items():
            if not isinstance(raw_strategy_id, str):
                msg = 'InstanceConfig.capital_pct keys must be non-empty strings'
                raise ValueError(msg)

            strategy_id = raw_strategy_id.strip()
            if not strategy_id:
                msg = 'InstanceConfig.capital_pct keys must be non-empty strings'
                raise ValueError(msg)
            if strategy_id in normalized_capital_pct:
                msg = (
                    'InstanceConfig.capital_pct contains duplicate keys after '
                    'normalization'
                )
                raise ValueError(msg)
            if not isinstance(pct, Decimal) or not pct.is_finite():
                msg = 'InstanceConfig.capital_pct values must be finite Decimals'
                raise ValueError(msg)
            if pct <= _ZERO or pct > _ONE_HUNDRED:
                msg = 'InstanceConfig.capital_pct values must be in (0, 100]'
                raise ValueError(msg)
            total_pct += pct
            normalized_capital_pct[strategy_id] = pct

        if total_pct > _ONE_HUNDRED:
            msg = 'InstanceConfig.capital_pct total must be <= 100'
            raise ValueError(msg)

        object.__setattr__(
            self,
            'capital_pct',
            MappingProxyType(normalized_capital_pct),
        )

        for field_name in (
            'shutdown_wait_timeout_seconds',
            'shutdown_abort_timeout_seconds',
        ):
            value = getattr(self, field_name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                msg = f'InstanceConfig.{field_name} must be a finite positive number'
                raise ValueError(msg)
