from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.edge.trend import MIN_BARS, TrendView, aligned_direction, ema, read_trend
from app.mt5.models import Bar

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def series(closes):
    return [
        Bar(time=T0 + timedelta(minutes=30 * i), open=c, high=c + 1,
            low=c - 1, close=c)
        for i, c in enumerate(closes)
    ]


def rising(n=MIN_BARS + 50):
    return series([100.0 + i * 0.5 for i in range(n)])


def falling(n=MIN_BARS + 50):
    return series([100.0 + (n - i) * 0.5 for i in range(n)])


def flat(n=MIN_BARS + 50):
    return series([100.0 + (1.0 if i % 2 else -1.0) for i in range(n)])


def test_ema_tracks_a_constant_series_exactly():
    assert ema([5.0] * 10, 3) == [5.0] * 10


def test_ema_lags_a_rising_series():
    out = ema([float(i) for i in range(100)], 10)
    assert out[-1] < 99.0


def test_rising_series_reads_as_an_uptrend():
    t = read_trend(rising(), atr=2.0)
    assert t.direction == "up"
    assert t.fast > t.slow > t.anchor


def test_falling_series_reads_as_a_downtrend():
    assert read_trend(falling(), atr=2.0).direction == "down"


def test_choppy_series_is_unresolved():
    """A range must not read as a trend just because the EMAs happen to stack.

    Without the separation guard this series tests as a clean uptrend on a
    gap of 0.03 in 100 - which would emit junk signals all through a range.
    """
    assert read_trend(flat(), atr=2.0).direction is None


def test_separation_guard_needs_a_real_gap_not_just_an_ordering():
    bars = rising()
    assert read_trend(bars, atr=2.0).direction == "up"
    # Same bars, but demanding a gap far wider than this drift provides.
    assert read_trend(bars, atr=2.0, min_separation_atr=50.0).direction is None


def test_zero_atr_refuses_to_classify():
    assert read_trend(rising(), atr=0.0).direction is None


def test_short_history_refuses_to_guess():
    """An unconverged 200 EMA is worse than no reading at all."""
    assert read_trend(rising(50), atr=2.0).direction is None
    assert read_trend(rising(MIN_BARS - 1), atr=2.0).resolved is False


def test_fast_gate_fires_where_the_anchor_gate_does_not():
    """A fresh turn has fast>slow before the stack has reordered."""
    bars = series([100.0 - i * 0.5 for i in range(MIN_BARS)]
                  + [100.0 - MIN_BARS * 0.5 + i * 2.0 for i in range(40)])
    assert read_trend(bars, atr=2.0, require_anchor=True).direction is None
    assert read_trend(bars, atr=2.0, require_anchor=False).direction == "up"


# --- multi-timeframe veto -------------------------------------------------

def up():
    return TrendView("up", 3.0, 2.0, 1.0)


def down():
    return TrendView("down", 1.0, 2.0, 3.0)


def unresolved():
    return TrendView(None, 1.0, 1.0, 1.0)


def test_agreement_permits_the_trade():
    assert aligned_direction(up(), up()) == "buy"
    assert aligned_direction(down(), down()) == "sell"


def test_disagreement_blocks_rather_than_tie_breaking():
    assert aligned_direction(up(), down()) is None
    assert aligned_direction(down(), up()) is None


def test_unresolved_higher_timeframe_blocks():
    """The veto is conservative: no opinion means no permission."""
    assert aligned_direction(up(), unresolved()) is None


def test_unresolved_execution_timeframe_never_trades():
    assert aligned_direction(unresolved(), up()) is None


def test_higher_timeframe_alone_cannot_create_a_trade():
    """MTF is a veto, never a signal - there must be a local trigger."""
    assert aligned_direction(unresolved(), up()) is None
    assert aligned_direction(unresolved(), down()) is None


def test_passing_none_disables_the_veto():
    """Single-timeframe behaviour stays reachable for comparison."""
    assert aligned_direction(up(), None) == "buy"
    assert aligned_direction(down(), None) == "sell"
