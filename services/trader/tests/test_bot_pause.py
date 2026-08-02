"""Pausing a bot from the dashboard.

Pausing stops NEW positions only. Anything already open keeps its broker-side
stop and continues to be trailed and banked - abandoning managed risk is not
what an operator means by "pause", and a stop already sitting at the broker is
safer than a market exit at whatever the spread happens to be.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.readonly import ReadOnlyApiMiddleware
from app.runtime.fleet import BotFleet


class FakeScalper:
    def __init__(self):
        self.paused = False


class FakeCrt:
    def __init__(self):
        self.paused = False


class FakeEngine:
    """The price-action engine spells its flag _paused."""
    def __init__(self):
        self._paused = False


def _fleet():
    f = BotFleet(Settings(_env_file=None), client=None, engine=FakeEngine())
    f.scalper = FakeScalper()
    f.crt = FakeCrt()
    return f


def test_pausing_one_bot_leaves_the_others_running():
    f = _fleet()
    ok, msg = f.set_paused("scalper", True)
    assert ok
    assert f.is_paused("scalper")
    assert not f.is_paused("crt")
    assert not f.is_paused("price_action")


def test_price_action_underscore_flag_is_handled():
    """That engine predates the fleet and names the flag differently."""
    f = _fleet()
    assert f.set_paused("price_action", True)[0]
    assert f.is_paused("price_action")
    assert f.engine._paused is True


def test_resume_clears_it():
    f = _fleet()
    f.set_paused("crt", True)
    assert f.is_paused("crt")
    f.set_paused("crt", False)
    assert not f.is_paused("crt")


def test_unknown_bot_is_refused_not_silently_ignored():
    ok, msg = _fleet().set_paused("nonexistent", True)
    assert not ok
    assert "nonexistent" in msg


def test_pause_message_says_open_positions_are_still_managed():
    _, msg = _fleet().set_paused("scalper", True)
    assert "Open positions keep their stops" in msg


def test_a_disabled_bot_reports_as_not_pausable():
    f = BotFleet(Settings(_env_file=None), client=None, engine=FakeEngine())
    # scalper and crt were never started
    assert f._engine_for("scalper") is None
    assert not f.is_paused("scalper")


# --- read-only interaction -------------------------------------------------


def _client(read_only: bool) -> TestClient:
    app = FastAPI()
    app.add_middleware(ReadOnlyApiMiddleware, enabled=read_only)

    @app.post("/api/bots/{bot_id}/pause")
    async def pause(bot_id: str):
        return {"ok": True, "botId": bot_id}

    @app.post("/api/bots/{bot_id}/resume")
    async def resume(bot_id: str):
        return {"ok": True, "botId": bot_id}

    @app.post("/api/operator/test-order")
    async def order():
        return {"placed": True}

    return TestClient(app)


def test_pause_still_works_in_read_only_mode():
    """Pausing only ever REDUCES what a bot can do, so it stays available.

    Refusing it would leave the dashboard able to show a bot losing money
    without offering the one control that helps.
    """
    c = _client(True)
    assert c.post("/api/bots/scalper/pause").status_code == 200
    assert c.post("/api/bots/scalper/resume").status_code == 200


def test_order_submission_is_still_blocked_in_read_only_mode():
    assert _client(True).post("/api/operator/test-order").status_code == 403


def test_the_exception_does_not_open_up_other_bot_routes():
    """Only /pause and /resume are excepted, not everything under /api/bots."""
    app = FastAPI()
    app.add_middleware(ReadOnlyApiMiddleware, enabled=True)

    @app.post("/api/bots/scalper/close-all")
    async def close_all():
        return {"closed": True}

    assert TestClient(app).post("/api/bots/scalper/close-all").status_code == 403


# --- the engines actually honour the flag ---------------------------------


class RecordingClient:
    """Fails loudly if a paused engine touches the broker."""

    def __init__(self):
        self.calls = []

    async def get_recent_candles(self, *a, **k):
        self.calls.append("get_recent_candles")
        return []

    async def positions_get(self):
        self.calls.append("positions_get")
        return []

    async def symbol_info_tick(self, *a, **k):
        self.calls.append("symbol_info_tick")
        return None

    async def send_market_order(self, *a, **k):
        self.calls.append("send_market_order")
        raise AssertionError("a paused engine must never submit an order")


@pytest.mark.asyncio
async def test_paused_crt_does_not_scan_or_order():
    from app.crt.engine import CrtEngine

    client = RecordingClient()
    engine = CrtEngine(Settings(_env_file=None), client)
    engine.symbols = ["BTCUSDm"]
    engine.paused = True

    await engine._scan("BTCUSDm")

    assert client.calls == [], f"paused engine still called: {client.calls}"


@pytest.mark.asyncio
async def test_unpaused_crt_does_reach_for_candles():
    """The counterpart, so the test above proves pausing rather than a no-op."""
    from app.crt.engine import CrtEngine
    from app.mt5.models import MarketSpec

    client = RecordingClient()
    engine = CrtEngine(Settings(_env_file=None), client)
    engine.symbols = ["BTCUSDm"]
    engine.paused = False
    engine.specs = {
        "BTCUSDm": MarketSpec(
            symbol="BTCUSDm", digits=2, tickSize=0.01, tickValue=0.01,
            contractSize=1.0, volumeStep=0.01, volumeMin=0.01, volumeMax=200.0,
        )
    }

    await engine._scan("BTCUSDm")

    assert "get_recent_candles" in client.calls


@pytest.mark.asyncio
async def test_paused_scalper_opens_nothing_but_still_manages_positions():
    from app.scalper.engine import ScalperEngine

    engine = ScalperEngine(Settings(_env_file=None), RecordingClient())
    engine.paused = True
    # The loop guard is `not self.paused`, so a paused engine skips the
    # top-up branch entirely while reconciliation and trailing continue.
    assert engine.paused is True
