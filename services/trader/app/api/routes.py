from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Request

from app.contracts import (
    BotControlResponse,
    BotPerformanceSnapshot,
    FleetSnapshot,
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


def get_fleet(request: Request):
    return getattr(request.app.state, "fleet", None)


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


@router.post("/api/operator/signals/{signal_id}/preview", response_model=SignalPreviewResponse)
async def preview_signal(request: Request, signal_id: str) -> SignalPreviewResponse:
    return get_engine(request).preview_signal(signal_id)


@router.post("/api/operator/test-order", response_model=OperatorActionResponse)
async def submit_test_order(
    request: Request,
    payload: SmokeTestOrderRequest,
) -> OperatorActionResponse:
    return await get_engine(request).submit_smoke_test_order(payload.symbol)


@router.get("/api/bots", response_model=FleetSnapshot)
async def bots(request: Request) -> FleetSnapshot:
    """Per-bot performance, attributed by magic number from account history."""
    engine = get_engine(request)
    fleet = get_fleet(request)
    account = engine.state.remoteAccount

    rows: list[BotPerformanceSnapshot] = []
    if fleet is not None:
        for perf in await fleet.performance():
            rows.append(
                BotPerformanceSnapshot(
                    botId=perf.botId,
                    label=perf.label,
                    magicNumber=perf.magicNumber,
                    trades=perf.trades,
                    wins=perf.wins,
                    losses=perf.losses,
                    winRate=perf.winRate,
                    realisedUsd=perf.realisedUsd,
                    unrealisedUsd=perf.unrealisedUsd,
                    equityImpactUsd=perf.equityImpactUsd,
                    averageUsd=perf.averageUsd,
                    openPositions=perf.openPositions,
                    openVolume=perf.openVolume,
                    bestUsd=perf.bestUsd,
                    worstUsd=perf.worstUsd,
                    symbols=perf.symbols,
                    lastTradeAt=perf.lastTradeAt,
                    paused=fleet.is_paused(perf.botId),
                    canPause=fleet._engine_for(perf.botId) is not None,
                )
            )

    return FleetSnapshot(
        generatedAt=datetime.now(timezone.utc),
        accountBalanceUsd=account.balanceUsd if account else None,
        accountEquityUsd=account.equityUsd if account else None,
        currency=account.currency if account else None,
        openPositions=sum(r.openPositions for r in rows),
        bots=rows,
    )


@router.post("/api/bots/{bot_id}/pause", response_model=BotControlResponse)
async def pause_bot(request: Request, bot_id: str) -> BotControlResponse:
    """Stop a bot opening NEW positions.

    Open positions keep their broker-side stops and continue to be trailed and
    banked. Pausing is deliberately not "close everything": abandoning managed
    risk is not what an operator means, and a stop already at the broker is
    safer than a market exit at whatever the spread happens to be.
    """
    fleet = get_fleet(request)
    if fleet is None:
        return BotControlResponse(ok=False, botId=bot_id, paused=False,
                                  message="No bot fleet is running.")
    ok, message = fleet.set_paused(bot_id, True)
    return BotControlResponse(ok=ok, botId=bot_id, paused=fleet.is_paused(bot_id),
                              message=message)


@router.post("/api/bots/{bot_id}/resume", response_model=BotControlResponse)
async def resume_bot(request: Request, bot_id: str) -> BotControlResponse:
    fleet = get_fleet(request)
    if fleet is None:
        return BotControlResponse(ok=False, botId=bot_id, paused=False,
                                  message="No bot fleet is running.")
    ok, message = fleet.set_paused(bot_id, False)
    return BotControlResponse(ok=ok, botId=bot_id, paused=fleet.is_paused(bot_id),
                              message=message)
