from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import settings
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.runtime.engine import TradingEngine
from app.runtime.fleet import BotFleet


configure_logging(settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = TradingEngine(settings)
    app.state.engine = engine
    await engine.start()

    # The extra bots start only after the main engine has resolved broker
    # symbol names and loaded specs, and they reuse both. Resolving twice would
    # risk them trading a different instrument than the dashboard reports.
    fleet = BotFleet(settings, engine.client)
    app.state.fleet = fleet
    if not settings.useSimulatedFeed:
        await fleet.start(engine.engineSymbols, engine.marketData.marketSpecs)
    try:
        yield
    finally:
        await fleet.stop()
        await engine.stop()


app = FastAPI(
    title="VTFX MT5 Trader Service",
    version="0.1.0",
    lifespan=lifespan,
)

dev_origins = list(
    {
        settings.frontendOrigin,
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001",
        "http://localhost:3000",
        "http://localhost:3001",
    }
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=dev_origins,
    allow_origin_regex=r"https?://(127\.0\.0\.1|localhost)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestContextMiddleware)

app.include_router(router)
