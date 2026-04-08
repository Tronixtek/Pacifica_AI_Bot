from __future__ import annotations

from dataclasses import dataclass
from statistics import mean

from app.contracts import SignalBias, SignalSetup


@dataclass(slots=True)
class StrategyCandidate:
    symbol: str
    setup: SignalSetup
    bias: SignalBias
    confidence: float
    entryPrice: float
    stopLoss: float
    takeProfit: float
    reason: str


class PriceActionStrategy:
    def __init__(
        self,
        breakout_window: int = 20,
        sweep_window: int = 12,
        trend_fast_window: int = 8,
        trend_slow_window: int = 34,
        momentum_window: int = 5,
        breakout_buffer: float = 0.0016,
        reward_to_risk: float = 2.1,
    ) -> None:
        self.breakout_window = breakout_window
        self.sweep_window = sweep_window
        self.trend_fast_window = trend_fast_window
        self.trend_slow_window = trend_slow_window
        self.momentum_window = momentum_window
        self.breakout_buffer = breakout_buffer
        self.reward_to_risk = reward_to_risk

    def evaluate(self, symbol: str, prices: list[float]) -> list[StrategyCandidate]:
        min_points = max(
            self.breakout_window + 2,
            self.sweep_window + 3,
            self.trend_slow_window + 2,
            self.momentum_window + 2,
        )
        if len(prices) < min_points:
            return []

        current = prices[-1]
        previous = prices[-2]
        range_window = prices[-(self.breakout_window + 1) : -1]
        range_high = max(range_window)
        range_low = min(range_window)
        center = mean(range_window)
        fast_ma = mean(prices[-self.trend_fast_window :])
        slow_ma = mean(prices[-self.trend_slow_window :])
        trend_strength = (fast_ma - slow_ma) / max(slow_ma, 1.0)
        momentum_reference = prices[-(self.momentum_window + 1)]
        momentum = (current - momentum_reference) / max(momentum_reference, 1.0)
        recent_noise = self._average_abs_return(prices[-(self.breakout_window + 4) :])
        volatility = max((range_high - range_low) / max(center, 1.0), recent_noise * 2.2, 0.0012)
        dynamic_buffer = max(self.breakout_buffer, recent_noise * 0.9)

        candidates: list[StrategyCandidate] = []

        breakout_gap_up = (current - range_high) / max(range_high, 1.0)
        long_breakout_floor = max(range_high, fast_ma)
        if (
            breakout_gap_up > dynamic_buffer
            and current > previous
            and current > fast_ma > slow_ma
            and trend_strength > dynamic_buffer * 0.55
            and momentum > recent_noise * 0.8
            and previous > center
        ):
            stop_loss = long_breakout_floor * (1 - max(recent_noise * 2.8, 0.0018))
            risk_per_unit = max(current - stop_loss, current * 0.0015)
            take_profit = current + (risk_per_unit * self._reward_multiple(trend_strength))
            candidates.append(
                StrategyCandidate(
                    symbol=symbol,
                    setup="breakout",
                    bias="long",
                    confidence=self._clamp(
                        0.72
                        + min(breakout_gap_up / max(dynamic_buffer, 0.0001), 2.4) * 0.04
                        + min(trend_strength / max(dynamic_buffer, 0.0001), 2.0) * 0.03,
                        0.72,
                        0.94,
                    ),
                    entryPrice=current,
                    stopLoss=stop_loss,
                    takeProfit=take_profit,
                    reason="Bullish trend, expanding momentum, and a clean range-high breakout aligned.",
                )
            )

        breakout_gap_down = (range_low - current) / max(range_low, 1.0)
        short_breakout_ceiling = min(range_low, fast_ma)
        if (
            breakout_gap_down > dynamic_buffer
            and current < previous
            and current < fast_ma < slow_ma
            and trend_strength < -(dynamic_buffer * 0.55)
            and momentum < -(recent_noise * 0.8)
            and previous < center
        ):
            stop_loss = short_breakout_ceiling * (1 + max(recent_noise * 2.8, 0.0018))
            risk_per_unit = max(stop_loss - current, current * 0.0015)
            take_profit = current - (risk_per_unit * self._reward_multiple(abs(trend_strength)))
            candidates.append(
                StrategyCandidate(
                    symbol=symbol,
                    setup="breakout",
                    bias="short",
                    confidence=self._clamp(
                        0.72
                        + min(breakout_gap_down / max(dynamic_buffer, 0.0001), 2.4) * 0.04
                        + min(abs(trend_strength) / max(dynamic_buffer, 0.0001), 2.0) * 0.03,
                        0.72,
                        0.94,
                    ),
                    entryPrice=current,
                    stopLoss=stop_loss,
                    takeProfit=take_profit,
                    reason="Bearish trend, downside expansion, and a clean range-low breakout aligned.",
                )
            )

        sweep_window = prices[-(self.sweep_window + 2) : -2]
        prior_high = max(sweep_window)
        prior_low = min(sweep_window)
        reclaim_up = (current - prior_low) / max(prior_low, 1.0)
        reclaim_down = (prior_high - current) / max(prior_high, 1.0)

        if (
            previous < prior_low * (1 - dynamic_buffer * 1.2)
            and current > prior_low
            and current >= fast_ma * (1 - dynamic_buffer * 0.4)
            and momentum > 0
            and trend_strength > -(dynamic_buffer * 0.35)
            and reclaim_up > dynamic_buffer * 0.45
        ):
            stop_loss = min(previous, prior_low) * (1 - max(recent_noise * 1.6, 0.0012))
            risk_per_unit = max(current - stop_loss, current * 0.0013)
            take_profit = current + (risk_per_unit * max(self.reward_to_risk - 0.3, 1.6))
            candidates.append(
                StrategyCandidate(
                    symbol=symbol,
                    setup="liquidity_sweep",
                    bias="long",
                    confidence=self._clamp(
                        0.75 + min(reclaim_up / max(dynamic_buffer, 0.0001), 2.0) * 0.04,
                        0.75,
                        0.9,
                    ),
                    entryPrice=current,
                    stopLoss=stop_loss,
                    takeProfit=take_profit,
                    reason="Downside sweep reclaimed quickly while higher-timeframe pressure stabilized.",
                )
            )

        if (
            previous > prior_high * (1 + dynamic_buffer * 1.2)
            and current < prior_high
            and current <= fast_ma * (1 + dynamic_buffer * 0.4)
            and momentum < 0
            and trend_strength < (dynamic_buffer * 0.35)
            and reclaim_down > dynamic_buffer * 0.45
        ):
            stop_loss = max(previous, prior_high) * (1 + max(recent_noise * 1.6, 0.0012))
            risk_per_unit = max(stop_loss - current, current * 0.0013)
            take_profit = current - (risk_per_unit * max(self.reward_to_risk - 0.3, 1.6))
            candidates.append(
                StrategyCandidate(
                    symbol=symbol,
                    setup="liquidity_sweep",
                    bias="short",
                    confidence=self._clamp(
                        0.75 + min(reclaim_down / max(dynamic_buffer, 0.0001), 2.0) * 0.04,
                        0.75,
                        0.9,
                    ),
                    entryPrice=current,
                    stopLoss=stop_loss,
                    takeProfit=take_profit,
                    reason="Upside sweep failed fast and rolled back under resistance.",
                )
            )

        return sorted(candidates, key=lambda candidate: candidate.confidence, reverse=True)

    def _average_abs_return(self, prices: list[float]) -> float:
        if len(prices) < 2:
            return 0.0
        returns = [
            abs(prices[index] - prices[index - 1]) / max(prices[index - 1], 1.0)
            for index in range(1, len(prices))
        ]
        return max(mean(returns), 0.0006)

    def _reward_multiple(self, trend_strength: float) -> float:
        return min(self.reward_to_risk + max(trend_strength, 0.0) * 180, 2.5)

    def _clamp(self, value: float, lower: float, upper: float) -> float:
        return max(lower, min(value, upper))
