from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import Settings
from app.mt5.client import Mt5Client
from app.training.store import DatasetStore

INTERVAL_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}


@dataclass(slots=True)
class BackfillSummary:
    symbol: str
    interval: str
    candleCount: int = 0
    tickCount: int = 0


class Mt5TrainingCollector:
    def __init__(
        self,
        settings: Settings,
        client: Mt5Client,
        *,
        output_root: Path | None = None,
    ) -> None:
        self.settings = settings
        self.client = client
        self.outputRoot = output_root or Path(__file__).resolve().parents[2] / "data" / "training"
        self.store = DatasetStore(self.outputRoot)

    async def backfill(
        self,
        *,
        symbols: list[str],
        intervals: list[str],
        lookback_days: int,
        include_recent_ticks: bool = True,
    ) -> list[BackfillSummary]:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        results: list[BackfillSummary] = []

        for symbol in symbols:
            tick_count = 0
            if include_recent_ticks:
                to_dt = datetime.now(timezone.utc)
                from_dt = to_dt.replace(hour=0, minute=0, second=0, microsecond=0)
                ticks = await self.client.get_ticks_range(symbol, from_dt, to_dt)
                tick_rows = [self._normalize_tick(symbol, tick) for tick in ticks]
                tick_count = self.store.write_jsonl(
                    self._raw_path(symbol, "recent_ticks.jsonl"),
                    tick_rows,
                    append=False,
                )
                self.store.update_manifest(
                    f"recent_ticks:{self.settings.botMode}:{symbol}",
                    {
                        "type": "recent_ticks",
                        "symbol": symbol,
                        "mode": self.settings.botMode,
                        "records": tick_count,
                    },
                )

            for interval in intervals:
                candles = await self._fetch_candles(symbol, interval, lookback_days, now_ms)
                candle_rows = [self._normalize_candle(symbol, interval, candle) for candle in candles]
                candle_count = self.store.write_jsonl(
                    self._raw_path(symbol, interval, "mark_candles.jsonl"),
                    candle_rows,
                    append=False,
                )
                if candle_rows:
                    self.store.update_manifest(
                        f"mark_candles:{self.settings.botMode}:{symbol}:{interval}",
                        {
                            "type": "mark_candles",
                            "symbol": symbol,
                            "interval": interval,
                            "mode": self.settings.botMode,
                            "lookbackDays": lookback_days,
                            "records": candle_count,
                            "startTime": candle_rows[0]["openTime"] if candle_rows else None,
                            "endTime": candle_rows[-1]["openTime"] if candle_rows else None,
                        },
                    )
                results.append(
                    BackfillSummary(
                        symbol=symbol,
                        interval=interval,
                        candleCount=candle_count,
                        tickCount=tick_count,
                    )
                )

        return results

    async def stream_live(
        self,
        *,
        symbols: list[str],
        poll_interval_sec: float = 2.0,
    ) -> None:
        cursors: dict[str, datetime] = {
            symbol: datetime.now(timezone.utc) for symbol in symbols
        }

        while True:
            for symbol in symbols:
                to_dt = datetime.now(timezone.utc)
                ticks = await self.client.get_ticks_range(symbol, cursors[symbol], to_dt)
                for tick in ticks:
                    self.store.append_jsonl(
                        self._stream_path(symbol, "ticks.jsonl"),
                        self._normalize_tick(symbol, tick),
                    )
                cursors[symbol] = to_dt
            await asyncio.sleep(poll_interval_sec)

    async def _fetch_candles(
        self,
        symbol: str,
        interval: str,
        lookback_days: int,
        now_ms: int,
    ) -> list[dict[str, Any]]:
        interval = interval.lower()
        if interval not in INTERVAL_MS:
            raise ValueError(f"Unsupported interval: {interval}")

        start_ms = now_ms - (lookback_days * 86_400_000)
        return await self.client.get_candles(
            symbol=symbol,
            interval=interval,
            start_time=start_ms,
            end_time=now_ms,
        )

    def _normalize_candle(self, symbol: str, interval: str, candle: dict[str, Any]) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "interval": interval.lower(),
            "openTime": self._coerce_timestamp(candle.get("t")),
            "open": self._to_float(candle.get("o")),
            "high": self._to_float(candle.get("h")),
            "low": self._to_float(candle.get("l")),
            "close": self._to_float(candle.get("c")),
            "volume": self._to_float(candle.get("v")),
            "collectedAt": datetime.now(timezone.utc).isoformat(),
            "source": "mt5_rates",
        }

    def _normalize_tick(self, symbol: str, tick: dict[str, Any]) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "bid": self._to_float(tick.get("bid")),
            "ask": self._to_float(tick.get("ask")),
            "last": self._to_float(tick.get("last")),
            "volume": self._to_float(tick.get("volume")),
            "createdAt": self._coerce_timestamp(tick.get("time")),
            "collectedAt": datetime.now(timezone.utc).isoformat(),
            "source": "mt5_ticks",
        }

    def _raw_path(self, symbol: str, *parts: str) -> Path:
        return Path("raw") / self.settings.botMode / symbol / Path(*parts)

    def _stream_path(self, symbol: str, *parts: str) -> Path:
        return Path("stream") / self.settings.botMode / symbol / Path(*parts)

    def _coerce_timestamp(self, value: Any) -> str | None:
        timestamp = self._to_int(value)
        if timestamp is None:
            return None
        if timestamp > 10_000_000_000:
            dt = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)
        else:
            dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        return dt.isoformat()

    def _to_float(self, value: Any) -> float | None:
        if value is None:
            return None
        return float(value)

    def _to_int(self, value: Any) -> int | None:
        if value is None:
            return None
        return int(value)
