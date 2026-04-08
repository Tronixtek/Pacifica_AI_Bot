from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from app.config import Settings
from app.contracts import ServiceHealth
from app.pacifica.client import PacificaClient
from app.pacifica.models import MarketQuote, MarketSpec


class PacificaMarketDataService:
    def __init__(self, settings: Settings, client: PacificaClient) -> None:
        self.settings = settings
        self.client = client
        self.marketSpecs: dict[str, MarketSpec] = {}
        self.quotes: dict[str, MarketQuote] = {}
        self.lastError: str | None = None
        self.lastRestSyncAt: datetime | None = None
        self.lastWsMessageAt: datetime | None = None
        self.lastQuoteSource = "simulated" if settings.useSimulatedFeed else "rest"
        self.websocketConnected = False
        self._wsTask: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        if self.settings.useSimulatedFeed:
            return
        try:
            await self.sync_market_specs()
        except Exception as exc:
            self.lastError = f"Market spec sync failed: {exc}"
        if self.settings.preferWebsocketFeed:
            self._running = True
            self._wsTask = asyncio.create_task(self._run_websocket_loop())

    async def stop(self) -> None:
        self._running = False
        if self._wsTask is not None:
            self._wsTask.cancel()
            try:
                await self._wsTask
            except asyncio.CancelledError:
                pass

    async def sync_market_specs(self) -> dict[str, MarketSpec]:
        self.marketSpecs = await self.client.get_market_info(self.settings.symbols)
        return self.marketSpecs

    async def refresh_quotes(self, symbols: list[str]) -> dict[str, MarketQuote]:
        requested = list(dict.fromkeys(symbols))
        if self._quotes_are_fresh(requested):
            return {symbol: self.quotes[symbol] for symbol in requested if symbol in self.quotes}

        try:
            rest_quotes = await self.client.get_prices(requested)
            for symbol, quote in rest_quotes.items():
                previous = self.quotes.get(symbol)
                self.quotes[symbol] = MarketQuote(
                    symbol=symbol,
                    markPrice=quote.markPrice,
                    midPrice=quote.midPrice,
                    bidPrice=previous.bidPrice if previous else None,
                    askPrice=previous.askPrice if previous else None,
                    updatedAt=quote.updatedAt or datetime.now(timezone.utc),
                    lastOrderId=previous.lastOrderId if previous else None,
                )
            self.lastRestSyncAt = datetime.now(timezone.utc)
            if rest_quotes:
                self.lastQuoteSource = "rest"
            self.lastError = None
        except Exception as exc:
            self.lastError = f"REST market data refresh failed: {exc}"

        return {symbol: self.quotes[symbol] for symbol in requested if symbol in self.quotes}

    def health(self) -> ServiceHealth:
        if self.settings.useSimulatedFeed:
            return ServiceHealth(
                id="market_data",
                label="Market Data",
                status="healthy",
                message="Simulated market feed is active.",
            )

        if self.websocketConnected and self._is_fresh(self.lastWsMessageAt):
            return ServiceHealth(
                id="market_data",
                label="Market Data",
                status="healthy",
                message="Streaming Pacifica prices and BBO via websocket.",
            )

        if self.lastRestSyncAt and self._is_fresh(self.lastRestSyncAt):
            status = "degraded" if self.settings.preferWebsocketFeed else "healthy"
            return ServiceHealth(
                id="market_data",
                label="Market Data",
                status=status,
                message=(
                    "Using Pacifica REST fallback because websocket data is not fresh."
                    if self.settings.preferWebsocketFeed
                    else "Polling Pacifica REST prices."
                ),
            )

        message = self.lastError or "Waiting for Pacifica market data."
        return ServiceHealth(
            id="market_data",
            label="Market Data",
            status="degraded",
            message=message,
        )

    async def _run_websocket_loop(self) -> None:
        try:
            from websockets import connect
        except ImportError as exc:
            self.lastError = f"websockets package unavailable: {exc}"
            self.websocketConnected = False
            return

        while self._running:
            try:
                async with connect(self.settings.pacificaWsUrl, ping_interval=None) as websocket:
                    self.websocketConnected = True
                    self.lastError = None
                    await websocket.send(
                        json.dumps(
                            {
                                "method": "subscribe",
                                "params": {"source": "prices"},
                            }
                        )
                    )
                    for symbol in self.settings.symbols:
                        await websocket.send(
                            json.dumps(
                                {
                                    "method": "subscribe",
                                    "params": {"source": "bbo", "symbol": symbol},
                                }
                            )
                        )

                    while self._running:
                        try:
                            raw_message = await asyncio.wait_for(
                                websocket.recv(),
                                timeout=self.settings.wsHeartbeatSec,
                            )
                        except TimeoutError:
                            await websocket.send(json.dumps({"method": "ping"}))
                            continue
                        self.lastWsMessageAt = datetime.now(timezone.utc)
                        self._handle_ws_message(raw_message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.websocketConnected = False
                self.lastError = f"Websocket feed unavailable: {exc}"
                await asyncio.sleep(min(10.0, self.settings.wsHeartbeatSec))
            finally:
                self.websocketConnected = False

    def _handle_ws_message(self, raw_message: str) -> None:
        payload = json.loads(raw_message)
        channel = payload.get("channel")
        data = payload.get("data")

        if channel == "prices" and isinstance(data, list):
            for item in data:
                symbol = item.get("symbol")
                if not symbol or symbol not in self.settings.symbols:
                    continue
                previous = self.quotes.get(symbol)
                self.quotes[symbol] = MarketQuote(
                    symbol=symbol,
                    markPrice=float(item["mark"]),
                    midPrice=self._optional_float(item.get("mid")),
                    bidPrice=previous.bidPrice if previous else None,
                    askPrice=previous.askPrice if previous else None,
                    updatedAt=self._parse_timestamp(item.get("timestamp")),
                    lastOrderId=previous.lastOrderId if previous else None,
                )
            self.lastQuoteSource = "websocket"
            return

        if channel == "bbo" and isinstance(data, dict):
            symbol = data.get("s")
            if not symbol:
                return
            previous = self.quotes.get(symbol)
            self.quotes[symbol] = MarketQuote(
                symbol=symbol,
                markPrice=previous.markPrice if previous else self._optional_float(data.get("b")) or 0.0,
                midPrice=previous.midPrice if previous else None,
                bidPrice=self._optional_float(data.get("b")),
                askPrice=self._optional_float(data.get("a")),
                updatedAt=self._parse_timestamp(data.get("t")),
                lastOrderId=int(data["li"]) if data.get("li") is not None else None,
            )
            self.lastQuoteSource = "websocket"
            return

        if channel == "pong":
            self.lastWsMessageAt = datetime.now(timezone.utc)

    def _quotes_are_fresh(self, symbols: list[str]) -> bool:
        if not symbols:
            return False
        return all(
            symbol in self.quotes
            and self._is_fresh(self.quotes[symbol].updatedAt)
            for symbol in symbols
        )

    def _is_fresh(self, timestamp: datetime | None) -> bool:
        if timestamp is None:
            return False
        age = datetime.now(timezone.utc) - timestamp
        return age.total_seconds() <= self.settings.marketDataStaleAfterSec

    def _optional_float(self, value: Any) -> float | None:
        if value is None:
            return None
        return float(value)

    def _parse_timestamp(self, value: Any) -> datetime | None:
        if value is None:
            return None
        timestamp = int(value)
        if timestamp > 10_000_000_000:
            return datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
