from __future__ import annotations

from dataclasses import dataclass

from app.mt5.models import Bar


@dataclass(slots=True, frozen=True)
class TrendView:
    """What one timeframe thinks the direction is."""

    direction: str | None     # "up" | "down" | None when unresolved
    fast: float
    slow: float
    anchor: float

    @property
    def resolved(self) -> bool:
        return self.direction is not None


def ema(values: list[float], period: int) -> list[float]:
    """Exponential moving average, seeded on the first value.

    Seeding on values[0] rather than an SMA of the first `period` bars means
    the early output is biased, which is why callers must supply a warmup far
    longer than the period - see `MIN_BARS`.
    """
    if not values:
        return []
    k = 2.0 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1.0 - k))
    return out


# EMA seeded from a single value needs roughly this many bars before the
# anchor period is trustworthy. Enforced rather than documented, because a
# trend read off an unconverged 200 EMA is worse than no trend read at all.
MIN_BARS = 400


def atr(bars: list[Bar], period: int = 14) -> float:
    """Average true range of the last `period` bars, Wilder-smoothed.

    True range includes the gap from the previous close, not merely the bar's
    own high-low. On indices that distinction is the whole point: they close
    and reopen, and a bar measured without its gap understates the real range
    badly enough to size a position several times too large.
    """
    if len(bars) < 2:
        return 0.0
    trs = [bars[0].high - bars[0].low]
    for i in range(1, len(bars)):
        prev_close = bars[i - 1].close
        trs.append(
            max(
                bars[i].high - bars[i].low,
                abs(bars[i].high - prev_close),
                abs(bars[i].low - prev_close),
            )
        )
    run = trs[0]
    for t in trs[1:]:
        run += (t - run) / period
    return max(0.0, run)


# In a range the three EMAs sit almost on top of each other, and their
# ordering is then decided by rounding rather than by direction - a flat
# oscillation tests as a clean uptrend on a separation of 0.03 in 100. Demand
# they be genuinely apart, measured in ATR so the threshold means the same
# thing on gold at 4000 and on an index at 40000.
MIN_SEPARATION_ATR = 0.10


def read_trend(bars: list[Bar], atr: float, fast: int = 20, slow: int = 50,
               anchor: int = 200, require_anchor: bool = True,
               min_separation_atr: float = MIN_SEPARATION_ATR) -> TrendView:
    """Classify the trend on one timeframe.

    `require_anchor` controls strictness. With it on, the slow MA must also sit
    the right side of the anchor - a genuine stacked trend. With it off, only
    fast vs slow matters, which fires far more often on fast timeframes where a
    200-period anchor barely moves. The sweep decides which is better; both are
    supported because the answer differed by timeframe in testing.

    `atr` is required rather than optional because the separation guard is the
    difference between a trend filter and a coin flip in ranging conditions,
    and an optional guard is one that eventually gets left off.
    """
    if len(bars) < MIN_BARS or atr <= 0:
        return TrendView(None, 0.0, 0.0, 0.0)

    closes = [b.close for b in bars]
    f = ema(closes, fast)[-1]
    s = ema(closes, slow)[-1]
    a = ema(closes, anchor)[-1]

    gap = min_separation_atr * atr
    if require_anchor:
        up = (f - s) >= gap and (s - a) >= gap
        down = (s - f) >= gap and (a - s) >= gap
    else:
        up = (f - s) >= gap
        down = (s - f) >= gap

    return TrendView("up" if up else "down" if down else None, f, s, a)


def aligned_direction(execution: TrendView, higher: TrendView | None) -> str | None:
    """The direction both timeframes permit, or None.

    Multi-timeframe analysis here is a VETO, not a vote. The higher timeframe
    never creates a trade - it only forbids trading against itself. That
    ordering matters: letting a higher timeframe generate entries would take
    trades with no local trigger, and letting it merely tie-break would allow
    counter-trend entries whenever the two disagreed.

    Passing `higher` as None disables the veto, so single-timeframe behaviour
    stays reachable and directly comparable.
    """
    if not execution.resolved:
        return None
    if higher is None:
        return "buy" if execution.direction == "up" else "sell"
    if not higher.resolved or higher.direction != execution.direction:
        return None
    return "buy" if execution.direction == "up" else "sell"
