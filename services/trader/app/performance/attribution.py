from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(slots=True)
class BotPerformance:
    """Realised and open results for one bot, attributed by magic number."""

    botId: str
    label: str
    magicNumber: int
    trades: int = 0
    wins: int = 0
    losses: int = 0
    realisedUsd: float = 0.0
    openPositions: int = 0
    openVolume: float = 0.0
    unrealisedUsd: float = 0.0
    bestUsd: float = 0.0
    worstUsd: float = 0.0
    lastTradeAt: datetime | None = None
    symbols: list[str] = field(default_factory=list)

    @property
    def winRate(self) -> float:
        return round(self.wins / self.trades * 100, 1) if self.trades else 0.0

    @property
    def averageUsd(self) -> float:
        return round(self.realisedUsd / self.trades, 4) if self.trades else 0.0

    @property
    def equityImpactUsd(self) -> float:
        """Realised plus open, i.e. what this bot has done to the balance."""
        return round(self.realisedUsd + self.unrealisedUsd, 2)


def attribute(
    deals,
    positions,
    registry: dict[int, tuple[str, str]],
    since: datetime | None = None,
) -> list[BotPerformance]:
    """Split account history and open positions across bots by magic number.

    `registry` maps magic number to (botId, label).

    Attribution is by the ENTRY deal's magic, not the exit's. A position closed
    by hand from the terminal or the mobile app records magic 0 on the exit, so
    reading the exit would silently drop those trades from the bot that opened
    them - and manual closes are exactly the ones worth seeing.
    """
    perf = {
        magic: BotPerformance(botId=bot_id, label=label, magicNumber=magic)
        for magic, (bot_id, label) in registry.items()
    }

    entry_magic: dict[int, int] = {}
    entry_symbol: dict[int, str] = {}
    for deal in deals:
        position_id = getattr(deal, "position_id", 0)
        if not position_id:
            continue
        if getattr(deal, "entry", None) == 0:      # DEAL_ENTRY_IN
            entry_magic[position_id] = getattr(deal, "magic", 0)
            entry_symbol[position_id] = getattr(deal, "symbol", "")

    for deal in deals:
        position_id = getattr(deal, "position_id", 0)
        if not position_id or getattr(deal, "entry", None) not in (1, 3):
            continue                                # OUT / OUT_BY only
        magic = entry_magic.get(position_id)
        if magic not in perf:
            continue
        closed_at = datetime.fromtimestamp(deal.time, tz=timezone.utc)
        if since is not None and closed_at < since:
            continue

        pnl = float(deal.profit) + float(deal.swap) + float(deal.commission)
        bot = perf[magic]
        bot.trades += 1
        bot.realisedUsd = round(bot.realisedUsd + pnl, 2)
        if pnl > 0:
            bot.wins += 1
        else:
            bot.losses += 1
        bot.bestUsd = round(max(bot.bestUsd, pnl), 2)
        bot.worstUsd = round(min(bot.worstUsd, pnl), 2)
        if bot.lastTradeAt is None or closed_at > bot.lastTradeAt:
            bot.lastTradeAt = closed_at
        symbol = entry_symbol.get(position_id) or getattr(deal, "symbol", "")
        if symbol and symbol not in bot.symbols:
            bot.symbols.append(symbol)

    for position in positions:
        magic = getattr(position, "magic", 0)
        if magic not in perf:
            continue
        bot = perf[magic]
        bot.openPositions += 1
        bot.openVolume = round(bot.openVolume + float(position.volume), 2)
        bot.unrealisedUsd = round(
            bot.unrealisedUsd + float(position.profit) + float(position.swap), 2
        )
        symbol = getattr(position, "symbol", "")
        if symbol and symbol not in bot.symbols:
            bot.symbols.append(symbol)

    return [perf[m] for m in registry]
