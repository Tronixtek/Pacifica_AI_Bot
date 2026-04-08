from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from uuid import uuid4

from app.config import Settings
from app.contracts import ServiceHealth, StrategySignal, SystemStatus
from app.pacifica.client import PacificaClient
from app.pacifica.models import (
    ExecutionResult,
    MarketSpec,
    RemoteAccountSnapshot,
    RemoteOpenOrderSnapshot,
    RemotePositionSnapshot,
    RemoteTradingSnapshot,
)


class PacificaExecutionService:
    def __init__(self, settings: Settings, client: PacificaClient) -> None:
        self.settings = settings
        self.client = client
        self.sessionAccountAddress: str | None = None
        self.lastAccountSyncAt: datetime | None = None
        self.lastError: str | None = None
        self.remoteSnapshot: RemoteTradingSnapshot | None = None

    async def sync_remote_account(self) -> RemoteTradingSnapshot | None:
        account_address = self._effective_account_address()
        if not account_address:
            return None

        account_payload, positions_response, open_orders_response = await asyncio.gather(
            self.client.get_account_info(account_address),
            self.client.get_positions(account_address),
            self.client.get_open_orders(account_address),
        )

        positions_payload, positions_last_order_id = positions_response
        open_orders_payload, open_orders_last_order_id = open_orders_response
        synced_at = datetime.now(timezone.utc)

        snapshot = RemoteTradingSnapshot(
            account=RemoteAccountSnapshot(
                equityUsd=float(account_payload.get("account_equity", 0.0)),
                availableMarginUsd=float(account_payload.get("available_to_spend", 0.0)),
                balanceUsd=float(account_payload.get("balance", 0.0)),
                availableToWithdrawUsd=self._optional_float(account_payload.get("available_to_withdraw")),
                pendingBalanceUsd=self._optional_float(account_payload.get("pending_balance")),
                totalMarginUsedUsd=self._optional_float(account_payload.get("total_margin_used")),
                crossMaintenanceMarginUsd=self._optional_float(account_payload.get("cross_mmr")),
                openPositions=int(account_payload.get("positions_count", 0)),
                openOrders=int(account_payload.get("orders_count", 0)),
                stopOrders=int(account_payload.get("stop_orders_count", 0)),
                feeLevel=self._optional_int(account_payload.get("fee_level")),
                makerFeeRate=self._optional_float(account_payload.get("maker_fee")),
                takerFeeRate=self._optional_float(account_payload.get("taker_fee")),
                useLastTradedPriceForStops=self._optional_bool(
                    account_payload.get("use_ltp_for_stop_orders")
                ),
                updatedAt=self._parse_timestamp(account_payload.get("updated_at")),
            ),
            positions=[self._parse_remote_position(item) for item in positions_payload],
            openOrders=[self._parse_remote_open_order(item) for item in open_orders_payload],
            lastOrderId=max(
                order_id
                for order_id in (positions_last_order_id, open_orders_last_order_id)
                if order_id is not None
            )
            if positions_last_order_id is not None or open_orders_last_order_id is not None
            else None,
            syncedAt=synced_at,
        )

        self.remoteSnapshot = snapshot
        self.lastAccountSyncAt = synced_at
        self.lastError = None
        return snapshot

    async def execute_signal(
        self,
        signal: StrategySignal,
        market_specs: dict[str, MarketSpec],
    ) -> ExecutionResult:
        payload = self.build_market_order_payload(signal, market_specs)

        if not self.settings.enableLiveTrading:
            return ExecutionResult(
                accepted=False,
                message="Live trading is disabled. Payload prepared but not submitted.",
                payload=payload,
            )

        if self.settings.botMode not in {"testnet", "mainnet"}:
            return ExecutionResult(
                accepted=False,
                message="Bot mode is not configured for Pacifica execution.",
                payload=payload,
            )

        self._validate_execution_readiness()
        response = await self.client.create_market_order(
            payload,
            account=self._effective_account_address(),
        )
        order_id = response.get("order_id") or response.get("data", {}).get("i")
        protection_message = await self._sync_position_tpsl(signal, market_specs)
        message = (
            f"Submitted Pacifica market order for {signal.symbol}. Order id {order_id}."
            if order_id
            else f"Submitted Pacifica market order for {signal.symbol}."
        )
        if protection_message:
            message = f"{message} {protection_message}"
        return ExecutionResult(
            accepted=True,
            message=message,
            payload=payload,
            response=response,
            orderId=int(order_id) if order_id is not None else None,
        )

    def build_market_order_payload(
        self,
        signal: StrategySignal,
        market_specs: dict[str, MarketSpec],
    ) -> dict[str, object]:
        market_spec = market_specs.get(signal.symbol)
        size = self._format_amount(signal.size, market_spec.lotSize if market_spec else None)
        entry_notional = Decimal(size) * Decimal(str(signal.entryPrice))

        if market_spec and entry_notional < Decimal(str(market_spec.minOrderSizeUsd)):
            raise RuntimeError(
                f"{signal.symbol} order notional {entry_notional} is below Pacifica min order size."
            )

        payload: dict[str, object] = {
            "symbol": signal.symbol,
            "amount": size,
            "side": "bid" if signal.bias == "long" else "ask",
            "slippage_percent": self._normalize_decimal(self.settings.slippagePercent),
            "reduce_only": False,
            "client_order_id": signal.id,
            "take_profit": {
                "stop_price": self._format_price(
                    signal.takeProfit,
                    market_spec.tickSize if market_spec else None,
                ),
                "client_order_id": str(uuid4()),
            },
            "stop_loss": {
                "stop_price": self._format_price(
                    signal.stopLoss,
                    market_spec.tickSize if market_spec else None,
                ),
                "client_order_id": str(uuid4()),
            },
        }

        if self.settings.pacificaBuilderCode:
            payload["builder_code"] = self.settings.pacificaBuilderCode

        return payload

    def build_position_tpsl_payload(
        self,
        signal: StrategySignal,
        market_specs: dict[str, MarketSpec],
    ) -> dict[str, object]:
        market_spec = market_specs.get(signal.symbol)
        return {
            "symbol": signal.symbol,
            "side": "bid" if signal.bias == "long" else "ask",
            "take_profit": {
                "stop_price": self._format_price(
                    signal.takeProfit,
                    market_spec.tickSize if market_spec else None,
                ),
                "client_order_id": str(uuid4()),
            },
            "stop_loss": {
                "stop_price": self._format_price(
                    signal.stopLoss,
                    market_spec.tickSize if market_spec else None,
                ),
                "client_order_id": str(uuid4()),
            },
        }

    def health(self) -> ServiceHealth:
        status, message = self._readiness_status()
        return ServiceHealth(
            id="execution",
            label="Execution",
            status=status,
            message=message,
        )

    def _validate_execution_readiness(self) -> None:
        if not self._effective_account_address():
            raise RuntimeError("PACIFICA_ACCOUNT_ADDRESS is required for live execution.")
        if not self.settings.pacificaAgentPrivateKey:
            raise RuntimeError("PACIFICA_AGENT_PRIVATE_KEY is required for live execution.")

    async def _sync_position_tpsl(
        self,
        signal: StrategySignal,
        market_specs: dict[str, MarketSpec],
    ) -> str:
        payload = self.build_position_tpsl_payload(signal, market_specs)
        last_error: str | None = None

        for attempt in range(3):
            try:
                await self.client.create_position_tpsl(
                    payload,
                    account=self._effective_account_address(),
                )
                self.lastError = None
                return "TP/SL protection synced on Pacifica."
            except Exception as exc:
                last_error = str(exc)
                if attempt < 2:
                    await asyncio.sleep(0.35 * (attempt + 1))

        warning = (
            "Pacifica accepted the entry order, but TP/SL protection could not be confirmed. "
            f"Last sync error: {last_error}"
        )
        self.lastError = warning
        return warning

    def _readiness_status(self) -> tuple[SystemStatus, str]:
        if not self.settings.enableLiveTrading:
            if self.settings.botMode == "testnet":
                return (
                    "degraded",
                    "Testnet observation mode is active. Pacifica prices and account sync can run, but signed testnet orders are still disabled.",
                )
            return "degraded", "Paper mode is active. Live orders are disabled."
        if not self._effective_account_address():
            return "degraded", "Live mode requested but no Pacifica account is configured."
        if not self.settings.pacificaAgentPrivateKey:
            return "degraded", "Live mode requested but no API Agent Key is configured."
        if self.lastError:
            return "degraded", self.lastError
        if self.remoteSnapshot:
            return "healthy", "Execution is ready and account state is synced."
        return "degraded", "Execution credentials are set. Waiting for account sync."

    def _effective_account_address(self) -> str | None:
        return self.sessionAccountAddress or self.settings.pacificaAccountAddress

    def _parse_remote_position(self, payload: dict[str, object]) -> RemotePositionSnapshot:
        side = self._normalize_position_side(payload.get("side"))
        size = float(payload.get("amount", 0.0))
        entry_price = float(payload.get("entry_price", 0.0))
        return RemotePositionSnapshot(
            symbol=str(payload.get("symbol", "")),
            side=side,
            size=size,
            entryPrice=entry_price,
            notionalUsd=round(abs(size * entry_price), 2),
            marginUsd=self._optional_float(payload.get("margin")),
            fundingUsd=self._optional_float(payload.get("funding")),
            isolated=bool(payload.get("isolated", False)),
            openedAt=self._parse_timestamp(payload.get("created_at")),
            updatedAt=self._parse_timestamp(payload.get("updated_at")),
        )

    def _parse_remote_open_order(self, payload: dict[str, object]) -> RemoteOpenOrderSnapshot:
        initial_amount = float(payload.get("initial_amount", 0.0))
        filled_amount = float(payload.get("filled_amount", 0.0))
        cancelled_amount = float(payload.get("cancelled_amount", 0.0))
        remaining_amount = max(initial_amount - filled_amount - cancelled_amount, 0.0)
        price = float(payload.get("price", 0.0))
        return RemoteOpenOrderSnapshot(
            orderId=int(payload.get("order_id", 0)),
            clientOrderId=str(payload["client_order_id"]) if payload.get("client_order_id") else None,
            symbol=str(payload.get("symbol", "")),
            side=self._normalize_order_side(payload.get("side")),
            orderType=str(payload.get("order_type", "unknown")),
            price=price,
            stopPrice=self._optional_float(payload.get("stop_price")),
            initialAmount=initial_amount,
            filledAmount=filled_amount,
            cancelledAmount=cancelled_amount,
            remainingAmount=remaining_amount,
            notionalUsd=round(abs(remaining_amount * price), 2),
            reduceOnly=bool(payload.get("reduce_only", False)),
            createdAt=self._parse_timestamp(payload.get("created_at")),
            updatedAt=self._parse_timestamp(payload.get("updated_at")),
        )

    def _normalize_position_side(self, side: object) -> str:
        if str(side).lower() == "ask":
            return "short"
        return "long"

    def _normalize_order_side(self, side: object) -> str:
        if str(side).lower() == "ask":
            return "sell"
        return "buy"

    def _format_amount(self, value: float, lot_size: float | None) -> str:
        return self._quantize(value, lot_size)

    def _format_price(self, value: float, tick_size: float | None) -> str:
        return self._quantize(value, tick_size)

    def _quantize(self, value: float, step: float | None) -> str:
        decimal_value = Decimal(str(value))
        if not step or step <= 0:
            return self._normalize_decimal(decimal_value)
        step_decimal = Decimal(str(step))
        units = (decimal_value / step_decimal).to_integral_value(rounding=ROUND_DOWN)
        quantized = units * step_decimal
        return self._normalize_decimal(quantized)

    def _normalize_decimal(self, value: Decimal | float) -> str:
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
        return format(decimal_value.normalize(), "f")

    def _optional_float(self, value: object) -> float | None:
        if value is None:
            return None
        return float(value)

    def _optional_int(self, value: object) -> int | None:
        if value is None:
            return None
        return int(value)

    def _optional_bool(self, value: object) -> bool | None:
        if value is None:
            return None
        return bool(value)

    def _parse_timestamp(self, value: object) -> datetime | None:
        if value is None:
            return None
        timestamp = int(value)
        if timestamp > 10_000_000_000:
            return datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
