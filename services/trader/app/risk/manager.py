from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.strategy.price_action import StrategyCandidate

if TYPE_CHECKING:
    from app.config import Settings
    from app.runtime.state import EngineRuntimeState


@dataclass(slots=True)
class RiskDecision:
    approved: bool
    reason: str
    size: float = 0.0
    notionalUsd: float = 0.0
    riskState: str = "normal"


class RiskManager:
    def __init__(self, settings: "Settings") -> None:
        self.settings = settings

    def review(self, candidate: StrategyCandidate, state: "EngineRuntimeState") -> RiskDecision:
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

        stop_distance = abs(candidate.entryPrice - candidate.stopLoss)
        if stop_distance <= 0:
            return RiskDecision(False, "Invalid stop distance produced by strategy.")

        risk_capital = state.currentEquityUsd * (self.settings.maxRiskPerTradePct / 100)
        confidence_scale = min(1.0, max(0.55, candidate.confidence))
        risk_capital *= confidence_scale
        raw_size = risk_capital / stop_distance
        notional = raw_size * candidate.entryPrice
        max_notional = max(state.availableMarginUsd * self.settings.defaultLeverage, 0.0)
        capped_notional = min(notional, max_notional)

        if capped_notional <= 0:
            return RiskDecision(False, "No margin available for a new trade.", riskState="warning")

        size = capped_notional / candidate.entryPrice
        risk_state = "warning" if capped_notional < notional else "normal"
        reason = (
            "Risk approved with capped size due to margin limits."
            if risk_state == "warning"
            else "Risk approved."
        )
        return RiskDecision(
            True,
            reason,
            size=round(size, 6),
            notionalUsd=round(capped_notional, 2),
            riskState=risk_state,
        )
