from __future__ import annotations

import asyncio
import collections
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import Settings
from app.edge.markets import MarketConfig, markets_from_settings
from app.edge.risk import OpenRisk, RiskManager, RiskSettings
from app.edge.signal import Rejection, Signal, evaluate
from app.edge.trend import MIN_BARS, atr, read_trend
from app.mt5.client import Mt5Client, timeframe_seconds
from app.mt5.models import Bar, MarketSpec

TRADE_RETCODE_DONE = 10009

# Enough history for a 200 EMA to converge, plus room for the ATR window.
BARS_NEEDED = MIN_BARS + 120


class EdgeEngine:
    """Trades the one design that beat a coin-flip control out of sample.

    Deliberately idle most of the time. It requires a resolved trend, agreement
    from a higher timeframe, a candlestick trigger pointing the same way, a
    stop wide enough to size against, and a spread small enough relative to
    that stop - and declines whenever any of those is missing.

    The decision logic lives in `signal.evaluate` and `risk.RiskManager`, both
    pure and tested without a broker. This class only handles the parts that
    need MT5: reading bars, placing the order, and trailing the stop.
    """

    def __init__(self, settings: Settings, client: Mt5Client) -> None:
        self.settings = settings
        self.client = client
        self.running = False
        self.paused = False
        self.symbols: list[str] = []
        # Broker symbol -> its own timeframes. Per-market because the measured
        # best differs by instrument: gold 30m, BTC 15m.
        self.markets: dict[str, MarketConfig] = {}
        self.specs: dict[str, MarketSpec] = {}
        self.events: list[str] = []
        self.startingEquity: float | None = None
        self._lastBarTime: dict[str, datetime] = {}
        self._valuePerPoint: dict[str, float] = {}
        # Why each symbol last declined. Logged on CHANGE rather than every
        # bar, so the log stays readable while still explaining silence - a
        # bot idle because nothing qualified must not look like one that died.
        self._lastReason: dict[str, str] = {}
        self._rejects: collections.Counter = collections.Counter()
        self._signalsSeen = 0
        self._lastHeartbeat: datetime | None = None
        # What the bot saw on each market's most recent closed bar. Kept so the
        # dashboard can show WHY it is idle rather than only that it is - the
        # difference between "waiting for the daily trend to agree" and "dead".
        self.observations: dict[str, dict[str, Any]] = {}
        # Signals that fired but were then refused by the risk manager. Worth
        # separating from rejections: a setup blocked by position limits is a
        # very different story from one that never qualified.
        self.refusals: list[dict[str, Any]] = []
        self.risk = RiskManager(
            RiskSettings(
                targetRiskPct=settings.edgeRiskPct,
                maxRiskPct=settings.edgeMaxRiskPct,
                maxOpenPositions=settings.edgeMaxOpenPositions,
                maxPortfolioHeatPct=settings.edgeMaxHeatPct,
                dailyLossHaltPct=settings.edgeDailyLossHaltPct,
                hardStopDrawdownPct=settings.edgeHardStopDrawdownPct,
            ),
            suffix=settings.symbolSuffix,
        )

    # --- plumbing --------------------------------------------------------

    def note(self, message: str) -> None:
        stamped = f"{datetime.now(timezone.utc):%H:%M:%S} {message}"
        self.events.append(stamped)
        # Bounded: this runs for weeks and nobody reads the middle of it.
        if len(self.events) > 300:
            del self.events[:-300]
        print(f"[edge] {stamped}", flush=True)

    async def _resolve_symbols(self) -> None:
        from app.mt5.symbols import SymbolResolver

        wanted = markets_from_settings(self.settings)
        names = await self.client.list_symbol_names()
        resolver = SymbolResolver(names, self.settings.symbolSuffix)

        for market in wanted:
            result = resolver.resolve([market.symbol])
            if not result.brokerSymbols:
                self.note(f"{market.symbol}: not offered by this broker; skipping.")
                continue
            broker = result.brokerSymbols[0]
            await self.client.symbol_select(broker)
            await self.client.wait_for_tick(broker)
            spec = await self.client.symbol_info(broker)
            if spec is None:
                self.note(f"{broker}: no symbol info; skipping.")
                continue
            # Value of a 1.0 price move at 1 lot, from the terminal itself so
            # the broker's own currency conversion is used rather than a
            # reimplementation of it.
            value = await self.client.calc_profit("buy", broker, 1.0, 100.0, 101.0)
            if not value or value <= 0:
                self.note(f"{broker}: cannot value a price move; skipping.")
                continue
            self.symbols.append(broker)
            self.markets[broker] = MarketConfig(
                symbol=broker,
                timeframe=market.timeframe,
                higherTimeframe=market.higherTimeframe,
            )
            self.specs[broker] = spec
            self._valuePerPoint[broker] = value

        if self.markets:
            self.note("Trading " + "; ".join(m.describe() for m in self.markets.values()))
        else:
            self.note("No tradeable markets resolved.")

    async def _bars(self, symbol: str, interval: str, count: int) -> list[Bar]:
        rows = await self.client.get_recent_candles(symbol, interval, count)
        spec = self.specs.get(symbol)
        tick = spec.tickSize if spec else 0.0
        return [
            Bar(
                time=datetime.fromtimestamp(r["t"] / 1000, tz=timezone.utc),
                open=r["o"], high=r["h"], low=r["l"], close=r["c"],
                volume=r.get("v", 0.0), spreadPoints=r.get("spread", 0),
                spreadPrice=r.get("spread", 0) * tick,
            )
            for r in rows
        ]

    # --- weekend ---------------------------------------------------------

    def _weekend_imminent(self, now: datetime | None = None) -> bool:
        """True inside the flat window before the weekly close.

        Index CFDs close and reopen, and a stop does NOT protect against a gap
        through it - you can lose several times the intended risk on a price
        that never traded in between. Being flat over the break is the only
        real defence, and it costs little because the edge is measured in
        trades, not in hours held.
        """
        if not self.settings.edgeFlatBeforeWeekend:
            return False
        now = now or datetime.now(timezone.utc)
        # Friday, within N hours of the 21:00 UTC close.
        if now.weekday() != 4:
            return False
        close = now.replace(hour=21, minute=0, second=0, microsecond=0)
        return now >= close - timedelta(hours=self.settings.edgeWeekendFlatHours)

    # --- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        self.running = True
        await self._resolve_symbols()
        account = await self.client.account_info()
        self.startingEquity = float(getattr(account, "equity", 0.0) or 0.0)
        self.note(f"Started. Starting equity ${self.startingEquity:.2f}, "
                  f"risk {self.settings.edgeRiskPct:.2f}% per trade.")

        # Paced off the fastest market, so a 15m instrument is not sampled
        # at a 30m instrument's cadence and misses its closes.
        interval = min(
            (timeframe_seconds(m.timeframe) for m in self.markets.values()),
            default=timeframe_seconds(self.settings.edgeTimeframe),
        )
        while self.running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:            # one bad cycle must not end the bot
                self.note(f"Cycle failed: {exc}")
            # Poll well inside the bar so a close is never missed, but not so
            # often that the terminal is hammered for no reason.
            await asyncio.sleep(min(self.settings.edgePollSec, max(5, interval / 10)))

    def _heartbeat(self, now: datetime | None = None) -> None:
        """Periodic proof of life, with what the bot is waiting for.

        Without this, a correctly idle bot and a wedged one produce the same
        output: nothing. The summary names the dominant rejection so a long
        quiet stretch can be read at a glance rather than reconstructed.
        """
        now = now or datetime.now(timezone.utc)
        every = max(300.0, self.settings.edgeHeartbeatSec)
        if self._lastHeartbeat is not None and (now - self._lastHeartbeat).total_seconds() < every:
            return
        first = self._lastHeartbeat is None
        self._lastHeartbeat = now
        if first:
            return
        top = self._rejects.most_common(1)
        waiting = f' mostly "{top[0][0]}"' if top else ""
        self.note(
            f"alive: {self._signalsSeen} signal(s), "
            f"{sum(self._rejects.values())} declined since start,{waiting}"
        )

    async def stop(self) -> None:
        self.running = False

    async def _tick(self) -> None:
        # Trailing runs even when paused: pausing stops NEW positions, it does
        # not mean abandoning management of live risk.
        await self._trail_open_positions()
        self._heartbeat()

        if self.paused or not self.symbols:
            return

        weekend = self._weekend_imminent()
        if weekend:
            await self._flatten_for_weekend()

        positions = await self.client.positions_get()
        mine = [p for p in positions if p.magic == self.settings.edgeMagicNumber]
        for symbol in self.symbols:
            # A 24/7 market keeps trading through the weekend window; only the
            # instruments that actually close are stood down.
            if weekend and self._closes_for_the_weekend(symbol):
                continue
            try:
                await self._consider(symbol, mine)
            except Exception as exc:
                self.note(f"{symbol}: {exc}")

    # --- the decision ----------------------------------------------------

    async def _consider(self, symbol: str, mine: list[Any]) -> None:
        market = self.markets[symbol]
        bars = await self._bars(symbol, market.timeframe, BARS_NEEDED)
        if len(bars) < MIN_BARS:
            return

        # Evaluate once per closed bar. Without this the same setup is acted on
        # every poll for the whole bar, which is how one signal becomes twenty
        # positions.
        last_time = bars[-1].time
        if self._lastBarTime.get(symbol) == last_time:
            return
        self._lastBarTime[symbol] = last_time

        higher: list[Bar] | None = None
        if market.higherTimeframe:
            higher = await self._bars(symbol, market.higherTimeframe, BARS_NEEDED)

        outcome = evaluate(
            symbol, bars, higher,
            require_anchor=self.settings.edgeRequireAnchor,
            stop_buffer_atr=self.settings.edgeStopBufferAtr,
            max_spread_fraction_of_risk=self.settings.edgeMaxSpreadFraction,
        )
        self._record_observation(symbol, market, bars, higher, outcome)

        if isinstance(outcome, Rejection):
            self._rejects[outcome.reason] += 1
            if self._lastReason.get(symbol) != outcome.reason:
                self._lastReason[symbol] = outcome.reason
                self.note(f"{symbol}: {outcome.reason}")
            return

        self._signalsSeen += 1
        self._lastReason.pop(symbol, None)
        self.note(f"{symbol}: SIGNAL {outcome.direction} - {outcome.reason}")
        await self._open(outcome, mine)

    def _record_observation(self, symbol, market, bars, higher, outcome) -> None:
        """Snapshot what the bot saw, for the dashboard.

        The trends are re-read rather than threaded out of `evaluate`, which
        keeps the decision path a pure function with one return value. It costs
        one extra EMA pass per closed bar, which at a 15-minute cadence is
        nothing.
        """
        a = atr(bars)
        execution = read_trend(bars, atr=a, require_anchor=self.settings.edgeRequireAnchor)
        higher_view = None
        if higher:
            higher_view = read_trend(
                higher, atr=atr(higher), require_anchor=self.settings.edgeRequireAnchor
            )

        bar = bars[-1]
        risk = abs(outcome.entry - outcome.stop) if isinstance(outcome, Signal) else 0.0
        self.observations[symbol] = {
            "symbol": symbol,
            "timeframe": market.timeframe,
            "higherTimeframe": market.higherTimeframe,
            "trend": execution.direction,
            "higherTrend": higher_view.direction if higher_view else None,
            "price": bar.close,
            "atr": a,
            "spreadPrice": bar.spreadPrice,
            "spreadFractionOfAtr": (bar.spreadPrice / a) if a > 0 else None,
            "barClosedAt": bar.time,
            "observedAt": datetime.now(timezone.utc),
            "status": "signal" if isinstance(outcome, Signal) else "waiting",
            "reason": outcome.reason,
            "pattern": outcome.pattern if isinstance(outcome, Signal) else None,
            "direction": outcome.direction if isinstance(outcome, Signal) else None,
            "entry": outcome.entry if isinstance(outcome, Signal) else None,
            "stop": outcome.stop if isinstance(outcome, Signal) else None,
            "riskAtr": (risk / a) if isinstance(outcome, Signal) and a > 0 else None,
        }

    def _note_refusal(self, sig: Signal, reason: str) -> None:
        self.refusals.append({
            "at": datetime.now(timezone.utc),
            "symbol": sig.symbol,
            "direction": sig.direction,
            "pattern": sig.pattern,
            "entry": sig.entry,
            "stop": sig.stop,
            "reason": reason,
        })
        del self.refusals[:-25]

    async def _open(self, sig: Signal, mine: list[Any]) -> None:
        spec = self.specs[sig.symbol]
        account = await self.client.account_info()
        equity = float(getattr(account, "equity", 0.0) or 0.0)

        open_risk = [
            OpenRisk(
                symbol=p.symbol,
                direction="buy" if p.type == 0 else "sell",
                riskCash=abs(p.price_open - p.sl) * p.volume
                * self._valuePerPoint.get(p.symbol, 0.0) if p.sl else 0.0,
            )
            for p in mine
        ]

        decision = self.risk.evaluate(
            symbol=sig.symbol,
            direction=sig.direction,
            entry=sig.entry,
            stop=sig.stop,
            spec=spec,
            valuePerPricePoint=self._valuePerPoint[sig.symbol],
            equity=equity,
            startingEquity=self.startingEquity or equity,
            openPositions=open_risk,
            realisedToday=await self._realised_today(),
        )
        if not decision.allowed:
            self._note_refusal(sig, decision.reason)
            self.note(f"{sig.symbol}: REFUSED - {decision.reason}")
            return

        # A far backstop rather than a real target: the trail is the exit, and
        # capping the winner is exactly what the trail exists to avoid. It
        # still bounds the position broker-side if this process dies.
        reach = sig.risk * self.settings.edgeBackstopR
        backstop = round(
            sig.entry + reach if sig.direction == "buy" else sig.entry - reach,
            spec.digits,
        )

        order = {
            "symbol": sig.symbol,
            "volume": decision.lots,
            "side": sig.direction,
            "sl": round(sig.stop, spec.digits),
            "tp": backstop,
            "deviation": self.settings.mt5DeviationPoints,
            "magic": self.settings.edgeMagicNumber,
            "comment": "vtfx-edge",
        }
        result = await self.client.send_market_order(order)
        if result.get("retcode") != TRADE_RETCODE_DONE:
            broker_reason = (
                f"broker rejected the order: retcode={result.get('retcode')} "
                f"{result.get('comment') or result.get('error')}"
            )
            self._note_refusal(sig, broker_reason)
            self.note(f"{sig.symbol}: {broker_reason}")
            return

        self.note(
            f"{sig.symbol} {sig.direction} {decision.lots:g} @ {result.get('price')} "
            f"SL {order['sl']} | {sig.reason} | {decision.reason}"
        )

    async def _realised_today(self) -> float:
        now = datetime.now(timezone.utc)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        deals = await self.client.history_deals_range(start, now)
        total = 0.0
        for d in deals:
            magic = d.get("magic") if isinstance(d, dict) else getattr(d, "magic", 0)
            if magic != self.settings.edgeMagicNumber:
                continue
            profit = d.get("profit") if isinstance(d, dict) else getattr(d, "profit", 0.0)
            total += float(profit or 0.0)
        return total

    # --- management ------------------------------------------------------

    async def _trail_open_positions(self) -> None:
        """Ratchet the stop once a position is far enough ahead.

        Arming at 1.0R rather than earlier is the sweep's answer, and the path
        study says why: arming at 0.25R protects a profit that would have grown
        anyway 95% of the time, paying for that insurance with the entire
        upside of every trade.
        """
        positions = await self.client.positions_get()
        mine = [p for p in positions if p.magic == self.settings.edgeMagicNumber]
        if not mine:
            return

        for p in mine:
            spec = self.specs.get(p.symbol)
            if spec is None or not p.sl:
                continue
            market = self.markets.get(p.symbol)
            if market is None:
                continue
            bars = await self._bars(p.symbol, market.timeframe, 100)
            if len(bars) < 20:
                continue
            a = atr(bars)
            if a <= 0:
                continue

            long_side = p.type == 0
            risk = abs(p.price_open - p.sl)
            if risk <= 0:
                continue
            gain = (p.price_current - p.price_open) if long_side else (p.price_open - p.price_current)
            if gain < risk * self.settings.edgeTrailActivateR:
                continue

            trail = self.settings.edgeTrailAtrMultiple * a
            proposed = (
                p.price_current - trail if long_side else p.price_current + trail
            )
            proposed = round(proposed, spec.digits)

            # Ratchet only. A stop that can move backwards is not a stop.
            if long_side and proposed <= p.sl:
                continue
            if not long_side and proposed >= p.sl:
                continue

            res = await self.client.modify_position_stops(
                ticket=p.ticket, symbol=p.symbol,
                stop_loss=proposed, take_profit=p.tp or None,
            )
            if res.get("retcode") == TRADE_RETCODE_DONE:
                self.note(f"{p.symbol} #{p.ticket}: stop {p.sl} -> {proposed}")

    def _closes_for_the_weekend(self, symbol: str) -> bool:
        """Does this instrument stop trading over the weekend?

        Crypto does not. Flattening it on a Friday would close good positions
        for a gap that never comes, and forfeit the whole weekend - which for
        BTC is a meaningful share of its trading time, not an edge case.

        Decided from the correlation bucket rather than a second list, so
        adding an instrument to a bucket carries this behaviour with it.
        """
        return self.risk.bucket_for(symbol) != "crypto"

    async def _flatten_for_weekend(self) -> None:
        positions = await self.client.positions_get()
        mine = [
            p for p in positions
            if p.magic == self.settings.edgeMagicNumber
            and self._closes_for_the_weekend(p.symbol)
        ]
        if not mine:
            return
        self.note(f"Weekend close approaching; flattening {len(mine)} position(s). "
                  f"A stop does not protect against a gap.")
        for p in mine:
            order = {
                "symbol": p.symbol,
                "volume": p.volume,
                "side": "sell" if p.type == 0 else "buy",
                "position": p.ticket,
                "deviation": self.settings.mt5DeviationPoints,
                "magic": self.settings.edgeMagicNumber,
                "comment": "vtfx-edge-weekend",
            }
            result = await self.client.send_market_order(order)
            if result.get("retcode") != TRADE_RETCODE_DONE:
                self.note(f"{p.symbol} #{p.ticket}: weekend close failed "
                          f"{result.get('retcode')} {result.get('comment')}")
