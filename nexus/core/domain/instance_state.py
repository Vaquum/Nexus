'''Composite runtime state for a Manager instance.

Composes capital, risk, positions, and operational mode into a
single top-level container. Created with `capital_pool` (the operational
allocation sourced from `Manifest.capital_pool`) at fresh startup via
`InstanceState.fresh()`.
'''

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from nexus.core.domain.capital_state import CapitalState
from nexus.core.domain.operational_mode import (
    ModeState,
    OperationalHolds,
    StrategyModeState,
)
from nexus.core.domain.position import Position
from nexus.core.domain.risk_state import RiskState

__all__ = ['InstanceState']


@dataclass
class InstanceState:
    '''Top-level runtime state for one Manager instance.

    Args:
        capital: Capital tracking state.
        risk: Instance-level and per-strategy risk metrics.
        positions: Open positions keyed by trade_id.
        mode: Instance-level operational mode.
        mode_holds: Non-health reasons (operator halt, risk breakers) that
            hold the instance in HALTED independently of the health-derived
            mode. Written only by `ModeController`; persisted so a halt and
            its cause survive restarts.
        strategy_modes: Per-strategy operational modes keyed by strategy_id.
        account_dust: Per-symbol base-asset residue from sub-lot
            position closes, keyed by symbol. Populated by
            `OutcomeProcessor._reduce_position` when a terminal
            full-close EXIT fill leaves residue (the venue snapped
            the qty and the remainder is below the venue's lot
            step) and by `OutcomeProcessor.close_as_dust` when the
            launcher's intake-time quantization rejects a
            full-close EXIT as sub-lot. Tracked symbol-keyed
            because the base-asset identity is unambiguous from the
            symbol (e.g., BTCUSDT → BTC residue). Persisted via WAL
            replay + snapshot save/load so dust survives restarts.
            See Vaquum/Nexus#82.
        processed_outcome_ids: `outcome_id`s already applied by
            `OutcomeProcessor.process`, persisted via WAL replay +
            snapshot save/load so the dedup set survives restarts.
            Persisted atomically with the mutation it guards (the
            launcher's `append_mutation` writes the whole state), so a
            boot replay of an already-applied outcome is recognised and
            not double-applied. See Vaquum/Nexus#86.
        processed_dust_close_ids: `dust_close_id`s already applied by
            `OutcomeProcessor.close_as_dust`, durable for the same
            reason as `processed_outcome_ids`.
    '''

    capital: CapitalState
    risk: RiskState = field(default_factory=RiskState)
    positions: dict[str, Position] = field(default_factory=dict)
    mode: ModeState = field(default_factory=ModeState)
    mode_holds: OperationalHolds = field(default_factory=OperationalHolds)
    strategy_modes: dict[str, StrategyModeState] = field(default_factory=dict)
    account_dust: dict[str, Decimal] = field(default_factory=dict)
    processed_outcome_ids: set[str] = field(default_factory=set)
    processed_dust_close_ids: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        '''Validate that dict keys match their value identifiers.'''

        for key, pos in self.positions.items():
            if not isinstance(pos, Position):
                msg = (
                    f'InstanceState.positions value for key {key!r} must be a Position'
                )
                raise ValueError(msg)
            if key != pos.trade_id:
                msg = f'InstanceState.positions key {key!r} does not match trade_id {pos.trade_id!r}'
                raise ValueError(msg)

        for key, sms in self.strategy_modes.items():
            if not isinstance(sms, StrategyModeState):
                msg = f'InstanceState.strategy_modes value for key {key!r} must be a StrategyModeState'
                raise ValueError(msg)
            if key != sms.strategy_id:
                msg = f'InstanceState.strategy_modes key {key!r} does not match strategy_id {sms.strategy_id!r}'
                raise ValueError(msg)

        for symbol, residue in self.account_dust.items():
            if not isinstance(symbol, str) or not symbol.strip():
                msg = f'InstanceState.account_dust key {symbol!r} must be a non-empty string'
                raise ValueError(msg)
            if (
                not isinstance(residue, Decimal)
                or not residue.is_finite()
                or residue < Decimal(0)
            ):
                msg = (
                    f'InstanceState.account_dust[{symbol!r}] must be a finite '
                    'non-negative Decimal'
                )
                raise ValueError(msg)

        for outcome_id in self.processed_outcome_ids:
            if not isinstance(outcome_id, str) or not outcome_id.strip():
                msg = 'InstanceState.processed_outcome_ids entries must be non-empty strings'
                raise ValueError(msg)

        for dust_close_id in self.processed_dust_close_ids:
            if not isinstance(dust_close_id, str) or not dust_close_id.strip():
                msg = 'InstanceState.processed_dust_close_ids entries must be non-empty strings'
                raise ValueError(msg)

    @classmethod
    def fresh(cls, capital_pool: Decimal) -> InstanceState:
        '''Create an initial empty state for a freshly-started instance.

        Args:
            capital_pool: Operational capital allocation in quote asset,
                sourced from `Manifest.capital_pool`. Becomes the initial
                `CapitalState.capital_pool` — NOT `Manifest.allocated_capital`,
                which is the infrastructure ceiling, not the operational
                allocation.

        Returns:
            Fresh InstanceState with capital pool set and everything else zeroed.

        Raises:
            ValueError: If `capital_pool` is not a finite positive Decimal.
        '''

        if not isinstance(capital_pool, Decimal) or not capital_pool.is_finite():
            msg = 'InstanceState.fresh(capital_pool) must receive a finite Decimal'
            raise ValueError(msg)

        if capital_pool <= 0:
            msg = 'InstanceState.fresh(capital_pool) must receive a positive value'
            raise ValueError(msg)

        return cls(
            capital=CapitalState(capital_pool=capital_pool),
        )
