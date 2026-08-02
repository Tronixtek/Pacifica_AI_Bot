from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.config import Settings
from app.crt.engine import CrtEngine
from app.mt5.client import Mt5Client
from app.performance.attribution import BotPerformance, attribute
from app.scalper.engine import ScalperEngine


class BotFleet:
    """Runs the additional bots beside the main strategy engine.

    All three share one MT5 client - the MetaTrader5 package serialises every
    call against a single terminal connection anyway, so separate clients would
    buy nothing and multiply the connection handling.

    Each bot trades under its own magic number, which is what keeps their
    positions, limits and results separable on a shared account.
    """

    def __init__(self, settings: Settings, client: Mt5Client) -> None:
        self.settings = settings
        self.client = client
        self.scalper: ScalperEngine | None = None
        self.crt: CrtEngine | None = None
        self._tasks: list[asyncio.Task] = []
        self.startedAt: datetime | None = None

    @property
    def registry(self) -> dict[int, tuple[str, str]]:
        """Magic number -> (id, label) for everything trading this account."""
        entries = {
            self.settings.mt5MagicNumber: ("price_action", "Price Action (breakout fade)"),
        }
        if self.settings.scalperEnabled:
            entries[self.settings.scalperMagicNumber] = ("scalper", "Fixed-target scalper")
        if self.settings.crtEnabled:
            entries[self.settings.crtMagicNumber] = ("crt", "CRT sweep + trail")
        return entries

    async def start(self, symbols: list[str], specs: dict) -> None:
        self.startedAt = datetime.now(timezone.utc)

        if self.settings.scalperEnabled:
            self.scalper = ScalperEngine(self.settings, self.client)
            self._tasks.append(asyncio.create_task(self._guard("scalper", self.scalper.start())))

        if self.settings.crtEnabled:
            self.crt = CrtEngine(self.settings, self.client)
            self._tasks.append(
                asyncio.create_task(self._guard("crt", self.crt.start(symbols, specs)))
            )

    async def _guard(self, name: str, coro) -> None:
        """Keep one bot's failure from taking down the others or the API."""
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[fleet] {name} stopped: {exc}", flush=True)

    async def stop(self) -> None:
        if self.scalper:
            self.scalper.running = False
        if self.crt:
            await self.crt.stop()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def performance(self) -> list[BotPerformance]:
        """Realised and open results per bot, read from the account itself.

        Deliberately sourced from MT5 history rather than in-memory counters:
        the terminal is the authority, and it survives a restart of this
        process while counters do not.
        """
        now = datetime.now(timezone.utc)
        deals = await self.client.history_deals_range(
            datetime(2000, 1, 1, tzinfo=timezone.utc), now
        )
        positions = await self.client.positions_get()
        return attribute(deals, positions, self.registry)
