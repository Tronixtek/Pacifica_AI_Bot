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
        """Magic number -> (id, label) for everything trading this account.

        Labels are derived from the live settings rather than hardcoded, so the
        dashboard cannot describe a bot as doing something it no longer does -
        the scalper read "Fixed-target scalper" for a while after it had been
        switched to trailing.
        """
        s = self.settings

        pa_exit = (
            f"trailing {s.trailAtrMultiple:g} ATR"
            if s.trailingStopEnabled
            else f"{s.contrarianTargetRiskMultiple:g}R target"
        )
        pa_setups = "+".join(s.enabledSetups) if s.enabledSetups else "all setups"
        direction = "fade" if s.contrarianExecutionEnabled else "follow"
        entries = {
            s.mt5MagicNumber: (
                "price_action",
                f"Price Action ({pa_setups} {direction}, {pa_exit}, {s.strategyTimeframe})",
            )
        }

        if s.scalperEnabled:
            scalp_exit = (
                f"trails from ${s.scalperTrailActivateUsd:.2f}"
                if s.scalperTrailingEnabled
                else f"fixed ${s.scalperTargetUsd:.2f} target"
            )
            entries[s.scalperMagicNumber] = (
                "scalper",
                f"Scalper ({scalp_exit}, ${s.scalperStopUsd:.2f} stop)",
            )

        if s.crtEnabled:
            window = f"{s.crtRangeFactor}x{s.crtExecutionTimeframe}"
            crt_exit = (
                f"trails {s.crtTrailAtrMultiple:g} ATR"
                if s.crtTrailActivateR > 0
                else "fixed target"
            )
            entries[s.crtMagicNumber] = ("crt", f"CRT sweep ({window}, {crt_exit})")

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
