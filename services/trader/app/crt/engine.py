from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.config import Settings
from app.crt.detector import CrtSetup, aggregate, detect
from app.mt5.client import TRADE_RETCODE_DONE, Mt5Client, timeframe_seconds
from app.mt5.models import Bar, MarketSpec
from app.risk.sizing import PositionSizer
from app.risk.trailing import TrailingStop


@dataclass(slots=True)
class TrackedTrade:
    ticket: int
    symbol: str
    side: str
    entryPrice: float
    initialStop: float
    openedAt: datetime
    bestPrice: float


@dataclass(slots=True)
class CrtStats:
    signals: int = 0
    submitted: int = 0
    rejected: int = 0
    trailUpdates: int = 0
    lastSignalAt: datetime | None = None
    events: list[str] = field(default_factory=list)


class CrtEngine:
    """Candle Range Theory as a directional bias, exited by a trailing stop.

    A sweep of the previous candle's range sets the direction. The stop starts
    beyond the swept wick - the level the setup claims was rejected, so price
    returning there invalidates it - and then trails, rather than aiming at a
    fixed target.

    Evaluated on CLOSED candles only. The sweep is defined by where the candle
    closed relative to the range, so a forming candle can look like a sweep and
    stop being one before it closes.
    """

    def __init__(self, settings: Settings, client: Mt5Client) -> None:
        self.settings = settings
        self.client = client
        self.stats = CrtStats()
        self.sizer = PositionSizer(client)
        self.trailing = TrailingStop(
            activate_r=settings.crtTrailActivateR,
            trail_atr_multiple=settings.crtTrailAtrMultiple,
        )
        self.symbols: list[str] = []
        self.specs: dict[str, MarketSpec] = {}
        self.tracked: dict[int, TrackedTrade] = {}
        self._lastCandleTime: dict[str, datetime] = {}
        self.running = False

    def note(self, message: str) -> None:
        stamped = f"{datetime.now(timezone.utc):%H:%M:%S} [crt] {message}"
        self.stats.events.append(stamped)
        del self.stats.events[:-40]
        print(stamped, flush=True)

    async def start(self, symbols: list[str], specs: dict[str, MarketSpec]) -> None:
        self.symbols = symbols
        self.specs = specs
        self.running = True
        tf = self.settings.crtExecutionTimeframe
        self.note(
            f"started on {', '.join(symbols)} | {tf} bars aggregated x"
            f"{self.settings.crtRangeFactor} for the range candle | "
            f"max {self.settings.crtMaxOpenPositions} positions"
        )
        while self.running:
            try:
                await self._tick()
            except Exception as exc:
                self.note(f"tick failed: {exc}")
            await asyncio.sleep(self.settings.crtPollSec)

    async def stop(self) -> None:
        self.running = False

    async def _tick(self) -> None:
        await self._reconcile()
        for symbol in self.symbols:
            await self._scan(symbol)
        await self._trail()

    async def _open_tickets(self) -> set[int]:
        positions = await self.client.positions_get()
        return {
            p.ticket for p in positions if p.magic == self.settings.crtMagicNumber
        }

    async def _reconcile(self) -> None:
        live = await self._open_tickets()
        for ticket in list(self.tracked):
            if ticket not in live:
                del self.tracked[ticket]

    async def _scan(self, symbol: str) -> None:
        if len(self.tracked) >= self.settings.crtMaxOpenPositions:
            return
        spec = self.specs.get(symbol)
        if spec is None:
            return

        factor = self.settings.crtRangeFactor
        need = (factor * 3) + factor
        rows = await self.client.get_recent_candles(
            symbol, self.settings.crtExecutionTimeframe, max(need, 60)
        )
        if len(rows) < need:
            return

        bars = [
            Bar(
                time=datetime.fromtimestamp(r["t"] / 1000, tz=timezone.utc),
                open=r["o"], high=r["h"], low=r["l"], close=r["c"],
                volume=r["v"], spreadPoints=r.get("spread", 0),
                spreadPrice=r.get("spread", 0) * spec.tickSize,
            )
            for r in rows
        ]
        candles = aggregate(bars, factor)
        if len(candles) < 3:
            return

        c1, c2 = candles[-2], candles[-1]

        # Act once per completed range candle.
        if self._lastCandleTime.get(symbol) == c2.time:
            return

        atr = self._atr(candles)
        setup = detect(c1, c2, self.settings.crtMinSweepAtr, atr)
        if setup is None:
            return
        self._lastCandleTime[symbol] = c2.time

        # A candle that closed too long ago is history, not a signal: price has
        # moved on while the levels have not.
        age = (datetime.now(timezone.utc) - c2.time).total_seconds()
        candle_seconds = timeframe_seconds(self.settings.crtExecutionTimeframe) * factor
        if age > candle_seconds * (1 + self.settings.maxBarAgeFraction):
            self.note(f"{symbol} sweep is {age:.0f}s stale, skipping")
            return

        self.stats.signals += 1
        self.stats.lastSignalAt = datetime.now(timezone.utc)
        await self._enter(symbol, spec, setup, atr)

    async def _enter(
        self, symbol: str, spec: MarketSpec, setup: CrtSetup, atr: float
    ) -> None:
        quote = await self.client.symbol_info_tick(symbol)
        if quote is None or not quote.bidPrice or not quote.askPrice:
            return

        side = "buy" if setup.bullish else "sell"
        entry = quote.askPrice if side == "buy" else quote.bidPrice

        # Stop beyond the swept wick plus a buffer, so a retest of the level
        # itself does not close the trade.
        buffer = atr * self.settings.crtStopBufferAtr
        stop = setup.sweepLow - buffer if setup.bullish else setup.sweepHigh + buffer
        risk = abs(entry - stop)
        min_risk = max(atr * 0.3, (quote.askPrice - quote.bidPrice) * 3)
        if risk < min_risk:
            self.note(f"{symbol} stop is {risk:.5f}, inside the noise floor; skipped")
            return

        account = await self.client.account_info()
        if account is None:
            return
        budget = float(account.equity) * (self.settings.crtRiskPerTradePct / 100)
        sizing = await self.sizer.size_for_risk(
            symbol=symbol, side="long" if side == "buy" else "short",
            entry_price=entry, stop_loss=stop, risk_amount=budget, spec=spec,
        )
        if not sizing.ok:
            self.note(f"{symbol} not sized: {sizing.reason}")
            return

        order = {
            "symbol": symbol,
            "volume": sizing.lots,
            "side": side,
            "sl": round(stop, spec.digits),
            # No fixed target: the trailing stop is the exit. A far backstop
            # keeps the position bounded broker-side if this process dies.
            "tp": round(
                entry + risk * 20 if side == "buy" else entry - risk * 20, spec.digits
            ),
            "deviation": self.settings.mt5DeviationPoints,
            "magic": self.settings.crtMagicNumber,
            "comment": "vtfx-crt",
        }
        result = await self.client.send_market_order(order)
        if result.get("retcode") != TRADE_RETCODE_DONE:
            self.stats.rejected += 1
            self.note(
                f"{symbol} rejected: {result.get('retcode')} "
                f"{result.get('comment') or result.get('error')}"
            )
            return

        ticket = int(result.get("order") or 0)
        fill = float(result.get("price") or entry)
        self.stats.submitted += 1
        self.tracked[ticket] = TrackedTrade(
            ticket=ticket, symbol=symbol, side="long" if side == "buy" else "short",
            entryPrice=fill, initialStop=stop,
            openedAt=datetime.now(timezone.utc), bestPrice=fill,
        )
        self.note(
            f"{symbol} {setup.bias} {sizing.lots} lots @ {fill} stop {stop:.5f} "
            f"risk ${sizing.riskAmountUsd:.2f} | {setup.reason}"
        )

    async def _trail(self) -> None:
        if not self.tracked:
            return
        positions = {
            p.ticket: p
            for p in await self.client.positions_get()
            if p.magic == self.settings.crtMagicNumber
        }
        for ticket, trade in list(self.tracked.items()):
            position = positions.get(ticket)
            if position is None:
                continue
            spec = self.specs.get(trade.symbol)
            if spec is None:
                continue
            quote = await self.client.symbol_info_tick(trade.symbol)
            if quote is None:
                continue

            mark = quote.bidPrice if trade.side == "long" else quote.askPrice
            if mark is None:
                continue
            trade.bestPrice = (
                max(trade.bestPrice, mark) if trade.side == "long"
                else min(trade.bestPrice, mark)
            )

            rows = await self.client.get_recent_candles(
                trade.symbol, self.settings.crtExecutionTimeframe, 40
            )
            if len(rows) < 20:
                continue
            atr = self._atr_from_rows(rows)
            decision = self.trailing.evaluate(
                side=trade.side,
                entry_price=trade.entryPrice,
                initial_stop=trade.initialStop,
                current_stop=position.sl or trade.initialStop,
                best_price=trade.bestPrice,
                atr=atr,
            )
            if not decision.shouldMove:
                continue
            new_stop = round(decision.stopLoss, spec.digits)
            if new_stop == position.sl:
                continue
            result = await self.client.modify_position_stops(
                ticket=ticket, symbol=trade.symbol,
                stop_loss=new_stop, take_profit=position.tp,
            )
            if result.get("retcode") == TRADE_RETCODE_DONE:
                self.stats.trailUpdates += 1
                self.note(f"{trade.symbol} stop trailed {position.sl} -> {new_stop}")

    def _atr(self, candles: list[Bar], window: int = 14) -> float:
        if len(candles) < 2:
            return 0.0
        window = min(window, len(candles) - 1)
        trs = []
        for i in range(len(candles) - window, len(candles)):
            b, pc = candles[i], candles[i - 1].close
            trs.append(max(b.high - b.low, abs(b.high - pc), abs(b.low - pc)))
        return sum(trs) / len(trs) if trs else 0.0

    def _atr_from_rows(self, rows, window: int = 14) -> float:
        if len(rows) < 2:
            return 0.0
        window = min(window, len(rows) - 1)
        trs = []
        for i in range(len(rows) - window, len(rows)):
            r, pc = rows[i], rows[i - 1]["c"]
            trs.append(max(r["h"] - r["l"], abs(r["h"] - pc), abs(r["l"] - pc)))
        return sum(trs) / len(trs) if trs else 0.0
