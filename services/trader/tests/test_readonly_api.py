"""Read-only mode closes the trading surface.

The dashboard only reads, but the same service exposes operator endpoints that
submit orders and pause the engine - none of which authenticate. Reaching the
API from a phone widens what can reach it, so the trading surface is closed
rather than trusted to stay unreachable.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.readonly import ReadOnlyApiMiddleware


def _app(read_only: bool) -> TestClient:
    app = FastAPI()
    app.add_middleware(ReadOnlyApiMiddleware, enabled=read_only)

    @app.get("/api/bots")
    async def bots():
        return {"ok": True}

    @app.get("/health")
    async def health():
        return {"status": "alive"}

    @app.post("/api/operator/test-order")
    async def test_order():
        return {"placed": True}

    @app.post("/api/operator/pause")
    async def pause():
        return {"paused": True}

    # A route added later, which nobody remembered to guard.
    @app.post("/api/something-new")
    async def something_new():
        return {"done": True}

    return TestClient(app)


def test_reads_still_work_when_read_only():
    client = _app(True)
    assert client.get("/api/bots").status_code == 200
    assert client.get("/health").status_code == 200


def test_order_submission_is_refused():
    r = _app(True).post("/api/operator/test-order")
    assert r.status_code == 403
    assert "read-only" in r.json()["detail"]


def test_operator_actions_are_refused():
    assert _app(True).post("/api/operator/pause").status_code == 403


def test_an_unguarded_new_route_is_still_refused():
    """Blocking by METHOD as well as path means new endpoints are safe by default."""
    assert _app(True).post("/api/something-new").status_code == 403


def test_nothing_is_blocked_when_disabled():
    client = _app(False)
    assert client.post("/api/operator/test-order").status_code == 200
    assert client.post("/api/operator/pause").status_code == 200
    assert client.get("/api/bots").status_code == 200


def test_the_refusal_says_how_to_undo_it():
    detail = _app(True).post("/api/operator/pause").json()["detail"]
    assert "API_READ_ONLY=false" in detail


# --- archived bots are omitted, not dimmed ---------------------------------

def test_archived_bots_are_absent_from_the_fleet_snapshot():
    """A retired strategy on a live dashboard invites a misreading.

    price_action was reported as still running purely because its card was
    present, even though it was paused and had not traded for hours. Its
    history lives in MT5 and in the archive tag; it does not need a card.
    """
    import warnings
    warnings.filterwarnings("ignore")
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as c:
        ids = [b["botId"] for b in c.get("/api/bots").json()["bots"]]
    assert "price_action" not in ids
    assert "scalper" not in ids
    assert "crt" not in ids


def test_activity_endpoint_reports_what_the_bot_is_waiting_for():
    import warnings
    warnings.filterwarnings("ignore")
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as c:
        r = c.get("/api/bots/edge/activity")
        assert r.status_code == 200
        d = r.json()
    for key in ("running", "signalsSeen", "declined", "topReasons",
                "markets", "refusals", "events"):
        assert key in d


def test_activity_is_a_read_and_survives_read_only_mode():
    """The whole point is being able to see why it is idle from a phone,
    which is exactly the situation read-only mode exists for."""
    from app.core.readonly import MUTATING_PREFIXES, SAFE_METHODS

    assert "GET" in SAFE_METHODS
    assert not any("/api/bots/edge/activity".startswith(p) for p in MUTATING_PREFIXES)
