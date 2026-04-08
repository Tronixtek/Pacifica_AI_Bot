from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import Settings
from app.pacifica.client import PacificaClient
from app.training.store import DatasetStore

INTERVAL_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
}


@dataclass(slots=True)
class BackfillSummary:
    symbol: str
    interval: str
    candleCount: int = 0
    tradeCount: int = 0


class PacificaTrainingCollector:
    def __init__(
        self,
        settings: Settings,
        client: PacificaClient,
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
        include_recent_trades: bool = True,
    ) -> list[BackfillSummary]:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        results: list[BackfillSummary] = []

        for symbol in symbols:
            trade_count = 0
            if include_recent_trades:
                recent_trades, last_order_id = await self.client.get_recent_trades(symbol)
                trade_rows = [
                    self._normalize_recent_trade(symbol, item, last_order_id)
                    for item in recent_trades
                ]
                trade_count = self.store.write_jsonl(
                    self._raw_path(symbol, "recent_trades.jsonl"),
                    trade_rows,
                    append=False,
                )
                self.store.update_manifest(
                    f"recent_trades:{self.settings.pacificaNetwork}:{symbol}",
                    {
                        "type": "recent_trades",
                        "symbol": symbol,
                        "network": self.settings.pacificaNetwork,
                        "records": trade_count,
                        "lastOrderId": last_order_id,
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
                        f"mark_candles:{self.settings.pacificaNetwork}:{symbol}:{interval}",
                        {
                            "type": "mark_candles",
                            "symbol": symbol,
                            "interval": interval,
                            "network": self.settings.pacificaNetwork,
                            "lookbackDays": lookback_days,
                            "records": candle_count,
                            "startTime": candle_rows[0]["openTime"],
                            "endTime": candle_rows[-1]["openTime"],
                        },
                    )
                results.append(
                    BackfillSummary(
                        symbol=symbol,
                        interval=interval,
                        candleCount=candle_count,
                        tradeCount=trade_count,
                    )
                )

        return results

    async def stream_live(
        self,
        *,
        symbols: list[str],
        capture_prices: bool = True,
        capture_trades: bool = True,
    ) -> None:
        if not capture_prices and not capture_trades:
            raise ValueError("At least one stream source must be enabled.")

        try:
            from websockets import connect
        except ImportError as exc:
            raise RuntimeError(f"websockets package unavailable: {exc}") from exc

        async with connect(self.settings.pacificaWsUrl, ping_interval=None) as websocket:
            if capture_prices:
                await websocket.send(json.dumps({"method": "subscribe", "params": {"source": "prices"}}))
            if capture_trades:
                for symbol in symbols:
                    await websocket.send(
                        json.dumps(
                            {
                                "method": "subscribe",
                                "params": {"source": "trades", "symbol": symbol},
                            }
                        )
                    )

            while True:
                try:
                    raw_message = await asyncio.wait_for(
                        websocket.recv(),
                        timeout=self.settings.wsHeartbeatSec,
                    )
                except TimeoutError:
                    await websocket.send(json.dumps({"method": "ping"}))
                    continue
                payload = json.loads(raw_message)
                if capture_prices:
                    self._handle_price_stream_payload(symbols, payload)
                if capture_trades:
                    self._handle_trade_stream_payload(symbols, payload)

    def _handle_price_stream_payload(self, symbols: list[str], payload: dict[str, Any]) -> None:
        if payload.get("channel") != "prices" or not isinstance(payload.get("data"), list):
            return

        collected_at = datetime.now(timezone.utc).isoformat()
        for item in payload["data"]:
            symbol = item.get("symbol")
            if symbol not in symbols:
                continue
            row = {
                "symbol": symbol,
                "markPrice": self._to_float(item.get("mark")),
                "midPrice": self._to_float(item.get("mid")),
                "fundingRate": self._to_float(item.get("funding") or item.get("funding_rate")),
                "nextFundingRate": self._to_float(
                    item.get("next_funding") or item.get("next_funding_rate")
                ),
                "openInterest": self._to_float(item.get("open_interest")),
                "volume24h": self._to_float(item.get("volume_24h")),
                "timestamp": self._coerce_timestamp(item.get("timestamp") or item.get("ts")),
                "collectedAt": collected_at,
                "source": "websocket_prices",
            }
            self.store.append_jsonl(self._stream_path(symbol, "prices.jsonl"), row)

    def _handle_trade_stream_payload(self, symbols: list[str], payload: dict[str, Any]) -> None:
        if payload.get("channel") != "trades" or not isinstance(payload.get("data"), list):
            return

        collected_at = datetime.now(timezone.utc).isoformat()
        for item in payload["data"]:
            symbol = item.get("symbol") or item.get("s")
            if symbol not in symbols:
                continue
            row = {
                "symbol": symbol,
                "eventType": item.get("event_type") or item.get("eventType"),
                "price": self._to_float(item.get("price") or item.get("p")),
                "amount": self._to_float(item.get("amount") or item.get("a")),
                "side": item.get("side") or item.get("d"),
                "cause": item.get("cause") or item.get("tc"),
                "lastOrderId": self._to_int(item.get("last_order_id") or item.get("li")),
                "createdAt": self._coerce_timestamp(item.get("created_at") or item.get("t")),
                "collectedAt": collected_at,
                "source": "websocket_trades",
            }
            self.store.append_jsonl(self._stream_path(symbol, "trades.jsonl"), row)

    async def _fetch_candles(
        self,
        symbol: str,
        interval: str,
        lookback_days: int,
        now_ms: int,
    ) -> list[dict[str, Any]]:
        interval = interval.lower()
        interval_ms = INTERVAL_MS.get(interval)
        if interval_ms is None:
            raise ValueError(f"Unsupported interval: {interval}")

        start_ms = now_ms - (lookback_days * 86_400_000)
        request_span_ms = interval_ms * 500
        cursor = start_ms
        deduped: dict[int, dict[str, Any]] = {}

        while cursor < now_ms:
            end_ms = min(cursor + request_span_ms, now_ms)
            payload = await self.client.get_candles(
                symbol=symbol,
                interval=interval,
                start_time=cursor,
                end_time=end_ms,
            )
            for candle in payload:
                timestamp = self._to_int(
                    candle.get("t")
                    or candle.get("open_time")
                    or candle.get("timestamp")
                    or candle.get("ts")
                )
                if timestamp is None:
                    continue
                deduped[timestamp] = candle

            cursor = end_ms + interval_ms

        return [deduped[key] for key in sorted(deduped.keys())]

    def _normalize_candle(self, symbol: str, interval: str, candle: dict[str, Any]) -> dict[str, Any]:
        interval = interval.lower()
        return {
            "symbol": symbol,
            "interval": interval,
            "openTime": self._coerce_timestamp(
                candle.get("t")
                or candle.get("open_time")
                or candle.get("timestamp")
                or candle.get("ts")
            ),
            "open": self._to_float(candle.get("o") or candle.get("open")),
            "high": self._to_float(candle.get("h") or candle.get("high")),
            "low": self._to_float(candle.get("l") or candle.get("low")),
            "close": self._to_float(candle.get("c") or candle.get("close")),
            "volume": self._to_float(candle.get("v") or candle.get("volume")),
            "collectedAt": datetime.now(timezone.utc).isoformat(),
            "source": "rest_mark_candles",
        }

    def _normalize_recent_trade(
        self,
        symbol: str,
        trade: dict[str, Any],
        last_order_id: int | None,
    ) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "eventType": trade.get("event_type"),
            "price": self._to_float(trade.get("price")),
            "amount": self._to_float(trade.get("amount")),
            "side": trade.get("side"),
            "cause": trade.get("cause"),
            "createdAt": self._coerce_timestamp(trade.get("created_at")),
            "lastOrderId": last_order_id,
            "collectedAt": datetime.now(timezone.utc).isoformat(),
            "source": "rest_recent_trades",
        }

    def _raw_path(self, symbol: str, *parts: str) -> Path:
        return Path("raw") / self.settings.pacificaNetwork / symbol / Path(*parts)

    def _stream_path(self, symbol: str, *parts: str) -> Path:
        return Path("stream") / self.settings.pacificaNetwork / symbol / Path(*parts)

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
