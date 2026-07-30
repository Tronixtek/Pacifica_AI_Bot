from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from app.config import Settings
from app.mt5.models import MarketQuote, MarketSpec

try:
    import MetaTrader5 as mt5

    MT5_AVAILABLE = True
except ImportError:
    mt5 = None  # type: ignore[assignment]
    MT5_AVAILABLE = False


TRADE_RETCODE_DONE = getattr(mt5, "TRADE_RETCODE_DONE", 10009) if MT5_AVAILABLE else 10009

_TIMEFRAME_NAMES = {
    "1m": "TIMEFRAME_M1",
    "3m": "TIMEFRAME_M3",
    "5m": "TIMEFRAME_M5",
    "15m": "TIMEFRAME_M15",
    "30m": "TIMEFRAME_M30",
    "1h": "TIMEFRAME_H1",
    "4h": "TIMEFRAME_H4",
    "1d": "TIMEFRAME_D1",
}

_ORDER_TYPE_LABELS = {
    2: "buy_limit",
    3: "sell_limit",
    4: "buy_stop",
    5: "sell_stop",
    6: "buy_stop_limit",
    7: "sell_stop_limit",
}


class Mt5Client:
    """Owns every direct call into the MetaTrader5 package.

    All `mt5.*` access is serialized behind one lock and pushed to a worker
    thread, because the package is synchronous and not safe for concurrent
    use against a single terminal IPC connection.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = asyncio.Lock()
        self.connected = False
        self.lastError: str | None = None

    async def connect(self) -> bool:
        if not MT5_AVAILABLE:
            self.lastError = "MetaTrader5 package is not installed on this platform (Windows-only)."
            return False

        async with self._lock:
            init_kwargs: dict[str, Any] = {"timeout": self.settings.mt5ConnectTimeoutMs}
            if self.settings.mt5TerminalPath:
                init_kwargs["path"] = self.settings.mt5TerminalPath

            initialized = await asyncio.to_thread(mt5.initialize, **init_kwargs)
            if not initialized:
                self.lastError = f"MT5 initialize() failed: {mt5.last_error()}"
                self.connected = False
                return False

            if self.settings.mt5Login and self.settings.mt5Password and self.settings.mt5Server:
                logged_in = await asyncio.to_thread(
                    mt5.login,
                    self.settings.mt5Login,
                    password=self.settings.mt5Password,
                    server=self.settings.mt5Server,
                    timeout=self.settings.mt5ConnectTimeoutMs,
                )
                if not logged_in:
                    self.lastError = f"MT5 login() failed: {mt5.last_error()}"
                    self.connected = False
                    return False

            account = await asyncio.to_thread(mt5.account_info)
            if account is None:
                self.lastError = "MT5 account_info() returned no data after connect."
                self.connected = False
                return False
            if self.settings.mt5Login and account.login != self.settings.mt5Login:
                self.lastError = (
                    f"Connected MT5 account {account.login} does not match the configured "
                    f"MT5_LOGIN {self.settings.mt5Login}."
                )
                self.connected = False
                return False

            self.connected = True
            self.lastError = None
            return True

    async def shutdown(self) -> None:
        if not MT5_AVAILABLE:
            return
        async with self._lock:
            await asyncio.to_thread(mt5.shutdown)
            self.connected = False

    async def _call(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        async with self._lock:
            return await asyncio.to_thread(fn, *args, **kwargs)

    async def symbol_select(self, symbol: str) -> bool:
        result = await self._call(mt5.symbol_select, symbol, True)
        return bool(result)

    async def symbol_info(self, symbol: str) -> MarketSpec | None:
        info = await self._call(mt5.symbol_info, symbol)
        if info is None:
            return None
        return MarketSpec(
            symbol=symbol,
            digits=info.digits,
            tickSize=info.trade_tick_size or info.point,
            tickValue=info.trade_tick_value,
            contractSize=info.trade_contract_size,
            volumeStep=info.volume_step,
            volumeMin=info.volume_min,
            volumeMax=info.volume_max,
            fillingModes=self._resolve_filling_mode_labels(info.filling_mode),
        )

    async def symbol_info_tick(self, symbol: str) -> MarketQuote | None:
        tick = await self._call(mt5.symbol_info_tick, symbol)
        if tick is None or (not tick.bid and not tick.ask):
            return None
        if tick.bid and tick.ask:
            mark = (tick.bid + tick.ask) / 2
            mid = mark
        else:
            mark = tick.last or tick.bid or tick.ask
            mid = None
        return MarketQuote(
            symbol=symbol,
            markPrice=mark,
            midPrice=mid,
            bidPrice=tick.bid or None,
            askPrice=tick.ask or None,
            updatedAt=datetime.fromtimestamp(tick.time, tz=timezone.utc) if tick.time else None,
        )

    async def account_info(self) -> Any:
        return await self._call(mt5.account_info)

    async def positions_get(self) -> list[Any]:
        result = await self._call(mt5.positions_get)
        return list(result) if result is not None else []

    async def orders_get(self) -> list[Any]:
        result = await self._call(mt5.orders_get)
        return list(result) if result is not None else []

    async def history_deals_for_position(self, ticket: int) -> list[Any]:
        result = await self._call(mt5.history_deals_get, position=ticket)
        return list(result) if result is not None else []

    async def send_market_order(self, order: dict[str, Any]) -> dict[str, Any]:
        symbol = order["symbol"]
        tick = await self._call(mt5.symbol_info_tick, symbol)
        info = await self._call(mt5.symbol_info, symbol)
        if tick is None or info is None:
            return {"retcode": None, "error": f"No tick/symbol info available for {symbol}."}

        side = order["side"]
        price = tick.ask if side == "buy" else tick.bid
        request: dict[str, Any] = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": order["volume"],
            "type": mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL,
            "price": price,
            "deviation": order.get("deviation", 20),
            "magic": order.get("magic", 0),
            "comment": (order.get("comment") or "")[:24],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._resolve_order_filling_mode(info),
        }
        if order.get("sl"):
            request["sl"] = order["sl"]
        if order.get("tp"):
            request["tp"] = order["tp"]

        result = await self._call(mt5.order_send, request)
        if result is None:
            error = await self._call(mt5.last_error)
            return {"retcode": None, "error": str(error), "request": request}

        return {
            "retcode": result.retcode,
            "comment": result.comment,
            "order": result.order,
            "deal": result.deal,
            "price": result.price,
            "volume": result.volume,
            "request": request,
        }

    async def get_candles(
        self,
        symbol: str,
        interval: str,
        start_time: int,
        end_time: int | None = None,
        retries: int = 3,
    ) -> list[dict[str, Any]]:
        if not MT5_AVAILABLE:
            raise RuntimeError("MetaTrader5 package is not available on this platform.")

        timeframe = self._resolve_timeframe(interval)
        start_dt = datetime.fromtimestamp(start_time / 1000, tz=timezone.utc)
        end_dt = (
            datetime.fromtimestamp(end_time / 1000, tz=timezone.utc)
            if end_time is not None
            else datetime.now(timezone.utc)
        )
        await self.symbol_select(symbol)

        for attempt in range(retries):
            rates = await self._call(mt5.copy_rates_range, symbol, timeframe, start_dt, end_dt)
            if rates is not None and len(rates) > 0:
                return [
                    {
                        "t": int(row["time"]) * 1000,
                        "o": float(row["open"]),
                        "h": float(row["high"]),
                        "l": float(row["low"]),
                        "c": float(row["close"]),
                        "v": float(row["tick_volume"]),
                    }
                    for row in rates
                ]
            await asyncio.sleep(0.5 * (attempt + 1))

        return []

    async def get_ticks_range(self, symbol: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
        if not MT5_AVAILABLE:
            raise RuntimeError("MetaTrader5 package is not available on this platform.")
        await self.symbol_select(symbol)
        ticks = await self._call(mt5.copy_ticks_range, symbol, start, end, mt5.COPY_TICKS_ALL)
        if ticks is None:
            return []
        return [
            {
                "time": int(row["time"]) * 1000,
                "bid": float(row["bid"]),
                "ask": float(row["ask"]),
                "last": float(row["last"]),
                "volume": float(row["volume"]),
            }
            for row in ticks
        ]

    def _resolve_timeframe(self, interval: str) -> Any:
        attr = _TIMEFRAME_NAMES.get(interval)
        if attr is None:
            raise ValueError(f"Unsupported MT5 candle interval: {interval}")
        return getattr(mt5, attr)

    def _resolve_filling_mode_labels(self, filling_mode_bitmask: int) -> list[str]:
        modes: list[str] = []
        if filling_mode_bitmask & mt5.SYMBOL_FILLING_FOK:
            modes.append("FOK")
        if filling_mode_bitmask & mt5.SYMBOL_FILLING_IOC:
            modes.append("IOC")
        if filling_mode_bitmask & mt5.SYMBOL_FILLING_RETURN:
            modes.append("RETURN")
        return modes

    def _resolve_order_filling_mode(self, info: Any) -> Any:
        mask = info.filling_mode
        if mask & mt5.SYMBOL_FILLING_IOC:
            return mt5.ORDER_FILLING_IOC
        if mask & mt5.SYMBOL_FILLING_FOK:
            return mt5.ORDER_FILLING_FOK
        return mt5.ORDER_FILLING_RETURN

    def order_type_label(self, order_type: int) -> str:
        return _ORDER_TYPE_LABELS.get(order_type, f"type_{order_type}")

    def order_side_from_type(self, order_type: int) -> str:
        return "buy" if "buy" in self.order_type_label(order_type) else "sell"

    def deal_entry_is_exit(self, entry_code: int) -> bool:
        return entry_code in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY)

    def deal_reason_label(self, reason_code: int) -> str:
        mapping = {
            mt5.DEAL_REASON_CLIENT: "client",
            mt5.DEAL_REASON_MOBILE: "mobile",
            mt5.DEAL_REASON_WEB: "web",
            mt5.DEAL_REASON_EXPERT: "expert",
            mt5.DEAL_REASON_SL: "stop_loss",
            mt5.DEAL_REASON_TP: "take_profit",
            mt5.DEAL_REASON_SO: "stop_out",
        }
        return mapping.get(reason_code, "unknown")
