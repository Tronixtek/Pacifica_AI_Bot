"""Guards against acting on stale prices.

Both bugs here were observed live, not hypothesised:
  - on restart the engine signalled off a bar that had closed 4m22s earlier
  - entry drift turned a 0.60 reward-to-risk setup into 0.05
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.config import Settings
from app.contracts import StrategySignal
from app.mt5.client import timeframe_seconds
from app.mt5.execution import Mt5ExecutionService
from app.mt5.market_data import Mt5MarketDataService
from app.mt5.models import MarketQuote


class FakeClient:
    """Serves a fixed set of bars and one live quote."""

    def __init__(self, bars_by_symbol=None, quote=None):
        self.bars_by_symbol = bars_by_symbol or {}
        self.quote = quote

    async def get_recent_candles(self, symbol, interval, count, include_forming=False):
        return self.bars_by_symbol.get(symbol, [])

    async def symbol_info_tick(self, symbol):
        return self.quote


def _rows(times, spread=8):
    """Bar rows as the client returns them; `times` are bar OPEN times."""
    return [
        {"t": int(t.timestamp() * 1000), "o": 1.10, "h": 1.11, "l": 1.09,
         "c": 1.10, "v": 100.0, "spread": spread}
        for t in times
    ]


def _service(rows, **overrides):
    settings = Settings(_env_file=None, useSimulatedFeed=False,
                        strategyTimeframe="5m", barRefreshIntervalSec=0.0, **overrides)
    svc = Mt5MarketDataService(settings, FakeClient({"EURUSDm": rows}))
    return svc


def _bar_times(n, newest_closed_secs_ago):
    """n consecutive 5m bar open-times, newest closing `secs_ago` seconds back."""
    tf = timeframe_seconds("5m")
    newest_open = datetime.now(timezone.utc) - timedelta(seconds=newest_closed_secs_ago + tf)
    return [newest_open - timedelta(seconds=tf * (n - 1 - i)) for i in range(n)]


# --- stale bar on restart -------------------------------------------------


@pytest.mark.asyncio
async def test_first_poll_never_signals():
    """The regression: on restart every symbol's last bar looks new.

    It may have closed a full timeframe ago. Prime the marker, signal nothing.
    """
    svc = _service(_rows(_bar_times(5, newest_closed_secs_ago=1)))
    assert await svc.refresh_bars(["EURUSDm"]) == {}
    assert svc.lastBarTime["EURUSDm"] is not None


@pytest.mark.asyncio
async def test_fresh_bar_after_priming_does_signal():
    times = _bar_times(5, newest_closed_secs_ago=1)
    svc = _service(_rows(times))
    await svc.refresh_bars(["EURUSDm"])                      # prime

    times.append(times[-1] + timedelta(seconds=timeframe_seconds("5m")))
    svc.client.bars_by_symbol["EURUSDm"] = _rows(times)
    # Newest bar has only just closed, so it is actionable.
    svc.lastBarTime["EURUSDm"] = times[-2]
    assert "EURUSDm" in await svc.refresh_bars(["EURUSDm"])


@pytest.mark.asyncio
async def test_stale_bar_is_skipped():
    """A bar closed beyond the freshness window must not produce a signal."""
    times = _bar_times(5, newest_closed_secs_ago=260)  # > 0.5 * 300s
    svc = _service(_rows(times))
    svc.lastBarTime["EURUSDm"] = times[-2]            # already primed
    assert await svc.refresh_bars(["EURUSDm"]) == {}
    assert "past the freshness window" in svc.lastError


@pytest.mark.asyncio
async def test_bar_just_inside_the_window_is_accepted():
    times = _bar_times(5, newest_closed_secs_ago=100)  # < 0.5 * 300s
    svc = _service(_rows(times))
    svc.lastBarTime["EURUSDm"] = times[-2]
    assert "EURUSDm" in await svc.refresh_bars(["EURUSDm"])


@pytest.mark.asyncio
async def test_unchanged_bar_does_not_resignal():
    times = _bar_times(5, newest_closed_secs_ago=1)
    svc = _service(_rows(times))
    await svc.refresh_bars(["EURUSDm"])
    assert await svc.refresh_bars(["EURUSDm"]) == {}


def test_age_is_measured_from_bar_close_not_open():
    """MT5 timestamps a bar by its OPEN; a just-closed 5m bar is not 5m old."""
    svc = _service([])
    just_closed = datetime.now(timezone.utc) - timedelta(seconds=timeframe_seconds("5m"))
    assert svc._age_seconds(just_closed) == pytest.approx(0, abs=2)
    assert not svc._is_stale(just_closed)


# --- entry drift ----------------------------------------------------------


def _signal(**overrides):
    defaults = dict(
        id="s1", symbol="EURUSDm", setup="breakout", bias="long", confidence=0.8,
        entryPrice=1.15000, stopLoss=1.14600, takeProfit=1.15200, size=0.05,
        notionalUsd=5750.0, status="approved", reason="t",
        createdAt=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return StrategySignal(**defaults)


def _quote(bid, ask):
    return MarketQuote(symbol="EURUSDm", markPrice=(bid + ask) / 2, bidPrice=bid, askPrice=ask)


def _exec_service(quote):
    return Mt5ExecutionService(Settings(_env_file=None), FakeClient(quote=quote))


@pytest.mark.asyncio
async def test_small_drift_is_allowed():
    # risk 400 pts; 25% budget = 100 pts. Ask 20 pts away.
    svc = _exec_service(_quote(1.15010, 1.15020))
    assert await svc._reject_on_entry_drift(_signal()) is None


@pytest.mark.asyncio
async def test_drift_beyond_budget_is_rejected():
    # Ask 150 pts above the signal entry, past the 100 pt budget.
    svc = _exec_service(_quote(1.15140, 1.15150))
    msg = await svc._reject_on_entry_drift(_signal())
    assert msg is not None and "past the" in msg


@pytest.mark.asyncio
async def test_the_live_failure_case_is_now_rejected():
    """The actual trade: 26 points of drift against a 50 point risk.

    Signal entry 1.34530, stop 1.34480, target 1.34560. Filled at 1.34556,
    leaving 4 points of reward against 76 of risk.
    """
    sig = _signal(symbol="GBPUSDm", entryPrice=1.34530, stopLoss=1.34480, takeProfit=1.34560)
    svc = _exec_service(_quote(1.34546, 1.34556))
    msg = await svc._reject_on_entry_drift(sig)
    assert msg is not None
    assert "52%" in msg  # 26 points of drift on 50 points of risk


@pytest.mark.asyncio
async def test_short_side_is_measured_against_the_bid():
    """A sell fills at the bid, so drift must be measured there."""
    sig = _signal(bias="short", entryPrice=1.15000, stopLoss=1.15400, takeProfit=1.14800)
    # Bid only 20 pts away, ask far off - using ask would wrongly reject.
    svc = _exec_service(_quote(1.14980, 1.15200))
    assert await svc._reject_on_entry_drift(sig) is None


@pytest.mark.asyncio
async def test_missing_quote_blocks_the_trade():
    svc = _exec_service(None)
    msg = await svc._reject_on_entry_drift(_signal())
    assert msg is not None and "refusing to submit blind" in msg
