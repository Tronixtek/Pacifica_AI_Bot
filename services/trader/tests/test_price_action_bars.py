from datetime import datetime, timedelta, timezone

import pytest

from app.mt5.models import Bar
from app.strategy.price_action import PriceActionStrategy


START = datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc)


def bar(index, open_, high, low, close, spread=8):
    return Bar(
        time=START + timedelta(minutes=5 * index),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100.0,
        spreadPoints=spread,
    )


def ranging_series(count=60, center=1.1000, half_range=0.0020):
    """A flat oscillating range: no trend, no breakout, no sweep."""
    bars = []
    for i in range(count):
        drift = half_range * (1 if i % 2 else -1) * 0.5
        open_ = center + drift
        close = center - drift
        bars.append(bar(i, open_, center + half_range, center - half_range, close))
    return bars


def uptrend_series(count=60, start=1.1000, step=0.0004):
    """A steady uptrend so the MA stack and momentum filters are satisfied."""
    bars = []
    price = start
    for i in range(count):
        open_ = price
        close = price + step
        bars.append(bar(i, open_, close + 0.0002, open_ - 0.0002, close))
        price = close
    return bars


def strategy():
    return PriceActionStrategy()


def test_insufficient_bars_yields_nothing():
    assert strategy().evaluate("EURUSDm", ranging_series(count=10)) == []


def test_flat_range_produces_no_signal():
    assert strategy().evaluate("EURUSDm", ranging_series()) == []


def test_clean_bullish_breakout_is_detected():
    bars = uptrend_series()
    top = max(b.high for b in bars[-21:-1])
    # A decisive bar: opens at the level, closes well beyond it, small wicks.
    bars.append(bar(len(bars), top, top + 0.0075, top - 0.0002, top + 0.0070))

    found = strategy().evaluate("EURUSDm", bars)
    breakouts = [c for c in found if c.setup == "breakout"]
    assert len(breakouts) == 1
    signal = breakouts[0]
    assert signal.bias == "long"
    assert signal.stopLoss < signal.entryPrice < signal.takeProfit
    assert signal.atr > 0


def test_failed_breakout_is_rejected():
    """A wick through the high that closes back inside is NOT a breakout.

    This is the case a close-only tick series cannot distinguish: sampled on
    closes alone, the spike is invisible, and sampled mid-spike it looks
    identical to a real breakout.
    """
    bars = uptrend_series()
    top = max(b.high for b in bars[-21:-1])
    # Same extreme as the clean breakout above, but it closes back below it.
    bars.append(bar(len(bars), top, top + 0.0075, top - 0.0010, top - 0.0005))

    breakouts = [c for c in strategy().evaluate("EURUSDm", bars) if c.setup == "breakout"]
    assert breakouts == []


def test_indecisive_bar_is_rejected_even_when_it_closes_beyond():
    """Closing beyond the range on a doji is not conviction."""
    bars = uptrend_series()
    top = max(b.high for b in bars[-21:-1])
    # Closes above the range, but the body is a small fraction of the range.
    bars.append(bar(len(bars), top + 0.0068, top + 0.0130, top + 0.0010, top + 0.0070))

    breakouts = [c for c in strategy().evaluate("EURUSDm", bars) if c.setup == "breakout"]
    assert breakouts == []


def test_bullish_liquidity_sweep_is_detected():
    bars = ranging_series()
    floor = min(b.low for b in bars[-13:-1])
    # Pierces the range floor, then closes back above it with a long lower wick.
    bars.append(bar(len(bars), floor + 0.0005, floor + 0.0012, floor - 0.0030, floor + 0.0010))

    sweeps = [c for c in strategy().evaluate("EURUSDm", bars) if c.setup == "liquidity_sweep"]
    assert len(sweeps) == 1
    signal = sweeps[0]
    assert signal.bias == "long"
    assert signal.takeProfit > signal.entryPrice
    # The stop must sit beneath the swept extreme, not merely beneath entry:
    # the whole premise is that price rejected that level.
    swept_low = floor - 0.0030
    assert signal.stopLoss < swept_low


def test_bearish_liquidity_sweep_is_detected():
    bars = ranging_series()
    ceiling = max(b.high for b in bars[-13:-1])
    bars.append(bar(len(bars), ceiling - 0.0005, ceiling + 0.0030, ceiling - 0.0012, ceiling - 0.0010))

    sweeps = [c for c in strategy().evaluate("EURUSDm", bars) if c.setup == "liquidity_sweep"]
    assert len(sweeps) == 1
    signal = sweeps[0]
    assert signal.bias == "short"
    assert signal.stopLoss > signal.entryPrice > signal.takeProfit


def test_sweep_without_rejection_wick_is_rejected():
    """Breaking the low and closing near it is continuation, not a sweep."""
    bars = ranging_series()
    floor = min(b.low for b in bars[-13:-1])
    # Closes back above the floor but with almost no lower wick.
    bars.append(bar(len(bars), floor + 0.0020, floor + 0.0021, floor - 0.0002, floor + 0.0001))

    sweeps = [c for c in strategy().evaluate("EURUSDm", bars) if c.setup == "liquidity_sweep"]
    assert sweeps == []


def test_atr_reflects_gaps_not_just_bar_range():
    """True range must span the gap from the previous close.

    A session gap moves price without any single bar having a wide high-low, so
    a naive range would under-report volatility and place stops far too tight
    right after a weekend open.
    """
    strat = strategy()
    flat = [bar(i, 1.1000, 1.1005, 1.0995, 1.1000) for i in range(20)]
    gapped = flat + [bar(20, 1.1100, 1.1105, 1.1095, 1.1100)]  # 100 pip gap up

    # Window of 1 isolates the gapped bar. Its own high-low is only 0.0010;
    # measured from the previous close it is 0.0105.
    assert strat._atr(gapped, 1) == pytest.approx(0.0105, abs=1e-6)
    assert strat._atr(flat, 1) == pytest.approx(0.0010, abs=1e-6)

    # And that feeds through to the smoothed average.
    assert strat._atr(gapped, 14) > strat._atr(flat, 14)


def test_spread_is_carried_onto_the_candidate():
    bars = uptrend_series()
    top = max(b.high for b in bars[-21:-1])
    bars.append(bar(len(bars), top, top + 0.0075, top - 0.0002, top + 0.0070, spread=31))

    found = [c for c in strategy().evaluate("EURUSDm", bars) if c.setup == "breakout"]
    assert found and found[0].spreadPoints == 31


def test_stop_inside_the_spread_is_rejected():
    """A stop nearer than 3x spread is unusable regardless of ATR.

    In a quiet session the ATR floor alone can still permit a stop narrower
    than the dealing spread, which is hit the instant the position opens.
    """
    bars = ranging_series()
    floor = min(b.low for b in bars[-13:-1])
    sweep = bar(len(bars), floor + 0.0005, floor + 0.0012, floor - 0.0030, floor + 0.0010)

    # Same setup, only the recorded spread differs.
    narrow = list(bars) + [sweep]
    assert [c for c in strategy().evaluate("EURUSDm", narrow) if c.setup == "liquidity_sweep"]

    wide = list(bars) + [
        Bar(time=sweep.time, open=sweep.open, high=sweep.high, low=sweep.low,
            close=sweep.close, volume=sweep.volume, spreadPoints=400,
            spreadPrice=0.0040)
    ]
    assert [c for c in strategy().evaluate("EURUSDm", wide) if c.setup == "liquidity_sweep"] == []
