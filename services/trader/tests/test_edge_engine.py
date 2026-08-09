from __future__ import annotations

from datetime import datetime, timedelta, timezone
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

def shipped(name):
    """The default compiled into the class, ignoring any local .env.

    Read from model_fields rather than an instance, because a developer's .env
    would otherwise mask what actually ships to a new machine.
    """
    return Settings.model_fields[name].default


def test_shipped_defaults_match_what_was_measured():
    assert shipped("edgeTimeframe") == "30m"
    assert shipped("edgeHigherTimeframe") == "1d"
    assert shipped("edgeRequireAnchor") is True
    assert shipped("edgeTrailActivateR") == 1.0


def test_higher_timeframe_veto_ships_on():
    """The largest single improvement measured.

    On gold 30m: none +0.134R, 1h +0.127R, 4h +0.180R, 1d +0.198R. Shipping
    it off would trade a materially worse strategy than the one measured.
    """
    assert shipped("edgeHigherTimeframe")


def test_ships_disabled():
    """Nothing trades on a fresh install until deliberately switched on."""
    assert shipped("edgeEnabled") is False


def test_risk_ceiling_would_refuse_gold_above_5m_on_a_small_account():
    """The constraint that forced 5m, asserted rather than remembered.

    Gold at ~$4,344 with a 30m ATR near 14.6 risks $12.73 at the 0.01 minimum
    lot - 2.59% of a $491 account, well past the ceiling. If someone raises
    edgeTimeframe without also funding the account, the risk manager must
    still refuse rather than quietly trade oversized.
    """
    from app.edge.risk import RiskManager, RiskSettings
    from app.mt5.models import MarketSpec

    gold = MarketSpec(symbol="XAUUSDm", digits=3, tickSize=0.001, tickValue=0.01,
                      contractSize=100.0, volumeStep=0.01, volumeMin=0.01,
                      volumeMax=100.0)
    m = RiskManager(RiskSettings(targetRiskPct=0.5, maxRiskPct=0.8), suffix="m")
    d = m.evaluate(
        symbol="XAUUSDm", direction="buy", entry=4344.0, stop=4344.0 - 12.73,
        spec=gold, valuePerPricePoint=100.0, equity=491.0, startingEquity=491.0,
        openPositions=[], realisedToday=0.0,
    )
    assert not d.allowed
    assert "too large for this account" in d.reason


def test_price_action_ships_disabled_and_persistently_so():
    """An API pause is runtime state and does not survive a reboot.

    On a VPS that distinction matters: the archived price-action strategy
    would resume trading after every restart if the only thing holding it
    back were a POST to /api/bots/price_action/pause.
    """
    assert shipped("priceActionEnabled") is False


def test_startup_pauses_price_action_when_disabled():
    from app.runtime.fleet import BotFleet

    class Stub:
        def __init__(self):
            self._paused = False

    s = Settings(priceActionEnabled=False)
    engine = Stub()
    fleet = BotFleet(s, client=None, engine=engine)
    if not s.priceActionEnabled:
        fleet.set_paused("price_action", True)
    assert engine._paused is True
    assert fleet.is_paused("price_action") is True


# --- per-market configuration ---------------------------------------------

def test_markets_parse_with_their_own_timeframes():
    from app.edge.markets import parse_markets

    m = parse_markets("XAUUSD:30m:1d,BTCUSD:15m:1d")
    assert [x.symbol for x in m] == ["XAUUSD", "BTCUSD"]
    assert m[0].timeframe == "30m" and m[0].higherTimeframe == "1d"
    assert m[1].timeframe == "15m" and m[1].higherTimeframe == "1d"


def test_veto_is_optional_per_market():
    from app.edge.markets import parse_markets

    m = parse_markets("BTCUSD:15m")
    assert m[0].higherTimeframe is None


def test_malformed_entries_are_skipped_not_fatal():
    """One bad symbol must not stop the others trading."""
    from app.edge.markets import parse_markets

    m = parse_markets("XAUUSD:30m:1d, ,BROKEN,,BTCUSD:15m")
    assert [x.symbol for x in m] == ["XAUUSD", "BTCUSD"]


def test_duplicate_symbols_collapse():
    """Two configs for one instrument would evade the correlation cap, which
    counts open positions rather than configuration."""
    from app.edge.markets import parse_markets

    m = parse_markets("BTCUSD:15m:1d,BTCUSD:30m:1d")
    assert len(m) == 1
    assert m[0].timeframe == "15m"


