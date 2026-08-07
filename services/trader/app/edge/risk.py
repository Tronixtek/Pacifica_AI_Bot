from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.mt5.models import MarketSpec


# Instruments that move together are ONE bet, however many tickers they have.
# US30, US500 and USTEC share most of their variance; holding all three is
# triple exposure to a single move wearing the costume of diversification.
# This is the same defect as the scalper's hedging bug seen from the other
# side: hedging guarantees a loss, correlation multiplies one.
DEFAULT_BUCKETS: dict[str, tuple[str, ...]] = {
    "us_indices": ("US30", "US500", "USTEC", "US2000"),
    "eu_indices": ("DE30", "DE40", "UK100", "FR40", "STOXX50"),
    "asia_indices": ("JP225", "HK50", "AUS200"),
    "metals": ("XAUUSD", "XAGUSD", "XPTUSD", "XPDUSD"),
    "energy": ("USOIL", "UKOIL", "NGAS"),
    "crypto": ("BTCUSD", "ETHUSD", "LTCUSD", "XRPUSD", "SOLUSD"),
}


@dataclass(slots=True, frozen=True)
class RiskDecision:
    allowed: bool
    reason: str
    lots: float = 0.0
    riskCash: float = 0.0
    riskPct: float = 0.0


@dataclass(slots=True)
class OpenRisk:
    """One live position, as the risk manager needs to see it."""

    symbol: str
    direction: str
    riskCash: float


@dataclass(slots=True)
class RiskSettings:
    targetRiskPct: float = 0.50
    # Minimum lot sizes mean the intended risk is not always expressible. Up to
    # this much is accepted; past it the instrument is declined rather than
    # silently traded at a size nobody chose.
    maxRiskPct: float = 0.80
    maxOpenPositions: int = 6
    maxPortfolioHeatPct: float = 3.0
    # Set to catch MALFUNCTION, not variance. At a 31% win rate a long losing
    # run is expected behaviour, and halting on it stops a working system at
    # precisely its worst moment - which is why the scalper's drawdown halt was
    # removed. -6% in a day is ~12 consecutive full losses: a bug, not a
    # bad session.
    dailyLossHaltPct: float = 6.0
    # A full stop needing human review. 25% down at 0.5% risk is 50R, a 2-3
    # sigma event against a +0.05R edge - evidence the edge is not real.
    hardStopDrawdownPct: float = 25.0
    buckets: dict[str, tuple[str, ...]] = field(
        default_factory=lambda: dict(DEFAULT_BUCKETS)
    )


def base_symbol(symbol: str, suffix: str = "") -> str:
    """Strip the broker suffix so 'US30m' and 'US30' bucket together."""
    if suffix and symbol.endswith(suffix):
        return symbol[: -len(suffix)]
    # Exness appends a lowercase marker; nothing in the base names is lowercase.
    trimmed = symbol.rstrip("abcdefghijklmnopqrstuvwxyz")
    return trimmed or symbol


