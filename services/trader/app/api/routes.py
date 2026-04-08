from __future__ import annotations

from fastapi import APIRouter, Request

from app.contracts import (
    AccountLinkRequest,
    AccountLinkResponse,
    DashboardSnapshot,
    DiagnosticsResponse,
    HealthResponse,
    OperatorActionResponse,
    PaperBalanceTopUpRequest,
    SmokeTestOrderRequest,
    SignalPreviewResponse,
)
from app.runtime.engine import TradingEngine

router = APIRouter()


def get_engine(request: Request) -> TradingEngine:
    return request.app.state.engine


@router.get("/livez")
async def livez() -> dict[str, str]:
    return {"status": "alive"}


@router.get("/readyz")
async def readyz(request: Request) -> dict[str, object]:
    health = get_engine(request).health()
    return {
        "status": "ready" if health.status != "offline" else "not_ready",
        "engineStatus": health.status,
        "message": health.message,
    }


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    return get_engine(request).health()


@router.get("/api/overview", response_model=DashboardSnapshot)
async def overview(request: Request) -> DashboardSnapshot:
    return get_engine(request).dashboard_snapshot()


@router.get("/api/diagnostics", response_model=DiagnosticsResponse)
async def diagnostics(request: Request, live_probe: bool = False) -> DiagnosticsResponse:
    return await get_engine(request).diagnostics(live_probe=live_probe)


@router.post("/api/operator/pause", response_model=OperatorActionResponse)
async def pause(request: Request) -> OperatorActionResponse:
    return get_engine(request).pause()


@router.post("/api/operator/resume", response_model=OperatorActionResponse)
async def resume(request: Request) -> OperatorActionResponse:
    return get_engine(request).resume()


@router.post("/api/operator/sync-account", response_model=OperatorActionResponse)
async def sync_account(request: Request) -> OperatorActionResponse:
    return await get_engine(request).force_account_sync()


@router.post("/api/operator/paper-account/reset", response_model=OperatorActionResponse)
async def reset_paper_account(request: Request) -> OperatorActionResponse:
    return get_engine(request).reset_paper_account()


@router.post("/api/operator/paper-account/top-up", response_model=OperatorActionResponse)
async def top_up_paper_account(
    request: Request,
    payload: PaperBalanceTopUpRequest,
) -> OperatorActionResponse:
    return get_engine(request).top_up_paper_account(payload.amountUsd)


@router.post("/api/operator/link-account", response_model=AccountLinkResponse)
async def link_account(request: Request, payload: AccountLinkRequest) -> AccountLinkResponse:
    return get_engine(request).link_account(payload.accountAddress)


@router.post("/api/operator/unlink-account", response_model=AccountLinkResponse)
async def unlink_account(request: Request) -> AccountLinkResponse:
    return get_engine(request).unlink_account()


@router.post("/api/operator/signals/{signal_id}/preview", response_model=SignalPreviewResponse)
async def preview_signal(request: Request, signal_id: str) -> SignalPreviewResponse:
    return get_engine(request).preview_signal(signal_id)


@router.post("/api/operator/test-order", response_model=OperatorActionResponse)
async def submit_test_order(
    request: Request,
    payload: SmokeTestOrderRequest,
) -> OperatorActionResponse:
    return await get_engine(request).submit_smoke_test_order(payload.symbol)
