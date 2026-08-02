from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.config import Settings
from app.mt5.client import TRADE_RETCODE_DONE, Mt5Client
from app.mt5.models import MarketSpec
from app.scalper.levels import (
    break_even_win_rate,
    compute_levels,
    spread_cost_ratio,
    value_per_point,
)


@dataclass(slots=True)
class ScalperStats:
    trades: int = 0
    wins: int = 0
    losses: int = 0
    realisedUsd: float = 0.0
    consecutiveLosses: int = 0
    startingEquityUsd: float = 0.0
    startedAt: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def winRate(self) -> float:
        return (self.wins / self.trades * 100) if self.trades else 0.0


class ScalperEngine:
    """Opens a position, takes a fixed cash profit, closes, repeats.

    Target and stop are placed as broker-side levels on the position, so the
    exit happens at MetaTrader even if this process dies. The loop only has to
    notice the position is gone and open the next one.

    The payoff is deliberately lopsided: a small target against a stop many
    times its size. That produces a high win rate and a rare large loss, so
    judge it on realised P/L, never on the win rate - see
    `break_even_win_rate`, which for the default settings is about 97%.
    """

    def __init__(self, settings: Settings, client: Mt5Client) -> None:
        self.settings = settings
        self.client = client
        self.stats = ScalperStats()
        self.symbols: list[str] = []
        self.specs: dict[str, MarketSpec] = {}
        self._nextSymbol = 0
        self.running = False
        self.paused = False
        self.haltReason: str | None = None
        self._nextSide = settings.scalperSide if settings.scalperSide in ("buy", "sell") else "buy"
        self._log: list[str] = []

    def note(self, message: str) -> None:
        stamped = f"{datetime.now(timezone.utc):%H:%M:%S}  {message}"
        self._log.append(stamped)
        print(stamped, flush=True)

    # --- safety ----------------------------------------------------------

    async def _halt_check(self) -> str | None:
        """Reasons to stop trading entirely.

        A strategy shaped like this loses in rare, large steps, so the limits
        below are the only thing standing between a bad hour and an empty
        account. They are checked before every entry, not periodically.
        """
        account = await self.client.account_info()
        if account is None:
            return "Lost the MT5 account connection."

        equity = float(account.equity)
        floor = self.settings.scalperEquityFloorUsd
        if floor > 0 and equity <= floor:
            return f"Equity {equity:.2f} hit the floor of {floor:.2f}."

        drawdown = self.stats.startingEquityUsd - equity
        max_dd = self.settings.scalperMaxDrawdownUsd
        if max_dd > 0 and drawdown >= max_dd:
            return f"Session drawdown {drawdown:.2f} reached the {max_dd:.2f} limit."

        max_streak = self.settings.scalperMaxConsecutiveLosses
        if max_streak > 0 and self.stats.consecutiveLosses >= max_streak:
            return f"{self.stats.consecutiveLosses} consecutive losses reached the limit."

        max_trades = self.settings.scalperMaxTrades
        if max_trades > 0 and self.stats.trades >= max_trades:
            return f"Reached the {max_trades} trade limit for this session."
        return None

    # --- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        if not await self.client.connect():
            raise RuntimeError(f"MT5 connect failed: {self.client.lastError}")

        names = await self.client.list_symbol_names()
        from app.mt5.symbols import SymbolResolver

        # scalperSymbols wins; scalperSymbol is kept so an older .env still works.
        wanted = self.settings.scalperSymbols or [self.settings.scalperSymbol]
        resolution = SymbolResolver(names, self.settings.symbolSuffix).resolve(wanted)
        if not resolution.resolved:
            raise RuntimeError(f"No broker symbol matched any of {wanted}.")

        account = await self.client.account_info()
        self.stats.startingEquityUsd = float(account.equity)

        self.note(f"Scalper starting: target {self.settings.scalperTargetUsd:.2f} "
                  f"stop {self.settings.scalperStopUsd:.2f}")
        if self.settings.scalperTrailingEnabled:
            self.note(f"  EXIT: trailing, arms at {self.settings.scalperTrailActivateUsd:+.2f} "
                      f"then follows {self.settings.scalperTrailAtrMultiple} ATR")
        else:
            self.note(f"  EXIT: fixed target at {self.settings.scalperTargetUsd:+.2f}")

        for broker_name in resolution.brokerSymbols:
            if not await self.client.symbol_select(broker_name):
                self.note(f"  {broker_name}: could not be selected, skipping")
                continue
            if not await self.client.wait_for_tick(broker_name):
                self.note(f"  {broker_name}: no tick, skipping")
                continue
            spec = await self.client.symbol_info(broker_name)
            if spec is None:
                self.note(f"  {broker_name}: no spec, skipping")
                continue

            quote = await self.client.symbol_info_tick(broker_name)
            lots = spec.volumeMin if self.settings.scalperLots <= 0 else self.settings.scalperLots
            per_point = value_per_point(spec, lots)
            if per_point <= 0:
                self.note(f"  {broker_name}: cannot price a cash target at {lots} lots, skipping")
                continue

            spread_points = round((quote.askPrice - quote.bidPrice) / spec.tickSize)
            spread_cost = spread_points * per_point
            ratio = spread_cost_ratio(spec, lots, self.settings.scalperTargetUsd, spread_points)
            breakeven = break_even_win_rate(
                self.settings.scalperTargetUsd, self.settings.scalperStopUsd, spread_cost
            )
            cost_r = spread_cost / max(self.settings.scalperStopUsd, 1e-9)

            self.specs[broker_name] = spec
            self.symbols.append(broker_name)
            flag = "  <-- expensive" if cost_r > 0.04 else ""
            self.note(
                f"  {broker_name:10s} {lots} lots  spread {spread_points}pts="
                f"{spread_cost:.4f} ({ratio*100:.0f}% of target, {cost_r:.3f}R)  "
                f"breakeven {breakeven*100:.1f}%{flag}"
            )

        if not self.symbols:
            raise RuntimeError("No scalper symbol could be priced; nothing to trade.")

        self.note(f"  trading {len(self.symbols)} symbol(s), "
                  f"max {self.settings.scalperMaxOpenPositions} open in total")
        self.note(f"  equity {self.stats.startingEquityUsd:.2f}, "
                  f"floor {self.settings.scalperEquityFloorUsd:.2f}")

        self.running = True
        await self._loop()

    async def _loop(self) -> None:
        """Hold up to `scalperMaxOpenPositions` at once.

        Each pass reconciles what the broker still has open against what we
        opened, banks anything that closed, then tops back up. Positions carry
        their own target and stop broker-side, so nothing here has to be
        waiting when one fills.
        """
        # Adopt anything already open under our magic, so a restart does not
        # lose track of live positions and immediately over-open.
        open_tickets: set[int] = {
            p.ticket
            for p in await self.client.positions_get()
            if p.magic == self.settings.scalperMagicNumber
        }
        if open_tickets:
            self.note(f"Adopted {len(open_tickets)} position(s) already open.")

        while self.running:
            halt = await self._halt_check()
            if halt:
                self.haltReason = halt
                self.note(f"HALTED: {halt}  ({len(open_tickets)} still open, "
                          "their broker-side stops remain in place)")
                self.running = False
                break

            live = {
                p.ticket
                for p in await self.client.positions_get()
                if p.magic == self.settings.scalperMagicNumber
            }
            for ticket in open_tickets - live:
                await self._record_close(ticket)
            open_tickets = live

            await self._trail_open_positions()

            # Paused stops NEW positions only. Open ones keep their
            # broker-side stops and are still trailed and banked - abandoning
            # live risk is not what "pause" should mean.
            while (self.running and not self.paused
                   and len(open_tickets) < self.settings.scalperMaxOpenPositions):
                ticket = await self._open_position()
                if ticket is None:
                    break
                open_tickets.add(ticket)

            await asyncio.sleep(self.settings.scalperPollSec)
        self._summarise()

    # --- trading ---------------------------------------------------------

    async def _open_position(self):
        # Round-robin so one symbol cannot monopolise every slot. Slots are a
        # single pool across symbols, matching the "5 per bot" limit.
        symbol = self.symbols[self._nextSymbol % len(self.symbols)]
        self._nextSymbol += 1
        spec = self.specs[symbol]

        quote = await self.client.symbol_info_tick(symbol)
        if quote is None or not quote.bidPrice or not quote.askPrice:
            self.note("No quote; waiting.")
            return None

        side = self._nextSide
        levels = compute_levels(
            side=side,
            bid=quote.bidPrice,
            ask=quote.askPrice,
            spec=spec,
            lots=self._lots(symbol),
            target_usd=self.settings.scalperTargetUsd,
            stop_usd=self.settings.scalperStopUsd,
        )
        if not levels.ok:
            # One symbol failing to price must not stop the bot: the others
            # are still tradable, and conditions change.
            self.note(f"{symbol}: {levels.reason}")
            return None

        # With trailing on, the fixed target is replaced by a far backstop:
        # capping the winner is the exact thing the trail exists to avoid. The
        # backstop still bounds the position broker-side if this process dies.
        take_profit = levels.takeProfit
        if self.settings.scalperTrailingEnabled:
            reach = abs(levels.takeProfit - levels.entryPrice) * 25
            take_profit = round(
                levels.entryPrice + reach if side == "buy" else levels.entryPrice - reach,
                spec.digits,
            )

        order = {
            "symbol": symbol,
            "volume": levels.lots,
            "side": side,
            "sl": levels.stopLoss,
            "tp": take_profit,
            "deviation": self.settings.mt5DeviationPoints,
            # A distinct magic number keeps these trades separable from the
            # main bot's, which filters its account view by its own magic.
            "magic": self.settings.scalperMagicNumber,
            "comment": "vtfx-scalp",
        }
        result = await self.client.send_market_order(order)
        if result.get("retcode") != TRADE_RETCODE_DONE:
            self.note(f"Order rejected: retcode={result.get('retcode')} "
                      f"{result.get('comment') or result.get('error')}")
            await asyncio.sleep(self.settings.scalperPollSec)
            return None

        ticket = result.get("order")
        self.note(f"#{self.stats.trades + 1} {symbol} {side} {levels.lots} @ "
                  f"{result.get('price')} SL {levels.stopLoss} ({levels.reason})")
        if self.settings.scalperSide == "alternate":
            self._nextSide = "sell" if side == "buy" else "buy"
        return ticket

    async def _trail_open_positions(self) -> None:
        """Ratchet stops behind price once a position is far enough ahead.

        Arming is measured in ACCOUNT CURRENCY, not price distance, because
        that is the unit this bot works in: the trail engages at the profit the
        fixed target used to take, so the trade is never worse than the target
        it replaced - it simply is not closed there.

        The trail distance is ATR-based rather than a fixed cash amount, so it
        widens when BTC is moving and tightens when it is not. A fixed cash
        trail would be stopped out constantly during normal volatility.
        """
        if not self.settings.scalperTrailingEnabled or not self.specs:
            return

        positions = [
            p for p in await self.client.positions_get()
            if p.magic == self.settings.scalperMagicNumber
        ]
        if not positions:
            return

        # ATR is per symbol, so cache one fetch per symbol per pass rather
        # than one per position.
        atr_by_symbol: dict[str, float] = {}

        for p in positions:
            spec = self.specs.get(p.symbol)
            if spec is None:
                continue
            if p.symbol not in atr_by_symbol:
                atr_by_symbol[p.symbol] = await self._recent_atr(p.symbol)
            atr = atr_by_symbol[p.symbol]
            if atr <= 0:
                continue
            offset = atr * self.settings.scalperTrailAtrMultiple
            side = "long" if p.type == 0 else "short"
            # profit at the CURRENT price is what MT5 already reports, so use
            # it rather than recomputing a per-point value.
            if p.profit < self.settings.scalperTrailActivateUsd:
                continue

            candidate = (p.price_current - offset) if side == "long" else (p.price_current + offset)
            candidate = round(candidate, spec.digits)

            # Ratchet only. A stop that can loosen turns a bounded loss into an
            # unbounded one.
            current = p.sl or (0.0 if side == "long" else float("inf"))
            improved = candidate > current if side == "long" else candidate < current
            if not improved:
                continue

            result = await self.client.modify_position_stops(
                ticket=p.ticket, symbol=p.symbol,
                stop_loss=candidate, take_profit=p.tp,
            )
            if result.get("retcode") == TRADE_RETCODE_DONE:
                self.stats.trailUpdates += 1
                self.note(f"  trailed #{p.ticket} stop {p.sl} -> {candidate} "
                          f"(locking {p.profit:+.2f})")

    async def _recent_atr(self, symbol: str, window: int = 14) -> float:
        rows = await self.client.get_recent_candles(symbol, "1m", window + 5)
        if len(rows) < window + 1:
            return 0.0
        trs = []
        for i in range(len(rows) - window, len(rows)):
            r, pc = rows[i], rows[i - 1]["c"]
            trs.append(max(r["h"] - r["l"], abs(r["h"] - pc), abs(r["l"] - pc)))
        return sum(trs) / len(trs) if trs else 0.0

    async def _record_close(self, ticket: int) -> None:
        """Bank a position the broker has closed.

        Realised P/L comes from the exit deals rather than being assumed to be
        the target or the stop: slippage, swap and commission all land here,
        and taking the configured figure on trust would quietly overstate
        results on exactly the trades that matter.
        """
        deals = await self.client.history_deals_for_position(ticket)
        exits = [d for d in deals if self.client.deal_entry_is_exit(d.entry)]
        if not exits:
            self.note(f"  #{ticket} vanished with no exit deal; not counted.")
            return

        realised = sum(d.profit + d.swap + d.commission for d in exits)
        reason = self.client.deal_reason_label(exits[-1].reason)

        self.stats.trades += 1
        self.stats.realisedUsd += realised
        if realised > 0:
            self.stats.wins += 1
            self.stats.consecutiveLosses = 0
        else:
            self.stats.losses += 1
            self.stats.consecutiveLosses += 1

        self.note(
            f"  closed #{ticket} {realised:+.2f} ({reason})  |  "
            f"{self.stats.wins}W/{self.stats.losses}L ({self.stats.winRate:.0f}%)  "
            f"net {self.stats.realisedUsd:+.2f}"
        )

    def _lots(self, symbol: str) -> float:
        configured = self.settings.scalperLots
        if configured > 0:
            return configured
        spec = self.specs.get(symbol)
        return spec.volumeMin if spec else 0.01

    def _summarise(self) -> None:
        s = self.stats
        ran = (datetime.now(timezone.utc) - s.startedAt).total_seconds() / 60
        self.note("")
        self.note(f"=== {s.trades} trades over {ran:.0f} min ===")
        self.note(f"  {s.wins}W / {s.losses}L  win rate {s.winRate:.1f}%")
        self.note(f"  realised {s.realisedUsd:+.2f}")
        if s.trades:
            self.note(f"  average {s.realisedUsd / s.trades:+.4f} per trade")
        if self.haltReason:
            self.note(f"  halted: {self.haltReason}")
