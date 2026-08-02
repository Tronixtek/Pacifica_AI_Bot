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
