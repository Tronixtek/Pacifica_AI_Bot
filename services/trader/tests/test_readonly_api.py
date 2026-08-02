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