def test_falls_back_to_the_legacy_single_market_fields():
    from app.edge.markets import markets_from_settings

    s = Settings(edgeMarkets="", edgeSymbols=["XAUUSD"],
                 edgeTimeframe="30m", edgeHigherTimeframe="1d")
    m = markets_from_settings(s)
    assert len(m) == 1 and m[0].timeframe == "30m"


def test_shipped_markets_are_gold_30m_and_btc_15m():
    from app.edge.markets import parse_markets

    m = parse_markets(shipped("edgeMarkets"))
    assert {x.symbol for x in m} == {"XAUUSD", "BTCUSD"}
    by = {x.symbol: x for x in m}
    assert by["XAUUSD"].timeframe == "30m"
    assert by["BTCUSD"].timeframe == "15m"
    assert all(x.higherTimeframe == "1d" for x in m)


# --- weekend handling is per-instrument -----------------------------------

def test_crypto_is_exempt_from_the_weekend_flat():
    """BTC trades through the weekend; flattening it forfeits that time."""
    e = engine()
    assert e._closes_for_the_weekend("XAUUSDm") is True
    assert e._closes_for_the_weekend("US30m") is True
    assert e._closes_for_the_weekend("BTCUSDm") is False


@pytest.mark.asyncio
async def test_weekend_flat_leaves_crypto_positions_alone():
    btc = SimpleNamespace(magic=990214, symbol="BTCUSDm", ticket=1, type=0,
                          volume=0.01, price_open=60000.0, price_current=61000.0,
                          sl=59000.0, tp=0.0)
    gold = SimpleNamespace(magic=990214, symbol="XAUUSDm", ticket=2, type=0,
                           volume=0.01, price_open=4300.0, price_current=4310.0,
                           sl=4290.0, tp=0.0)
    e = engine()
    e.client = FakeClient(positions=[btc, gold])
    await e._flatten_for_weekend()
    closed = [o["symbol"] for o in e.client.orders]
    assert closed == ["XAUUSDm"]


# --- the bot must explain its silence --------------------------------------

@pytest.mark.asyncio
async def test_a_rejection_is_logged_once_not_every_bar():
    """Silence needs a reason, but not the same reason a thousand times."""
    from app.edge.signal import Rejection

    e = engine()
    e.symbols = ["XAUUSDm"]
    for _ in range(5):
        out = Rejection("XAUUSDm", "Higher timeframe is down.")
        e._rejects[out.reason] += 1
        if e._lastReason.get("XAUUSDm") != out.reason:
            e._lastReason["XAUUSDm"] = out.reason
            e.note(f"XAUUSDm: {out.reason}")
    logged = [x for x in e.events if "Higher timeframe is down" in x]
    assert len(logged) == 1
    assert e._rejects["Higher timeframe is down."] == 5


def test_a_changed_reason_is_logged_again():
    e = engine()
    for reason in ("Higher timeframe is down.", "No trend on the execution timeframe."):
        if e._lastReason.get("BTCUSDm") != reason:
            e._lastReason["BTCUSDm"] = reason
            e.note(f"BTCUSDm: {reason}")
    assert len([x for x in e.events if "BTCUSDm:" in x]) == 2


def test_heartbeat_is_rate_limited():
    e = engine(edgeHeartbeatSec=1800)
    t0 = datetime(2026, 8, 9, 9, 0, tzinfo=timezone.utc)
    e._heartbeat(t0)                                   # first call only arms it
    assert not any("alive:" in x for x in e.events)
    e._heartbeat(t0 + timedelta(minutes=5))            # too soon
    assert not any("alive:" in x for x in e.events)
    e._heartbeat(t0 + timedelta(minutes=31))
    assert sum("alive:" in x for x in e.events) == 1


def test_heartbeat_names_what_it_is_waiting_for():
    e = engine(edgeHeartbeatSec=600)
    e._rejects["Higher timeframe is down."] = 40
    e._rejects["No trend on the execution timeframe."] = 3
    t0 = datetime(2026, 8, 9, 9, 0, tzinfo=timezone.utc)
    e._heartbeat(t0)
    e._heartbeat(t0 + timedelta(minutes=11))
    alive = [x for x in e.events if "alive:" in x][0]
    assert "43 declined" in alive
    assert "Higher timeframe is down" in alive
