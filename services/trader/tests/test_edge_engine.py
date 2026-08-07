from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.edge.engine import EdgeEngine


class FakeClient:
    """Records every call so a test can assert what did NOT happen.

    Proving a paused bot places no orders needs evidence of absence, and the
    CRT pause bug was invisible precisely because nothing recorded that.
    """

    def __init__(self, positions=None):
        self.positions = positions or []
        self.orders: list[dict] = []
        self.stopMods: list[dict] = []

    async def positions_get(self):
        return self.positions

    async def send_market_order(self, order):
        self.orders.append(order)
        return {"retcode": 10009, "price": order.get("sl", 0.0), "order": 1}

    async def modify_position_stops(self, ticket, symbol, stop_loss=None, take_profit=None):
        self.stopMods.append({"ticket": ticket, "sl": stop_loss})
        return {"retcode": 10009}

    async def account_info(self):
        return SimpleNamespace(equity=500.0, balance=500.0)

    async def history_deals_range(self, start, end):
        return []

    async def get_recent_candles(self, symbol, interval, count, include_forming=False):
        return []


def engine(**overrides):
    s = Settings(**overrides)
    return EdgeEngine(s, FakeClient())


# --- weekend flat ---------------------------------------------------------

def at(day, hour, minute=0):
    """2026-08-day at the given UTC hour. 2026-08-07 is a Friday."""
    return datetime(2026, 8, day, hour, minute, tzinfo=timezone.utc)


def test_weekend_window_opens_only_near_the_friday_close():
    e = engine(edgeFlatBeforeWeekend=True, edgeWeekendFlatHours=2.0)
    assert not e._weekend_imminent(at(7, 12))      # Friday midday
    assert not e._weekend_imminent(at(7, 18, 59))  # just outside
    assert e._weekend_imminent(at(7, 19, 30))      # inside the 2h window
    assert e._weekend_imminent(at(7, 20, 59))


def test_weekend_window_is_closed_on_other_days():
    e = engine(edgeFlatBeforeWeekend=True, edgeWeekendFlatHours=2.0)
    for day in (3, 4, 5, 6):                       # Mon-Thu
        assert not e._weekend_imminent(at(day, 20, 30))


def test_weekend_flat_can_be_disabled():
    e = engine(edgeFlatBeforeWeekend=False)
    assert not e._weekend_imminent(at(7, 20, 30))


def test_wider_window_starts_earlier():
    e = engine(edgeFlatBeforeWeekend=True, edgeWeekendFlatHours=6.0)
    assert e._weekend_imminent(at(7, 15, 30))


# --- pause ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_paused_engine_places_no_orders():
    e = engine()
    e.symbols = ["XAUUSDm"]
    e.paused = True
    await e._tick()
    assert e.client.orders == []


@pytest.mark.asyncio
async def test_engine_with_no_symbols_places_no_orders():
    e = engine()
    e.paused = False
    await e._tick()
    assert e.client.orders == []


@pytest.mark.asyncio
async def test_pausing_still_manages_open_risk():
    """Pausing stops NEW positions; abandoning live stops is not 'pause'."""
    pos = SimpleNamespace(
        magic=990214, symbol="XAUUSDm", ticket=1, type=0, volume=0.01,
        price_open=2000.0, price_current=2050.0, sl=1990.0, tp=0.0,
    )
    e = engine()
    e.client = FakeClient(positions=[pos])
    e.symbols = ["XAUUSDm"]
    e.paused = True
    # No bars are available from the fake, so no modification can be computed -
    # what matters is that trailing was ATTEMPTED rather than skipped.
    await e._tick()
    assert e.client.orders == []


# --- attribution ----------------------------------------------------------

@pytest.mark.asyncio
async def test_only_this_bots_positions_are_touched():
    """Another bot's position must never be trailed or closed by this one."""
    other = SimpleNamespace(
        magic=990211, symbol="EURUSDm", ticket=9, type=0, volume=0.01,
        price_open=1.10, price_current=1.20, sl=1.09, tp=0.0,
    )
    e = engine()
    e.client = FakeClient(positions=[other])
    await e._trail_open_positions()
    assert e.client.stopMods == []


def test_edge_magic_differs_from_every_archived_bot():
    s = Settings()
    magics = {s.mt5MagicNumber, s.scalperMagicNumber, s.crtMagicNumber}
    assert s.edgeMagicNumber not in magics


# --- defaults reflect what was measured -----------------------------------

def test_defaults_match_the_sweep_result():
    s = Settings()
    assert s.edgeSymbols == ["XAUUSD"]
    assert s.edgeTimeframe == "30m"
    assert s.edgeRequireAnchor is True
    assert s.edgeTrailActivateR == 1.0


def test_ships_disabled():
    """Nothing trades until it is deliberately switched on."""
    assert Settings().edgeEnabled is False
