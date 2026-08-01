from __future__ import annotations

from dataclasses import dataclass
from statistics import mean

from app.contracts import SignalBias, SignalSetup
from app.mt5.models import Bar


ATR_WINDOW = 14


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
    # Carried through so the risk layer can size against real volatility and
    # reject setups whose stop sits inside the broker's spread.
    atr: float = 0.0
    spreadPoints: int = 0


class PriceActionStrategy:
    """Breakout and liquidity-sweep detection over closed OHLC bars.

    Every window below counts *bars on the strategy timeframe*, not tick
    samples. The distinction matters: the same numbers applied to a 2-second
    tick feed describe a 40-second window, which is spread noise rather than
    market structure.
    """

    def __init__(
        self,
        breakout_window: int = 20,
        sweep_window: int = 12,
        trend_fast_window: int = 8,
        trend_slow_window: int = 34,
        momentum_window: int = 5,
        breakout_atr_multiple: float = 0.15,
        min_trend_separation_atr: float = 0.25,
        min_stop_atr_multiple: float = 0.6,
        min_stop_spread_multiple: float = 3.0,
        reward_to_risk: float = 2.1,
    ) -> None:
        self.breakout_window = breakout_window
        self.sweep_window = sweep_window
        self.trend_fast_window = trend_fast_window
        self.trend_slow_window = trend_slow_window
        self.momentum_window = momentum_window
        self.breakout_atr_multiple = breakout_atr_multiple
        self.min_trend_separation_atr = min_trend_separation_atr
        self.min_stop_atr_multiple = min_stop_atr_multiple
        self.min_stop_spread_multiple = min_stop_spread_multiple
        self.reward_to_risk = reward_to_risk
        # Sweeps are counter-trend by nature, so the trend filter only blocks
        # them when the prevailing trend is strongly against the setup.
        self.max_counter_trend_atr = max(min_trend_separation_atr * 6, 1.5)

    @property
    def min_bars(self) -> int:
        return max(
            self.breakout_window + 2,
            self.sweep_window + 3,
            self.trend_slow_window + 2,
            self.momentum_window + 2,
            ATR_WINDOW + 2,
        )

    def evaluate(self, symbol: str, bars: list[Bar]) -> list[StrategyCandidate]:
        if len(bars) < self.min_bars:
            return []

        current = bars[-1]
        closes = [bar.close for bar in bars]

        # The reference range deliberately excludes the current bar, otherwise
        # the bar that breaks the range is also the bar that defines it.
        range_bars = bars[-(self.breakout_window + 1) : -1]
        range_high = max(bar.high for bar in range_bars)
        range_low = min(bar.low for bar in range_bars)

        atr = self._atr(bars, ATR_WINDOW)
        if atr <= 0 or current.close <= 0:
            return []

        fast_ma = mean(closes[-self.trend_fast_window :])
        slow_ma = mean(closes[-self.trend_slow_window :])
        # Measured in ATR rather than as a fraction of price: a 0.2% MA gap is
        # a strong trend on EURUSD and noise on XAUUSD, but 0.25 ATR means the
        # same thing on both.
        trend_separation = (fast_ma - slow_ma) / atr

        momentum_reference = closes[-(self.momentum_window + 1)]
        momentum = (current.close - momentum_reference) / atr

        candidates: list[StrategyCandidate] = []
        candidates.extend(
            self._breakouts(
                symbol, current, range_high, range_low,
                fast_ma, slow_ma, trend_separation, momentum, atr,
            )
        )
        candidates.extend(self._sweeps(symbol, current, bars, atr, trend_separation))

        return sorted(
            (c for c in candidates if self._has_workable_stop(c, current, atr)),
            key=lambda item: item.confidence,
            reverse=True,
        )

    def _has_workable_stop(self, candidate: StrategyCandidate, current: Bar, atr: float) -> bool:
        """Reject setups whose stop is too tight to survive normal noise.

        Structural stops can land a point or two from entry when price closes
        right at the level it broke. Two independent floors apply:

        - ATR-relative, so the stop respects current volatility.
        - Spread-relative, because in a quiet session 0.6 ATR can itself be
          narrower than the spread, and a stop inside the spread is hit the
          instant the position opens.
        """
        stop_distance = abs(candidate.entryPrice - candidate.stopLoss)
        if stop_distance < atr * self.min_stop_atr_multiple:
            return False
        if current.spreadPrice > 0:
            return stop_distance >= current.spreadPrice * self.min_stop_spread_multiple
        return True

    def _breakouts(
        self,
        symbol: str,
        current: Bar,
        range_high: float,
        range_low: float,
        fast_ma: float,
        slow_ma: float,
        trend_separation: float,
        momentum: float,
        atr: float,
    ) -> list[StrategyCandidate]:
        found: list[StrategyCandidate] = []
        breakout_margin = atr * self.breakout_atr_multiple

        # A breakout counts only if the bar CLOSED beyond the range. A bar that
        # spiked through and closed back inside is a failed breakout, which is
        # frequently the opposite signal.
        conviction = current.body >= current.range * 0.5 if current.range > 0 else False

        if (
            current.close > range_high + breakout_margin
            and current.isBullish
            and conviction
            and current.close > fast_ma > slow_ma
            and trend_separation > self.min_trend_separation_atr
            and momentum > 0
        ):
            # Invalidation sits under the broken level, not under the entry:
            # price reclaiming the range is what proves the breakout failed.
            stop_loss = min(range_high, current.low) - (atr * 0.35)
            risk = current.close - stop_loss
            if risk > 0:
                found.append(
                    StrategyCandidate(
                        symbol=symbol,
                        setup="breakout",
                        bias="long",
                        confidence=self._breakout_confidence(
                            current, range_high, atr, trend_separation
                        ),
                        entryPrice=current.close,
                        stopLoss=stop_loss,
                        takeProfit=current.close + risk * self._reward_multiple(trend_separation),
                        reason=(
                            f"Bar closed {self._as_atr(current.close - range_high, atr)} ATR above the "
                            f"{self.breakout_window}-bar high with an aligned uptrend and a full-bodied candle."
                        ),
                        atr=atr,
                        spreadPoints=current.spreadPoints,
                    )
                )

        if (
            current.close < range_low - breakout_margin
            and not current.isBullish
            and conviction
            and current.close < fast_ma < slow_ma
            and trend_separation < -self.min_trend_separation_atr
            and momentum < 0
        ):
            stop_loss = max(range_low, current.high) + (atr * 0.35)
            risk = stop_loss - current.close
            if risk > 0:
                found.append(
                    StrategyCandidate(
                        symbol=symbol,
                        setup="breakout",
                        bias="short",
                        confidence=self._breakout_confidence(
                            current, range_low, atr, abs(trend_separation)
                        ),
                        entryPrice=current.close,
                        stopLoss=stop_loss,
                        takeProfit=current.close - risk * self._reward_multiple(abs(trend_separation)),
                        reason=(
                            f"Bar closed {self._as_atr(range_low - current.close, atr)} ATR below the "
                            f"{self.breakout_window}-bar low with an aligned downtrend and a full-bodied candle."
                        ),
                        atr=atr,
                        spreadPoints=current.spreadPoints,
                    )
                )

        return found

    def _sweeps(
        self,
        symbol: str,
        current: Bar,
        bars: list[Bar],
        atr: float,
        trend_separation: float,
    ) -> list[StrategyCandidate]:
        """Liquidity sweeps: a wick takes out a level, then price rejects it.

        This is the setup that cannot be expressed without OHLC. It is defined
        entirely by the relationship between a bar's extreme and its close - on
        a close-only series the piercing wick simply does not exist.
        """
        found: list[StrategyCandidate] = []
        sweep_bars = bars[-(self.sweep_window + 1) : -1]
        prior_low = min(bar.low for bar in sweep_bars)
        prior_high = max(bar.high for bar in sweep_bars)

        # Bullish sweep: pierced the prior low, closed back above it, and left a
        # rejection wick that dominates the bar body.
        if (
            current.low < prior_low
            and current.close > prior_low
            and current.lowerWick > max(current.body * 1.1, atr * 0.4)
            and trend_separation > -self.max_counter_trend_atr
        ):
            stop_loss = current.low - (atr * 0.25)
            risk = current.close - stop_loss
            if risk > 0:
                found.append(
                    StrategyCandidate(
                        symbol=symbol,
                        setup="liquidity_sweep",
                        bias="long",
                        confidence=self._sweep_confidence(current.lowerWick, current, atr),
                        entryPrice=current.close,
                        stopLoss=stop_loss,
                        takeProfit=current.close + risk * max(self.reward_to_risk - 0.3, 1.6),
                        reason=(
                            f"Wick swept the {self.sweep_window}-bar low by "
                            f"{self._as_atr(prior_low - current.low, atr)} ATR and closed back above it."
                        ),
                        atr=atr,
                        spreadPoints=current.spreadPoints,
                    )
                )

        if (
            current.high > prior_high
            and current.close < prior_high
            and current.upperWick > max(current.body * 1.1, atr * 0.4)
            and trend_separation < self.max_counter_trend_atr
        ):
            stop_loss = current.high + (atr * 0.25)
            risk = stop_loss - current.close
            if risk > 0:
                found.append(
                    StrategyCandidate(
                        symbol=symbol,
                        setup="liquidity_sweep",
                        bias="short",
                        confidence=self._sweep_confidence(current.upperWick, current, atr),
                        entryPrice=current.close,
                        stopLoss=stop_loss,
                        takeProfit=current.close - risk * max(self.reward_to_risk - 0.3, 1.6),
                        reason=(
                            f"Wick swept the {self.sweep_window}-bar high by "
                            f"{self._as_atr(current.high - prior_high, atr)} ATR and closed back below it."
                        ),
                        atr=atr,
                        spreadPoints=current.spreadPoints,
                    )
                )

        return found

    def _atr(self, bars: list[Bar], window: int) -> float:
        """Average true range, the standard volatility unit for stop placement.

        True range includes the gap from the previous close, so it accounts for
        session and weekend gaps that a plain high-minus-low would miss.
        """
        window = min(window, len(bars) - 1)
        if window <= 0:
            return 0.0
        true_ranges = []
        for index in range(len(bars) - window, len(bars)):
            bar = bars[index]
            previous_close = bars[index - 1].close
            true_ranges.append(
                max(
                    bar.high - bar.low,
                    abs(bar.high - previous_close),
                    abs(bar.low - previous_close),
                )
            )
        return mean(true_ranges) if true_ranges else 0.0

    def _breakout_confidence(
        self,
        current: Bar,
        level: float,
        atr: float,
        trend_separation: float,
    ) -> float:
        extension = abs(current.close - level) / max(atr, 1e-9)
        body_ratio = current.body / max(current.range, 1e-9)
        return self._clamp(
            0.72
            + min(extension, 2.0) * 0.045
            + min(trend_separation, 2.0) * 0.025
            + body_ratio * 0.03,
            0.72,
            0.94,
        )

    def _sweep_confidence(self, wick: float, current: Bar, atr: float) -> float:
        wick_strength = wick / max(atr, 1e-9)
        wick_ratio = wick / max(current.range, 1e-9)
        return self._clamp(
            0.75 + min(wick_strength, 2.0) * 0.04 + wick_ratio * 0.05,
            0.75,
            0.9,
        )

    def _reward_multiple(self, trend_separation: float) -> float:
        # `trend_separation` is in ATR units, so a strong trend (~1 ATR of MA
        # separation) widens the target by roughly 0.4R.
        return min(self.reward_to_risk + max(trend_separation, 0.0) * 0.4, 2.5)

    def _as_atr(self, distance: float, atr: float) -> str:
        return f"{distance / max(atr, 1e-9):.2f}"

    def _clamp(self, value: float, lower: float, upper: float) -> float:
        return max(lower, min(value, upper))
