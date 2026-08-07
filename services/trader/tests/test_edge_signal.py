from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.edge.signal import Rejection, Signal, evaluate
from app.edge.trend import MIN_BARS, atr
from app.mt5.models import Bar

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def bar(o, h, l, c, i, spread=0.0):
    return Bar(time=T0 + timedelta(minutes=30 * i), open=o, high=h, low=l,
               close=c, spreadPrice=spread)


def uptrend(n=MIN_BARS + 20, step=0.5, start=100.0):
    """A clean rising series with enough range to give a usable ATR."""
    out = []
    for i in range(n):
        base = start + i * step
        out.append(bar(base, base + 1.0, base - 1.0, base + 0.4, i))
    return out


def downtrend(n=MIN_BARS + 20, step=0.5):
    out = []
    for i in range(n):
        base = 500.0 - i * step
        out.append(bar(base, base + 1.0, base - 1.0, base - 0.4, i))
    return out


def with_hammer(bars, spread=0.0):
    """Append a hammer that closes near the prior close."""
    last = bars[-1]
    n = len(bars)
    c = last.close
    return bars + [bar(c, c + 0.3, c - 6.0, c + 0.1, n, spread)]


def with_shooting_star(bars, spread=0.0):
    last = bars[-1]
    n = len(bars)
    c = last.close
    return bars + [bar(c, c + 6.0, c - 0.3, c - 0.1, n, spread)]


def test_uptrend_plus_hammer_produces_a_buy():
    s = evaluate("XAUUSDm", with_hammer(uptrend()), None)
    assert isinstance(s, Signal)
    assert s.direction == "buy"
    assert s.pattern == "hammer"
    assert s.stop < s.entry
    assert s.risk > 0


def test_downtrend_plus_shooting_star_produces_a_sell():
    s = evaluate("XAUUSDm", with_shooting_star(downtrend()), None)
    assert isinstance(s, Signal)
    assert s.direction == "sell"
    assert s.stop > s.entry


def test_counter_trend_pattern_is_refused():
    """A sell pattern in an uptrend must not trade."""
    r = evaluate("XAUUSDm", with_shooting_star(uptrend()), None)
    assert isinstance(r, Rejection)
    assert "counter-trend" in r.reason


def test_no_pattern_is_a_rejection_not_a_trade():
    r = evaluate("XAUUSDm", uptrend(), None)
    assert isinstance(r, Rejection)
    assert "no pattern" in r.reason


def test_higher_timeframe_veto_blocks_a_valid_local_setup():
    """The setup is good locally; the higher timeframe disagrees."""
    bars = with_hammer(uptrend())
    assert isinstance(evaluate("XAUUSDm", bars, None), Signal)

    r = evaluate("XAUUSDm", bars, downtrend())
    assert isinstance(r, Rejection)
    assert "veto" in r.reason


def test_higher_timeframe_agreement_permits_the_trade():
    s = evaluate("XAUUSDm", with_hammer(uptrend()), uptrend())
    assert isinstance(s, Signal)
    assert s.direction == "buy"


def test_unresolved_higher_timeframe_blocks():
    """No opinion upstairs means no permission - the veto is conservative."""
    flat = [bar(100.0 + (1 if i % 2 else -1), 101.5, 98.5, 100.0 + (1 if i % 2 else -1), i)
            for i in range(MIN_BARS + 20)]
    r = evaluate("XAUUSDm", with_hammer(uptrend()), flat)
    assert isinstance(r, Rejection)


def test_wide_spread_relative_to_risk_is_refused():
    """The check that decided every result in this project."""
    bars = uptrend()
    a = atr(bars)
    ok = evaluate("XAUUSDm", with_hammer(bars, spread=0.01), None)
    assert isinstance(ok, Signal)

    # A spread larger than a quarter of the stop distance.
    bad = evaluate("XAUUSDm", with_hammer(bars, spread=a * 3.0), None)
    assert isinstance(bad, Rejection)
    assert "Spread is" in bad.reason


def test_spread_ceiling_is_relative_not_absolute():
    """The same spread passes on a wide stop and fails on a narrow one."""
    bars = uptrend()
    a = atr(bars)
    spread = a * 0.5
    wide = evaluate("XAUUSDm", with_hammer(bars, spread=spread), None,
                    max_spread_fraction_of_risk=0.90)
    narrow = evaluate("XAUUSDm", with_hammer(bars, spread=spread), None,
                      max_spread_fraction_of_risk=0.05)
    assert isinstance(wide, Signal)
    assert isinstance(narrow, Rejection)


def test_too_tight_a_stop_is_refused():
    r = evaluate("XAUUSDm", with_hammer(uptrend()), None, min_stop_atr=99.0)
    assert isinstance(r, Rejection)
    assert "too tight to size" in r.reason


def test_insufficient_history_does_not_trade():
    r = evaluate("XAUUSDm", with_hammer(uptrend(60)), None)
    assert isinstance(r, Rejection)


def test_rejections_always_say_why():
    """Silence from a crash must not look like silence from no setup."""
    for bars, higher in [
        (uptrend(), None),
        (with_shooting_star(uptrend()), None),
        (with_hammer(uptrend()), downtrend()),
        (with_hammer(uptrend(60)), None),
    ]:
        r = evaluate("XAUUSDm", bars, higher)
        assert isinstance(r, Rejection)
        assert r.reason and len(r.reason) > 10
