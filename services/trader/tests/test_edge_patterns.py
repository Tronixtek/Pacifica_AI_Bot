from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.edge.patterns import detect, structural_stop
from app.mt5.models import Bar

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def bar(o, h, l, c, i=0):
    return Bar(time=T0 + timedelta(minutes=30 * i), open=o, high=h, low=l, close=c)


def filler(n, base=100.0):
    """Neutral bars that must not themselves trigger anything."""
    return [bar(base, base + 0.4, base - 0.4, base + 0.05, i) for i in range(n)]


def test_bullish_engulfing_stops_below_the_pair_not_just_the_trigger():
    bars = filler(2) + [
        bar(100.0, 100.2, 98.0, 98.5, 2),       # down bar, low 98.0
        bar(98.4, 101.0, 98.9, 100.5, 3),       # engulfs it, but its own low is higher
    ]
    hit = detect(bars, atr=2.0)
    assert hit is not None and hit.name == "bullish_engulfing"
    assert hit.direction == "buy"
    # The pattern is BOTH bars, so invalidation is the lower of the two lows.
    assert hit.structuralLevel == 98.0


def test_bearish_engulfing_detected():
    bars = filler(2) + [
        bar(98.0, 100.5, 97.9, 100.2, 2),
        bar(100.3, 101.0, 97.5, 97.8, 3),
    ]
    hit = detect(bars, atr=2.0)
    assert hit is not None and hit.name == "bearish_engulfing"
    assert hit.direction == "sell"
    assert hit.structuralLevel == 101.0


def test_hammer_needs_a_long_lower_wick_and_small_body():
    bars = filler(2) + [bar(100.0, 100.3, 95.0, 100.1, 2)]
    hit = detect(bars, atr=2.0)
    assert hit is not None and hit.name == "hammer"
    assert hit.direction == "buy"
    assert hit.structuralLevel == 95.0


def test_shooting_star_detected():
    bars = filler(2) + [bar(100.0, 105.0, 99.8, 100.1, 2)]
    hit = detect(bars, atr=2.0)
    assert hit is not None and hit.name == "shooting_star"
    assert hit.direction == "sell"
    assert hit.structuralLevel == 105.0


def test_long_body_is_not_a_pin_bar():
    """A big directional candle has a wick but is not a rejection."""
    bars = filler(2) + [bar(96.0, 104.5, 95.0, 104.0, 2)]
    assert detect(bars, atr=2.0) is None


def test_inside_bar_break_uses_the_inside_bar_for_the_stop():
    bars = filler(1) + [
        bar(100.0, 106.0, 94.0, 100.0, 1),      # wide mother bar
        bar(100.0, 103.0, 97.0, 100.2, 2),      # inside it
        bar(100.2, 104.0, 100.1, 103.5, 3),     # closes above the inside bar's high
    ]
    hit = detect(bars, atr=2.0)
    assert hit is not None and hit.name == "inside_bar_break_up"
    assert hit.structuralLevel == 97.0


def test_tiny_bar_is_rejected_so_the_stop_cannot_be_microscopic():
    """A near-flat bar would give a stop a fraction of a tick wide.

    That is the dangerous failure: risking a fixed cash amount across a
    near-zero stop distance demands an enormous position.
    """
    bars = filler(2) + [bar(100.0, 100.02, 99.99, 100.001, 2)]
    assert detect(bars, atr=5.0) is None


def test_no_pattern_returns_none():
    assert detect(filler(5), atr=2.0) is None


def test_too_few_bars_is_safe():
    assert detect([bar(1, 2, 0.5, 1.5)], atr=1.0) is None
    assert detect([], atr=1.0) is None


def test_zero_atr_is_refused_rather_than_dividing():
    bars = filler(2) + [bar(100.0, 100.3, 95.0, 100.1, 2)]
    assert detect(bars, atr=0.0) is None


def test_structural_stop_sits_beyond_the_level_on_both_sides():
    buy = detect(filler(2) + [bar(100.0, 100.3, 95.0, 100.1, 2)], atr=2.0)
    assert structural_stop(buy, atr=2.0, buffer_atr=0.10) == 95.0 - 0.2

    sell = detect(filler(2) + [bar(100.0, 105.0, 99.8, 100.1, 2)], atr=2.0)
    assert structural_stop(sell, atr=2.0, buffer_atr=0.10) == 105.0 + 0.2
