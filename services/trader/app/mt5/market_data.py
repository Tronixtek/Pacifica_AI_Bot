from __future__ import annotations

from datetime import datetime, timezone

from app.config import Settings
from app.contracts import ServiceHealth
from app.mt5.client import Mt5Client
from app.mt5.models import MarketQuote, MarketSpec


class Mt5MarketDataService:
    """Poll-only market data, since MT5's Python API has no push/websocket feed."""

    def __init__(self, settings: Settings, client: Mt5Client) -> None:
        self.settings = settings
        self.client = client
        self.marketSpecs: dict[str, MarketSpec] = {}
        self.quotes: dict[str, MarketQuote] = {}
        self.lastError: str | None = None
        self.lastPollAt: datetime | None = None
        self.lastQuoteSource = "simulated" if settings.useSimulatedFeed else "poll"

    async def start(self) -> None:
        if self.settings.useSimulatedFeed:
            return
        try:
            await self.sync_market_specs()
        except Exception as exc:
            self.lastError = f"MT5 market spec sync failed: {exc}"

    async def stop(self) -> None:
        return

    async def sync_market_specs(self) -> dict[str, MarketSpec]:
        specs: dict[str, MarketSpec] = {}
        for symbol in self.settings.symbols:
            selected = await self.client.symbol_select(symbol)
            if not selected:
                self.lastError = f"MT5 symbol {symbol} could not be selected in Market Watch."
                continue
            spec = await self.client.symbol_info(symbol)
            if spec is not None:
                specs[symbol] = spec
        self.marketSpecs = specs
        return specs

    async def refresh_quotes(self, symbols: list[str]) -> dict[str, MarketQuote]:
        requested = list(dict.fromkeys(symbols))
        try:
            for symbol in requested:
                quote = await self.client.symbol_info_tick(symbol)
                if quote is not None:
                    self.quotes[symbol] = quote
            self.lastPollAt = datetime.now(timezone.utc)
            self.lastQuoteSource = "poll"
            self.lastError = None
        except Exception as exc:
            self.lastError = f"MT5 tick poll failed: {exc}"

        return {symbol: self.quotes[symbol] for symbol in requested if symbol in self.quotes}

    def health(self) -> ServiceHealth:
        if self.settings.useSimulatedFeed:
            return ServiceHealth(
                id="market_data",
                label="Market Data",
                status="healthy",
                message="Simulated market feed is active.",
            )

        if self.lastPollAt and self._is_fresh(self.lastPollAt):
            return ServiceHealth(
                id="market_data",
                label="Market Data",
                status="healthy",
                message="Polling MT5 terminal ticks.",
            )

        message = self.lastError or "Waiting for MT5 market data."
        return ServiceHealth(
            id="market_data",
            label="Market Data",
            status="degraded",
            message=message,
        )

    def _is_fresh(self, timestamp: datetime) -> bool:
        age = datetime.now(timezone.utc) - timestamp
        return age.total_seconds() <= self.settings.marketDataStaleAfterSec
