from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import Settings
from app.pacifica.models import MarketQuote, MarketSpec


class PacificaClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = httpx.AsyncClient(
            base_url=self.settings.pacificaRestUrl,
            timeout=10.0,
            headers=self._default_headers(),
        )

    def _default_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.settings.pacificaApiConfigKey:
            headers["PF-API-KEY"] = self.settings.pacificaApiConfigKey
        return headers

    async def close(self) -> None:
        await self._client.aclose()

    async def get_prices(self, symbols: list[str] | None = None) -> dict[str, MarketQuote]:
        response = await self._client.get("/info/prices")
        payload = self._unwrap_response(response)
        requested = set(symbols or [])
        prices: dict[str, MarketQuote] = {}

        for item in payload:
            symbol = item.get("symbol")
            if not symbol:
                continue
            if requested and symbol not in requested:
                continue
            updated_at = self._parse_timestamp(item.get("timestamp") or item.get("ts"))
            prices[symbol] = MarketQuote(
                symbol=symbol,
                markPrice=float(item["mark"]),
                midPrice=self._optional_float(item.get("mid")),
                updatedAt=updated_at,
            )

        return prices

    async def get_market_info(self, symbols: list[str] | None = None) -> dict[str, MarketSpec]:
        response = await self._client.get("/info")
        payload = self._unwrap_response(response)
        requested = set(symbols or [])
        markets: dict[str, MarketSpec] = {}

        for item in payload:
            symbol = item.get("symbol")
            if not symbol:
                continue
            if requested and symbol not in requested:
                continue
            markets[symbol] = MarketSpec(
                symbol=symbol,
                tickSize=float(item.get("tick_size", 0.0)),
                lotSize=float(item.get("lot_size", 0.0)),
                minOrderSizeUsd=float(item.get("min_order_size", 0.0)),
                maxOrderSizeUsd=float(item.get("max_order_size", 0.0)),
                maxLeverage=int(item.get("max_leverage", 1)),
                isolatedOnly=bool(item.get("isolated_only", False)),
            )

        return markets

    async def get_orderbook(self, symbol: str, agg_level: int = 2) -> dict[str, Any]:
        response = await self._client.get(
            "/book",
            params={"symbol": symbol, "agg_level": agg_level},
        )
        return self._unwrap_response(response)

    async def get_candles(
        self,
        symbol: str,
        interval: str,
        start_time: int,
        end_time: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "symbol": symbol,
            "interval": interval,
            "start_time": start_time,
        }
        if end_time is not None:
            params["end_time"] = end_time
        response = await self._client.get("/kline/mark", params=params)
        return self._unwrap_response(response)

    async def get_account_info(self, account: str) -> dict[str, Any]:
        response = await self._client.get("/account", params={"account": account})
        return self._unwrap_response(response)

    async def get_positions(self, account: str) -> tuple[list[dict[str, Any]], int | None]:
        response = await self._client.get("/positions", params={"account": account})
        payload = self._unwrap_response(response, include_full_payload=True)
        return payload.get("data", []), self._optional_int(payload.get("last_order_id"))

    async def get_open_orders(self, account: str) -> tuple[list[dict[str, Any]], int | None]:
        response = await self._client.get("/orders", params={"account": account})
        payload = self._unwrap_response(response, include_full_payload=True)
        return payload.get("data", []), self._optional_int(payload.get("last_order_id"))

    async def get_recent_trades(self, symbol: str) -> tuple[list[dict[str, Any]], int | None]:
        response = await self._client.get("/trades", params={"symbol": symbol})
        payload = self._unwrap_response(response, include_full_payload=True)
        return payload.get("data", []), self._optional_int(payload.get("last_order_id"))

    async def create_market_order(
        self,
        payload: dict[str, Any],
        account: str | None = None,
    ) -> dict[str, Any]:
        signed_request = self._sign_request("create_market_order", payload, account=account)
        response = await self._client.post("/orders/create_market", json=signed_request)
        return self._unwrap_response(response, expect_data=False)

    async def create_position_tpsl(
        self,
        payload: dict[str, Any],
        account: str | None = None,
    ) -> dict[str, Any]:
        signed_request = self._sign_request("set_position_tpsl", payload, account=account)
        response = await self._client.post("/positions/tpsl", json=signed_request)
        return self._unwrap_response(response, expect_data=False)

    def _sign_request(
        self,
        operation_type: str,
        payload: dict[str, Any],
        account: str | None = None,
    ) -> dict[str, Any]:
        import base58
        from solders.keypair import Keypair

        account_address = account or self.settings.pacificaAccountAddress
        if not account_address:
            raise RuntimeError("PACIFICA_ACCOUNT_ADDRESS is required for signed requests.")
        if not self.settings.pacificaAgentPrivateKey:
            raise RuntimeError("PACIFICA_AGENT_PRIVATE_KEY is required for signed requests.")

        keypair = Keypair.from_base58_string(self.settings.pacificaAgentPrivateKey)
        agent_wallet = str(keypair.pubkey())
        timestamp = int(time.time() * 1000)
        header = {
            "timestamp": timestamp,
            "expiry_window": self.settings.signatureExpiryWindowMs,
            "type": operation_type,
        }
        message = self._prepare_message(header, payload)
        signature = keypair.sign_message(message.encode("utf-8"))

        request_header = {
            "account": account_address,
            "agent_wallet": agent_wallet,
            "signature": base58.b58encode(bytes(signature)).decode("ascii"),
            "timestamp": header["timestamp"],
            "expiry_window": header["expiry_window"],
        }
        return {**request_header, **payload}

    def _prepare_message(self, header: dict[str, Any], payload: dict[str, Any]) -> str:
        data = {**header, "data": payload}
        sorted_payload = self._sort_json_keys(data)
        return json.dumps(sorted_payload, separators=(",", ":"))

    def _sort_json_keys(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self._sort_json_keys(value[key]) for key in sorted(value.keys())}
        if isinstance(value, list):
            return [self._sort_json_keys(item) for item in value]
        return value

    def _unwrap_response(
        self,
        response: httpx.Response,
        expect_data: bool = True,
        include_full_payload: bool = False,
    ) -> Any:
        payload: dict[str, Any] = {}
        try:
            payload = response.json()
        except Exception:
            payload = {}

        if response.is_error:
            raise RuntimeError(self._friendly_http_error(response, payload))

        if payload.get("success") is False:
            error = payload.get("error") or payload.get("message") or "Pacifica API request failed."
            raise RuntimeError(str(error))

        if include_full_payload:
            return payload

        if expect_data:
            return payload.get("data", [])
        return payload

    def _friendly_http_error(
        self,
        response: httpx.Response,
        payload: dict[str, Any],
    ) -> str:
        status_code = response.status_code
        path = response.request.url.path
        normalized_path = path.rstrip("/")
        account = response.request.url.params.get("account")

        api_error = payload.get("error") or payload.get("message")
        if isinstance(api_error, str) and api_error.strip():
            return api_error

        response_text = response.text.strip()
        if response_text:
            compact_text = " ".join(response_text.split())
            if compact_text and compact_text != "{}":
                return (
                    f"Pacifica API request failed with HTTP {status_code} for {path}. "
                    f"Response: {compact_text}"
                )

        if status_code == 404 and normalized_path.endswith("/account"):
            if account:
                return (
                    f"Pacifica does not have an initialized account record for {account} on "
                    f"{self.settings.pacificaNetwork} yet. Open Pacifica on the same network, "
                    "connect that wallet, and deposit collateral first, then retry account sync."
                )
            return (
                f"Pacifica does not have an initialized account record on "
                f"{self.settings.pacificaNetwork} yet. Open Pacifica, connect the wallet, "
                "and deposit collateral first, then retry account sync."
            )

        if status_code == 404 and (
            normalized_path.endswith("/positions") or normalized_path.endswith("/orders")
        ):
            return (
                f"Pacifica account state is not initialized on {self.settings.pacificaNetwork} "
                "yet, so positions and orders are unavailable."
            )

        if status_code == 403:
            return (
                "Pacifica returned 403 Forbidden. Pacifica's official terms say access to "
                "trading functionality is programmatically restricted from certain "
                "jurisdictions, including the United States."
            )

        return f"Pacifica API request failed with HTTP {status_code} for {path}."

    def _optional_float(self, value: Any) -> float | None:
        if value is None:
            return None
        return float(value)

    def _optional_int(self, value: Any) -> int | None:
        if value is None:
            return None
        return int(value)

    def _parse_timestamp(self, value: Any) -> datetime | None:
        if value is None:
            return None
        timestamp = int(value)
        if timestamp > 10_000_000_000:
            return datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
