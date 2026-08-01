from __future__ import annotations

from datetime import datetime, timezone

from app.config import Settings
from app.contracts import ServiceHealth
from app.mt5.client import Mt5Client, timeframe_seconds
from app.mt5.models import Bar, MarketQuote, MarketSpec
from app.mt5.symbols import SymbolResolution, SymbolResolver


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
        self.resolution = SymbolResolution()
        self.bars: dict[str, list[Bar]] = {}
        self.lastBarTime: dict[str, datetime] = {}
        self.lastBarFetchAt: datetime | None = None
        self.skippedStaleBars = 0

    @property
    def tradingSymbols(self) -> list[str]:
        """Broker-side symbol names the engine should actually trade.

        Falls back to the configured names so paper mode (which never talks to
        a terminal) keeps working unchanged.
        """
        if self.resolution.resolved:
            return self.resolution.brokerSymbols
        return list(self.settings.symbols)

    async def start(self) -> None:
        if self.settings.useSimulatedFeed:
            return
        try:
            await self.resolve_symbols()
            await self.sync_market_specs()
        except Exception as exc:
            self.lastError = f"MT5 market spec sync failed: {exc}"

    async def stop(self) -> None:
        return

    async def resolve_symbols(self) -> SymbolResolution:
        """Translate configured names onto whatever this broker calls them."""
        available = await self.client.list_symbol_names()
        if not available:
            self.lastError = "MT5 returned no tradable symbols."
            return self.resolution

        resolver = SymbolResolver(available, self.settings.symbolSuffix)
        self.resolution = resolver.resolve(self.settings.symbols)
        if self.resolution.unresolved:
            self.lastError = (
                f"No broker symbol matched: {', '.join(self.resolution.unresolved)}."
            )
        return self.resolution

    async def sync_market_specs(self) -> dict[str, MarketSpec]:
        specs: dict[str, MarketSpec] = {}
        for symbol in self.tradingSymbols:
            selected = await self.client.symbol_select(symbol)
            if not selected:
                self.lastError = f"MT5 symbol {symbol} could not be selected in Market Watch."
                continue
            # Specs read before the first tick report a zero tick value on any
            # pair the terminal has to currency-convert, which breaks sizing.
            if not await self.client.wait_for_tick(symbol):
                self.lastError = f"MT5 symbol {symbol} was selected but never produced a tick."
                continue
            spec = await self.client.symbol_info(symbol)
            if spec is not None:
                specs[symbol] = spec
        self.marketSpecs = specs
        return specs

    async def refresh_bars(self, symbols: list[str]) -> dict[str, list[Bar]]:
        """Pull closed bars for each symbol, returning only those that changed.

        A symbol is reported only when its newest closed bar is one the engine
        has not evaluated yet, so the caller can drive strategy evaluation off
        bar closes without re-running on every poll.
        """
        if self.settings.useSimulatedFeed:
            return {}

        updated: dict[str, list[Bar]] = {}
        now = datetime.now(timezone.utc)
        if (
            self.lastBarFetchAt is not None
            and (now - self.lastBarFetchAt).total_seconds() < self.settings.barRefreshIntervalSec
        ):
            return {}
        self.lastBarFetchAt = now

        for symbol in symbols:
            try:
                rows = await self.client.get_recent_candles(
                    symbol,
                    self.settings.strategyTimeframe,
                    self.settings.strategyBarCount,
                )
            except Exception as exc:
                self.lastError = f"MT5 candle fetch failed for {symbol}: {exc}"
                continue

            if not rows:
                continue

            spec = self.marketSpecs.get(symbol)
            point_size = spec.tickSize if spec and spec.tickSize else 0.0
            bars = [
                Bar(
                    time=datetime.fromtimestamp(row["t"] / 1000, tz=timezone.utc),
                    open=row["o"],
                    high=row["h"],
                    low=row["l"],
                    close=row["c"],
                    volume=row["v"],
                    spreadPoints=row.get("spread", 0),
                    spreadPrice=row.get("spread", 0) * point_size,
                )
                for row in rows
            ]
            self.bars[symbol] = bars

            newest = bars[-1].time
            known = self.lastBarTime.get(symbol)
            self.lastBarTime[symbol] = newest

            if known is None:
                # First sight of this symbol, which happens on every restart.
                # Its newest closed bar is "new" to us but not to the market -
                # it may have closed a full timeframe ago, and acting on it
                # submits an order priced off history. Record it and wait for
                # the next genuine close.
                self.skippedStaleBars += 1
                continue

            if newest == known:
                continue

            if self._is_stale(newest):
                self.skippedStaleBars += 1
                self.lastError = (
                    f"{symbol} bar closed {self._age_seconds(newest):.0f}s ago, "
                    "past the freshness window. Signal skipped."
                )
                continue

            updated[symbol] = bars

        return updated

    def _age_seconds(self, bar_time: datetime) -> float:
        """Seconds since the bar CLOSED (MT5 timestamps a bar by its open)."""
        closed_at = bar_time.timestamp() + timeframe_seconds(self.settings.strategyTimeframe)
        return datetime.now(timezone.utc).timestamp() - closed_at

    def _is_stale(self, bar_time: datetime) -> bool:
        budget = timeframe_seconds(self.settings.strategyTimeframe) * self.settings.maxBarAgeFraction
        return self._age_seconds(bar_time) > budget

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
