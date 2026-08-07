from __future__ import annotations

from dataclasses import dataclass

from app.mt5.models import Bar


@dataclass(slots=True, frozen=True)
class PatternHit:
    """A candlestick signal and the price that would invalidate it.

    `structuralLevel` is the whole point of this module. A fixed stop distance
    is arbitrary; this level is the price at which the pattern is simply wrong -
    below a hammer's wick, beyond an engulfing pair's extreme. Placing the stop
    there means the market defines the risk, and it makes the stop WIDER than a
    tight fixed one, which matters more than it sounds: spread costs a fixed
    number of dollars, so a wider stop is a smaller cost per unit of risk. That
    ratio decided every result we measured.
    """

    name: str
    direction: str            # "buy" | "sell"
    structuralLevel: float


# A body this small relative to the bar's range is a rejection wick rather than
# a directional move, whatever its colour.
_PIN_WICK_FRACTION = 0.60
_PIN_BODY_MULTIPLE = 2.0
# Bars whose range is a rounding error produce garbage ratios and absurd stops.
_MIN_RANGE_FRACTION_OF_ATR = 0.10


def _is_doji(bar: Bar) -> bool:
    return bar.range <= 0 or bar.body <= bar.range * 0.05


def detect(bars: list[Bar], atr: float) -> PatternHit | None:
    """Find a candlestick signal on the LAST bar of `bars`.

    Only the final bar is considered a trigger: `bars` must end with a CLOSED
    bar, because a pattern on a forming bar can un-form before the close and is
    the classic way a backtest quietly cheats.

    Returns None when nothing fires, which is the common case by design - this
    bot should be idle far more often than it trades.
    """
    if len(bars) < 3 or atr <= 0:
        return None

    cur, prev, prior = bars[-1], bars[-2], bars[-3]

    # Reject bars too small to place a sane stop against. Without this, a
    # flat bar produces a structural level a fraction of a tick away, which
    # then demands an enormous position to risk the intended cash amount.
    if cur.range < _MIN_RANGE_FRACTION_OF_ATR * atr:
        return None

    # --- engulfing -------------------------------------------------------
    # The current body must fully contain the previous body and reverse its
    # direction. Stop goes beyond the extreme of the PAIR, not just the
    # trigger bar: the pattern is the two bars together.
    if not _is_doji(prev):
        if (
            cur.isBullish
            and not prev.isBullish
            and cur.close >= prev.open
            and cur.open <= prev.close
        ):
            return PatternHit("bullish_engulfing", "buy", min(cur.low, prev.low))
        if (
            not cur.isBullish
            and prev.isBullish
            and cur.close <= prev.open
            and cur.open >= prev.close
        ):
            return PatternHit("bearish_engulfing", "sell", max(cur.high, prev.high))

    # --- pin bar ---------------------------------------------------------
    # A long wick is price being rejected from a level. The stop belongs just
    # beyond the wick's tip: if price returns through it, the rejection failed.
    if cur.lowerWick > _PIN_BODY_MULTIPLE * cur.body and cur.lowerWick > _PIN_WICK_FRACTION * cur.range:
        return PatternHit("hammer", "buy", cur.low)
    if cur.upperWick > _PIN_BODY_MULTIPLE * cur.body and cur.upperWick > _PIN_WICK_FRACTION * cur.range:
        return PatternHit("shooting_star", "sell", cur.high)

    # --- inside-bar break ------------------------------------------------
    # A bar contained inside its predecessor is a pause; the break of it is the
    # resumption. The stop sits at the far side of the inside bar, so the
    # pattern's own range defines the risk.
    if prev.high < prior.high and prev.low > prior.low:
        if cur.close > prev.high:
            return PatternHit("inside_bar_break_up", "buy", prev.low)
        if cur.close < prev.low:
            return PatternHit("inside_bar_break_down", "sell", prev.high)

    return None


def structural_stop(hit: PatternHit, atr: float, buffer_atr: float = 0.10) -> float:
    """Place the stop just beyond the level that invalidates the pattern.

    The buffer exists because a stop sitting exactly on the wick's tip gets
    clipped by ordinary noise and a one-tick overshoot - the pattern would be
    stopped out without actually being wrong.
    """
    pad = buffer_atr * atr
    return hit.structuralLevel - pad if hit.direction == "buy" else hit.structuralLevel + pad
