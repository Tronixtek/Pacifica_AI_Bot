from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.mt5.models import MarketSpec
from app.risk.sizing import PositionSizer
from app.strategy.price_action import StrategyCandidate

if TYPE_CHECKING:
    from app.config import Settings
    from app.runtime.state import EngineRuntimeState


@dataclass(slots=True)
class RiskDecision:
    approved: bool
    reason: str
    # Lots, matching what MT5 trades in. Everything downstream - the order
    # request, paper positions, and PnL - uses the same unit, because mixing
    # lots with base-currency units silently rescales PnL by the contract size.
    size: float = 0.0
    notionalUsd: float = 0.0
    riskState: str = "normal"
    riskAmountUsd: float = 0.0
    marginRequiredUsd: float = 0.0


class RiskManager:
    def __init__(self, settings: "Settings", client=None) -> None:
        self.settings = settings
        self.sizer = PositionSizer(client)
        self.client = client

    async def review(
        self,
        candidate: StrategyCandidate,
        state: "EngineRuntimeState",
        spec: MarketSpec | None = None,
    ) -> RiskDecision:
        if candidate.symbol in state.positions:
            return RiskDecision(False, f"Existing {candidate.symbol} position is already open.")

        if len(state.positions) >= self.settings.maxOpenPositions:
            return RiskDecision(False, "Max open position count reached.")

        if candidate.confidence < self.settings.minSignalConfidence:
            return RiskDecision(
                False,
                (
                    f"Signal confidence {candidate.confidence:.2f} is below the"
                    f" {self.settings.minSignalConfidence:.2f} execution threshold."
                ),
                riskState="warning",
            )

        daily_loss_limit = state.startingEquityUsd * (self.settings.maxDailyLossPct / 100)
        if (
            self.settings.enforceDailyLossLimit
            and self.settings.maxDailyLossPct > 0
            and state.realizedPnlUsd <= -daily_loss_limit
        ):
            return RiskDecision(
                False,
                "Daily loss limit reached. Bot is in protective mode.",
                riskState="reduced",
            )

        if abs(candidate.entryPrice - candidate.stopLoss) <= 0:
            return RiskDecision(False, "Invalid stop distance produced by strategy.")

        # Risk budget scales with conviction but never exceeds the configured
        # percentage of current equity.
        risk_amount = state.currentEquityUsd * (self.settings.maxRiskPerTradePct / 100)
        risk_amount *= min(1.0, max(0.55, candidate.confidence))

        sizing = await self.sizer.size_for_risk(
            symbol=candidate.symbol,
            side=candidate.bias,
            entry_price=candidate.entryPrice,
            stop_loss=candidate.stopLoss,
            risk_amount=risk_amount,
            spec=spec,
        )
        if not sizing.ok:
            return RiskDecision(False, sizing.reason, riskState="warning")

        contract_size = spec.contractSize if spec and spec.contractSize else 1.0
        notional = sizing.lots * contract_size * candidate.entryPrice

        margin_required = await self._margin_for(candidate, sizing.lots)
        if margin_required is not None and margin_required > state.availableMarginUsd:
            return RiskDecision(
                False,
                (
                    f"Margin required {margin_required:.2f} exceeds available "
                    f"{state.availableMarginUsd:.2f}."
                ),
                riskState="warning",
            )

        return RiskDecision(
            True,
            f"Risk approved. {sizing.reason}",
            size=sizing.lots,
            notionalUsd=round(notional, 2),
            riskState="normal",
            riskAmountUsd=sizing.riskAmountUsd,
            marginRequiredUsd=round(margin_required or 0.0, 2),
        )

    async def _margin_for(self, candidate: StrategyCandidate, lots: float) -> float | None:
        """Margin the broker will hold, or None when it cannot be determined.

        This replaces a hardcoded `availableMargin * defaultLeverage` notional
        cap. That cap assumed 3:1 leverage; on a 2000:1 account it bound long
        before risk did and pinned every trade to the minimum lot.
        """
        if self.client is None:
            return None
        return await self.client.calc_margin(
            candidate.bias, candidate.symbol, lots, candidate.entryPrice
        )
