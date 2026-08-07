from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class MarketConfig:
    """One instrument and the timeframes it is traded on.

    Kept per-market rather than global because the measured best differs by
    instrument, and forcing them to share would mean trading at least one of
    them at a setting that was never the best for it:

        XAUUSD  30m under a daily veto   +0.198R  t=2.73
        BTCUSD  15m under a daily veto   +0.146R  t=2.42

    Gold at 15m and BTC at 30m both measure materially worse, and BTC at 5m is
    outright negative.
    """

    symbol: str
    timeframe: str
    higherTimeframe: str | None = None

    def describe(self) -> str:
        veto = f" under {self.higherTimeframe}" if self.higherTimeframe else ""
        return f"{self.symbol} {self.timeframe}{veto}"


def parse_markets(spec: str) -> list[MarketConfig]:
    """Parse "XAUUSD:30m:1d,BTCUSD:15m:1d" into market configs.

    A colon-delimited form rather than JSON because this arrives through an
    environment variable, and quoting JSON inside a .env is exactly the class
    of thing that produced a dotenv parse error and a backend that would not
    start.

    The veto is optional: "BTCUSD:15m" trades the execution timeframe alone.
    An empty or malformed entry is skipped rather than raising, because one
    bad symbol should not stop the others from trading.
    """
    out: list[MarketConfig] = []
    seen: set[str] = set()
    for chunk in (spec or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = [p.strip() for p in chunk.split(":")]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            continue
        symbol = parts[0].upper()
        if symbol in seen:
            # A duplicate would open two positions on one instrument under the
            # correlation cap's nose, since the cap looks at open positions
            # rather than at configuration.
            continue
        seen.add(symbol)
        higher = parts[2] if len(parts) > 2 and parts[2] else None
        out.append(MarketConfig(symbol=symbol, timeframe=parts[1], higherTimeframe=higher))
    return out


def markets_from_settings(settings) -> list[MarketConfig]:
    """Markets from `edgeMarkets`, falling back to the single-market fields.

    The fallback keeps an existing .env working: anything that still sets
    EDGE_SYMBOLS/EDGE_TIMEFRAME gets the same behaviour as before rather than
    silently trading nothing.
    """
    parsed = parse_markets(getattr(settings, "edgeMarkets", "") or "")
    if parsed:
        return parsed
    return [
        MarketConfig(
            symbol=s,
            timeframe=settings.edgeTimeframe,
            higherTimeframe=settings.edgeHigherTimeframe or None,
        )
        for s in settings.edgeSymbols
    ]