class RiskManager:
    """Decides whether a signal may be taken, and at what size.

    Deliberately pure: equity, open positions and the day's realised P/L are
    passed in rather than fetched. That keeps every rule testable without a
    broker connection, which is the only way to be confident a guard actually
    fires - the CRT pause bug was invisible precisely because the logic could
    not be exercised in isolation.
    """

    def __init__(self, settings: RiskSettings, suffix: str = "") -> None:
        self.settings = settings
        self.suffix = suffix
        self._bucket_of: dict[str, str] = {}
        for bucket, members in settings.buckets.items():
            for m in members:
                self._bucket_of[m] = bucket

    def bucket_for(self, symbol: str) -> str:
        """The correlation group a symbol belongs to.

        Unknown symbols get a bucket of their own rather than sharing one:
        assuming two unrecognised instruments are uncorrelated is the less
        damaging error, since the position cap still limits total exposure.
        """
        base = base_symbol(symbol, self.suffix)
        return self._bucket_of.get(base, f"other:{base}")

    def size_lots(
        self,
        riskCash: float,
        entry: float,
        stop: float,
        spec: MarketSpec,
        valuePerPricePoint: float,
    ) -> float:
        """Lots that risk `riskCash` if `stop` is hit.

        `valuePerPricePoint` is the account-currency value of a 1.0 price move
        at 1.0 lots, which the caller obtains from order_calc_profit so the
        broker's own conversion is used rather than a reimplementation of it.

        Always rounds DOWN. Rounding up to reach the minimum lot would risk
        more than was authorised, and the whole point of this class is that
        nothing trades at a size nobody chose.
        """
        distance = abs(entry - stop)
        if distance <= 0 or valuePerPricePoint <= 0 or riskCash <= 0:
            return 0.0
        raw = riskCash / (distance * valuePerPricePoint)
        step = spec.volumeStep or 0.01
        lots = math.floor(raw / step) * step
        # Floating point leaves 0.29999999999999993 where 0.3 was intended.
        lots = round(lots, 8)
        if lots > spec.volumeMax:
            lots = spec.volumeMax
        return max(0.0, lots)

    def evaluate(
        self,
        symbol: str,
        direction: str,
        entry: float,
        stop: float,
        spec: MarketSpec,
        valuePerPricePoint: float,
        equity: float,
        startingEquity: float,
        openPositions: list[OpenRisk],
        realisedToday: float = 0.0,
    ) -> RiskDecision:
        s = self.settings

        if equity <= 0 or startingEquity <= 0:
            return RiskDecision(False, "Equity unavailable; refusing to size a trade.")

        # --- kill switches, checked before anything else ------------------
        drawdown = (startingEquity - equity) / startingEquity * 100.0
        if drawdown >= s.hardStopDrawdownPct:
            return RiskDecision(
                False,
                f"HARD STOP: {drawdown:.1f}% below starting equity "
                f"(limit {s.hardStopDrawdownPct:.0f}%). Manual review required.",
            )
        if realisedToday < 0 and abs(realisedToday) / equity * 100.0 >= s.dailyLossHaltPct:
            return RiskDecision(
                False,
                f"Daily loss halt: {abs(realisedToday)/equity*100:.1f}% lost today "
                f"(limit {s.dailyLossHaltPct:.0f}%). This size of loss in one day "
                f"indicates a fault rather than variance.",
            )

        if len(openPositions) >= s.maxOpenPositions:
            return RiskDecision(False, f"At the {s.maxOpenPositions}-position cap.")

        # --- correlation ---------------------------------------------------
        bucket = self.bucket_for(symbol)
        for pos in openPositions:
            if self.bucket_for(pos.symbol) != bucket:
                continue
            if pos.symbol == symbol and pos.direction != direction:
                return RiskDecision(
                    False,
                    f"Refusing to hold both directions on {symbol}: an opposing "
                    f"pair can only ever net out to minus the spread.",
                )
            return RiskDecision(
                False,
                f"Bucket '{bucket}' already holds {pos.symbol}; these move "
                f"together, so a second position doubles one bet rather than "
                f"spreading two.",
            )

        # --- sizing --------------------------------------------------------
        target = equity * s.targetRiskPct / 100.0
        lots = self.size_lots(target, entry, stop, spec, valuePerPricePoint)

        if lots < spec.volumeMin:
            # The intended risk cannot be expressed. Ask what the smallest
            # tradeable position would actually cost, and accept it only if it
            # stays inside the tolerance.
            floor_risk = spec.volumeMin * abs(entry - stop) * valuePerPricePoint
            floor_pct = floor_risk / equity * 100.0
            if floor_pct > s.maxRiskPct:
                return RiskDecision(
                    False,
                    f"Minimum lot ({spec.volumeMin:g}) risks ${floor_risk:.2f} "
                    f"= {floor_pct:.2f}% of equity, above the {s.maxRiskPct:.2f}% "
                    f"ceiling. This instrument is too large for this account.",
                )
            lots = spec.volumeMin

        riskCash = lots * abs(entry - stop) * valuePerPricePoint
        riskPct = riskCash / equity * 100.0

        # --- portfolio heat -------------------------------------------------
        heat = sum(p.riskCash for p in openPositions) + riskCash
        heatPct = heat / equity * 100.0
        if heatPct > s.maxPortfolioHeatPct:
            return RiskDecision(
                False,
                f"Total open risk would reach {heatPct:.2f}%, above the "
                f"{s.maxPortfolioHeatPct:.1f}% cap. Skipping rather than "
                f"shrinking: a token position is noise that still pays spread.",
            )

        return RiskDecision(
            True,
            f"{lots:g} lots risking ${riskCash:.2f} ({riskPct:.2f}% of equity), "
            f"bucket '{bucket}', heat {heatPct:.2f}%.",
            lots=lots,
            riskCash=riskCash,
            riskPct=riskPct,
        )
