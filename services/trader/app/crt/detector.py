from __future__ import annotations

from dataclasses import dataclass

from app.mt5.models import Bar


@dataclass(slots=True)
class CrtSetup:
    """A completed Candle Range Theory sweep.

    C1 defines a range. C2 wicks beyond one edge and closes back inside it -
    the sweep. The expectation is that C3 expands the other way, so a swept low
    reads bullish and a swept high bearish.
    """

    bullish: bool
    rangeHigh: float
    rangeLow: float
    sweepHigh: float
    sweepLow: float
    sweepClose: float
    sweptBy: float          # how far past the level the wick reached
    closedBackBy: float     # how far back inside the range the close landed
    reason: str

    @property
    def bias(self) -> str:
        return "long" if self.bullish else "short"


def detect(c1: Bar, c2: Bar, min_sweep_atr: float = 0.0, atr: float = 0.0) -> CrtSetup | None:
    """Return the setup formed by `c2` sweeping `c1`, or None.

    A sweep of BOTH sides is rejected rather than guessed at: an outside bar
    that took liquidity above and below gives no directional read, and picking
    one edge would be inventing information.
    """
    range_size = c1.high - c1.low
    if range_size <= 0:
        return None

    swept_low = c2.low < c1.low and c2.close > c1.low
    swept_high = c2.high > c1.high and c2.close < c1.high

    if swept_low and swept_high:
        return None
    if not (swept_low or swept_high):
        return None

    if swept_low:
        swept_by = c1.low - c2.low
        closed_back = c2.close - c1.low
        bullish = True
    else:
        swept_by = c2.high - c1.high
        closed_back = c1.high - c2.close
        bullish = False

    # Optionally require the wick to be a meaningful excursion rather than a
    # one-tick overshoot, which is noise rather than a liquidity grab.
    if min_sweep_atr > 0 and atr > 0 and swept_by < atr * min_sweep_atr:
        return None

    side = "low" if bullish else "high"
    return CrtSetup(
        bullish=bullish,
        rangeHigh=c1.high,
        rangeLow=c1.low,
        sweepHigh=c2.high,
        sweepLow=c2.low,
        sweepClose=c2.close,
        sweptBy=swept_by,
        closedBackBy=closed_back,
        reason=(
            f"Swept the range {side} by {swept_by:.5f} and closed back inside "
            f"by {closed_back:.5f}."
        ),
    )


def aggregate(bars: list[Bar], factor: int) -> list[Bar]:
    """Group consecutive bars into higher-timeframe candles.

    Built from the execution series rather than fetched separately so bucket
    boundaries line up exactly - a mismatch would place the sweep candle at a
    different time than the bars used to trade it.
    """
    if factor <= 1:
        return list(bars)
    out: list[Bar] = []
    for i in range(0, len(bars) - factor + 1, factor):
        chunk = bars[i : i + factor]
        out.append(
            Bar(
                time=chunk[0].time,
                open=chunk[0].open,
                high=max(b.high for b in chunk),
                low=min(b.low for b in chunk),
                close=chunk[-1].close,
                volume=sum(b.volume for b in chunk),
                spreadPoints=chunk[-1].spreadPoints,
                spreadPrice=chunk[-1].spreadPrice,
            )
        )
    return out
