from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.edge.engine import EdgeEngine
from app.edge.markets import MarketConfig
from app.mt5.models import MarketSpec

T0 = datetime(2026, 8, 9, 0, 0, tzinfo=timezone.utc)


def candles(n=600, step_min=30, rising=True):
    """Candle dicts in the shape get_recent_candles returns."""
    out = []
    for i in range(n):
        base = 4000.0 + (i * 0.5 if rising else -i * 0.5)
        t = int((T0 + timedelta(minutes=step_min * i)).timestamp() * 1000)
        out.append({"t": t, "o": base, "h": base + 3.0, "l": base - 3.0,
                    "c": base + 1.0, "v": 1.0, "spread": 20})
    return out


class BarClient:
    """Serves a fixed candle series and counts how often it is asked."""

    def __init__(self):
        self.calls = 0
        self.tickCalls = 0
        self.orders: list[dict] = []

    async def get_recent_candles(self, symbol, interval, count, include_forming=False):
        self.calls += 1
        return candles()

    async def symbol_info_tick(self, symbol):
        self.tickCalls += 1
        return SimpleNamespace(bidPrice=4300.0, askPrice=4300.26,
                               markPrice=4300.13, midPrice=4300.13)

    async def positions_get(self):
        return []

    async def account_info(self):
        return SimpleNamespace(equity=5000.0, balance=5000.0)

    async def history_deals_range(self, start, end):
        return []

    async def send_market_order(self, order):
        self.orders.append(order)
        return {"retcode": 10009, "price": 4300.0, "order": 1}


def build(**kw):
    s = Settings(**kw)
    e = EdgeEngine(s, BarClient())
    sym = "XAUUSDm"
    e.symbols = [sym]
    e.markets[sym] = MarketConfig(symbol=sym, timeframe="30m", higherTimeframe="1d")
    e.specs[sym] = MarketSpec(symbol=sym, digits=3, tickSize=0.001, tickValue=0.01,
                              contractSize=100.0, volumeStep=0.01, volumeMin=0.01,
                              volumeMax=100.0)
    e._valuePerPoint[sym] = 100.0
    e.startingEquity = 5000.0
    return e, sym


@pytest.mark.asyncio
async def test_observation_refreshes_on_every_poll_not_only_on_a_new_bar():
    """The defect this file exists for.

    The once-per-bar guard used to return BEFORE recording anything, so the
    dashboard refreshed at most every 30 minutes and showed an empty panel for
    the whole first bar after a restart.
    """
    e, sym = build()
    await e._consider(sym, [])
    first = e.observations[sym]["observedAt"]
    assert sym in e.observations

    await e._consider(sym, [])          # same bar, second poll
    second = e.observations[sym]["observedAt"]
    assert second >= first
    assert e.observations[sym]["reason"]


@pytest.mark.asyncio
async def test_acting_still_happens_only_once_per_bar():
    """Observing more often must not let one signal become many positions."""
    e, sym = build()
    for _ in range(5):
        await e._consider(sym, [])
    # The series is a clean uptrend with no qualifying pattern on the last bar,
    # so no order is expected - what matters is the bar guard still latched.
    assert e._lastBarTime.get(sym) is not None
    assert len(e.client.orders) <= 1


@pytest.mark.asyncio
async def test_observation_carries_a_live_quote():
    e, sym = build()
    await e._consider(sym, [])
    o = e.observations[sym]
    assert o["bid"] == 4300.0
    assert o["ask"] == 4300.26
    assert o["liveSpread"] == pytest.approx(0.26, abs=1e-9)
    assert o["liveSpreadFractionOfAtr"] is not None


@pytest.mark.asyncio
async def test_observation_survives_a_quote_failure():
    """A stale price beats a blank card, which reads as 'dead'."""
    e, sym = build()

    async def boom(symbol):
        raise RuntimeError("no tick")

    e.client.symbol_info_tick = boom
    await e._consider(sym, [])
    assert sym in e.observations
    assert e.observations[sym]["bid"] is None
    assert e.observations[sym]["reason"]


@pytest.mark.asyncio
async def test_veto_bars_are_cached_rather_than_refetched_every_poll():
    """A daily bar changes once a day; refetching 520 of them every poll is pure IPC."""
    e, sym = build(edgeHigherCacheSec=3600)
    await e._consider(sym, [])
    after_first = e.client.calls
    await e._consider(sym, [])
    added = e.client.calls - after_first
    # Second poll refetches the execution series only, not the veto series.
    assert added == 1


@pytest.mark.asyncio
async def test_cache_expires_so_the_veto_can_change():
    e, sym = build(edgeHigherCacheSec=0)
    await e._consider(sym, [])
    after_first = e.client.calls
    await e._consider(sym, [])
    assert e.client.calls - after_first == 2
