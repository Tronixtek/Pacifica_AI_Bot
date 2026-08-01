from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN

from app.config import Settings
from app.contracts import ServiceHealth, StrategySignal, SystemStatus
from app.mt5.client import TRADE_RETCODE_DONE, Mt5Client
from app.mt5.models import (
    ExecutionResult,
    MarketSpec,
    RemoteAccountSnapshot,
    RemoteOpenOrderSnapshot,
    RemotePositionSnapshot,
    RemoteTradingSnapshot,
)


class Mt5ExecutionService:
    def __init__(self, settings: Settings, client: Mt5Client) -> None:
        self.settings = settings
        self.client = client
        self.lastAccountSyncAt: datetime | None = None
        self.lastError: str | None = None
        self.remoteSnapshot: RemoteTradingSnapshot | None = None

    async def sync_remote_account(
        self,
        market_specs: dict[str, MarketSpec] | None = None,
    ) -> RemoteTradingSnapshot | None:
        if not self.settings.mt5Login:
            return None

        account = await self.client.account_info()
        if account is None:
            self.lastError = "MT5 account_info() returned no data."
            return None

        positions = await self.client.positions_get()
        orders = await self.client.orders_get()
        magic = self.settings.mt5MagicNumber
        bot_positions = [position for position in positions if position.magic == magic]
        bot_orders = [order for order in orders if order.magic == magic]
        synced_at = datetime.now(timezone.utc)
        specs = market_specs or {}

        snapshot = RemoteTradingSnapshot(
            account=RemoteAccountSnapshot(
                equityUsd=float(account.equity),
                availableMarginUsd=float(account.margin_free),
                balanceUsd=float(account.balance),
                totalMarginUsedUsd=float(account.margin),
                marginLevelPct=float(account.margin_level) if account.margin_level else None,
                currency=account.currency,
                leverage=int(account.leverage),
                tradeAllowed=bool(account.trade_allowed),
                openPositions=len(bot_positions),
                openOrders=len(bot_orders),
                updatedAt=synced_at,
            ),
            positions=[self._parse_position(position, specs) for position in bot_positions],
            openOrders=[self._parse_order(order, specs) for order in bot_orders],
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
        order_request = self.build_order_request(signal, market_specs)

        if not self.settings.enableLiveTrading:
            return ExecutionResult(
                accepted=False,
                message="Live trading is disabled. Order prepared but not submitted.",
                payload=order_request,
            )

        if self.settings.botMode not in {"demo", "live"}:
            return ExecutionResult(
                accepted=False,
                message="Bot mode is not configured for MT5 execution.",
                payload=order_request,
            )

        self._validate_execution_readiness()

        drift_error = await self._reject_on_entry_drift(signal)
        if drift_error is not None:
            self.lastError = drift_error
            return ExecutionResult(accepted=False, message=drift_error, payload=order_request)

        response = await self.client.send_market_order(order_request)
        retcode = response.get("retcode")

        if retcode is None:
            message = f"MT5 order_send failed for {signal.symbol}: {response.get('error')}"
            self.lastError = message
            return ExecutionResult(accepted=False, message=message, payload=order_request, response=response)

        if retcode != TRADE_RETCODE_DONE:
            message = (
                f"MT5 rejected the order for {signal.symbol} (retcode {retcode}): "
                f"{response.get('comment')}"
            )
            self.lastError = message
            return ExecutionResult(accepted=False, message=message, payload=order_request, response=response)

        self.lastError = None
        order_id = response.get("order")
        return ExecutionResult(
            accepted=True,
            message=f"Submitted MT5 market order for {signal.symbol}. Order id {order_id}.",
            payload=order_request,
            response=response,
            orderId=int(order_id) if order_id else None,
        )

    def build_order_request(
        self,
        signal: StrategySignal,
        market_specs: dict[str, MarketSpec],
    ) -> dict[str, object]:
        market_spec = market_specs.get(signal.symbol)
        if market_spec is None:
            raise RuntimeError(
                f"No MT5 symbol spec available for {signal.symbol}. Is it selected in Market Watch?"
            )

        # `signal.size` is already lots: the risk layer sized it against the
        # terminal's own profit calculation. Re-deriving volume from a notional
        # value here would undo that and reintroduce the currency-conversion
        # error on pairs whose profit currency is not the account currency.
        volume = self._quantize_volume(signal.size, market_spec)
        if volume <= 0:
            raise RuntimeError(f"Computed MT5 volume for {signal.symbol} rounds down to zero lots.")

        self._validate_stop_distance(signal, market_spec)

        return {
            "symbol": signal.symbol,
            "volume": volume,
            "side": "buy" if signal.bias == "long" else "sell",
            "sl": round(signal.stopLoss, market_spec.digits),
            "tp": round(signal.takeProfit, market_spec.digits),
            "deviation": self.settings.mt5DeviationPoints,
            "magic": self.settings.mt5MagicNumber,
            "comment": f"vtfx-{signal.id[:16]}",
        }

    async def _reject_on_entry_drift(self, signal: StrategySignal) -> str | None:
        """Refuse the trade if price has left the geometry the signal assumed.

        Stop and target are fixed at the signal's entry price, but a market
        order fills at whatever the market is doing now. When price drifts, the
        levels do not follow: the reward shrinks and the risk widens, silently
        changing a trade the risk layer already approved. Observed live, 26
        points of drift on a 30 point reward turned a 0.60 reward-to-risk setup
        into 0.05.

        Returns an error message when the trade should be abandoned, else None.
        """
        risk = abs(signal.entryPrice - signal.stopLoss)
        if risk <= 0:
            return None

        quote = await self.client.symbol_info_tick(signal.symbol)
        if quote is None:
            return f"No live price for {signal.symbol}; refusing to submit blind."

        # Compare against the side actually paid: ask to buy, bid to sell.
        live = (quote.askPrice if signal.bias == "long" else quote.bidPrice) or quote.markPrice
        drift = abs(live - signal.entryPrice)
        budget = risk * self.settings.maxEntryDriftRiskFraction
        if drift <= budget:
            return None

        return (
            f"{signal.symbol} price moved {drift / risk:.0%} of the intended risk "
            f"({signal.entryPrice} -> {live}) before execution, past the "
            f"{self.settings.maxEntryDriftRiskFraction:.0%} limit. Trade abandoned "
            "rather than taken on degraded stop/target geometry."
        )

    def _validate_stop_distance(self, signal: StrategySignal, market_spec: MarketSpec) -> None:
        """Reject stops or targets closer than the broker permits.

        Deliberately a rejection rather than a nudge outward: widening the stop
        silently increases the loss the position can take beyond what the risk
        layer sized for, which is worse than not trading.
        """
        if market_spec.stopsLevel <= 0 or market_spec.tickSize <= 0:
            return

        minimum = market_spec.stopsLevel * market_spec.tickSize
        for label, level in (("stop loss", signal.stopLoss), ("take profit", signal.takeProfit)):
            if not level:
                continue
            distance = abs(signal.entryPrice - level)
            if distance < minimum:
                raise RuntimeError(
                    f"{signal.symbol} {label} is {distance / market_spec.tickSize:.0f} points from "
                    f"entry, inside the broker minimum of {market_spec.stopsLevel} points."
                )

    def health(self) -> ServiceHealth:
        status, message = self._readiness_status()
        return ServiceHealth(id="execution", label="Execution", status=status, message=message)

    def _quantize_volume(self, volume: float, market_spec: MarketSpec) -> float:
        step = market_spec.volumeStep or 0.01
        step_decimal = Decimal(str(step))
        steps = (Decimal(str(volume)) / step_decimal).to_integral_value(rounding=ROUND_DOWN)
        quantized = steps * step_decimal

        volume_min = Decimal(str(market_spec.volumeMin))
        if quantized < volume_min:
            # Rounding down below the broker's minimum lot means this risk-sized trade
            # cannot be placed safely. Reject it (via the volume<=0 check in the caller)
            # rather than silently bumping it up to volumeMin, which would inflate the
            # risk manager's intended position size.
            return 0.0

        clamped = min(quantized, Decimal(str(market_spec.volumeMax)))
        return float(clamped)

    def _validate_execution_readiness(self) -> None:
        if not self.settings.mt5Login:
            raise RuntimeError("MT5_LOGIN is required for live execution.")
        if not self.client.connected:
            raise RuntimeError("MT5 terminal connection is not established.")

    def _readiness_status(self) -> tuple[SystemStatus, str]:
        if not self.settings.enableLiveTrading:
            if self.settings.botMode == "demo":
                return (
                    "degraded",
                    "Demo observation mode is active. MT5 prices and account sync can run, but signed demo orders are still disabled.",
                )
            return "degraded", "Paper mode is active. Live orders are disabled."
        if not self.settings.mt5Login:
            return "degraded", "Live mode requested but no MT5 login is configured."
        if not self.client.connected:
            return "degraded", "Live mode requested but the MT5 terminal is not connected."
        if self.lastError:
            return "degraded", self.lastError
        if self.remoteSnapshot:
            return "healthy", "Execution is ready and account state is synced."
        return "degraded", "Execution credentials are set. Waiting for account sync."

    def _parse_position(
        self,
        position: object,
        market_specs: dict[str, MarketSpec],
    ) -> RemotePositionSnapshot:
        side = "long" if position.type == 0 else "short"  # type: ignore[attr-defined]
        contract_size = self._contract_size(position.symbol, market_specs)  # type: ignore[attr-defined]
        return RemotePositionSnapshot(
            ticket=position.ticket,  # type: ignore[attr-defined]
            symbol=position.symbol,  # type: ignore[attr-defined]
            side=side,
            size=position.volume,  # type: ignore[attr-defined]
            entryPrice=position.price_open,  # type: ignore[attr-defined]
            stopLoss=position.sl or None,  # type: ignore[attr-defined]
            takeProfit=position.tp or None,  # type: ignore[attr-defined]
            notionalUsd=round(abs(position.volume * position.price_open * contract_size), 2),  # type: ignore[attr-defined]
            swapUsd=position.swap,  # type: ignore[attr-defined]
            profitUsd=position.profit,  # type: ignore[attr-defined]
            openedAt=(
                datetime.fromtimestamp(position.time, tz=timezone.utc)  # type: ignore[attr-defined]
                if getattr(position, "time", None)
                else None
            ),
            updatedAt=(
                datetime.fromtimestamp(position.time_update, tz=timezone.utc)  # type: ignore[attr-defined]
                if getattr(position, "time_update", None)
                else None
            ),
        )

    def _parse_order(
        self,
        order: object,
        market_specs: dict[str, MarketSpec],
    ) -> RemoteOpenOrderSnapshot:
        order_type = order.type  # type: ignore[attr-defined]
        side = self.client.order_side_from_type(order_type)
        contract_size = self._contract_size(order.symbol, market_specs)  # type: ignore[attr-defined]
        return RemoteOpenOrderSnapshot(
            orderId=order.ticket,  # type: ignore[attr-defined]
            symbol=order.symbol,  # type: ignore[attr-defined]
            side=side,
            orderType=self.client.order_type_label(order_type),
            price=order.price_open,  # type: ignore[attr-defined]
            stopPrice=getattr(order, "price_stoplimit", None) or None,
            volume=order.volume_initial,  # type: ignore[attr-defined]
            volumeRemaining=order.volume_current,  # type: ignore[attr-defined]
            notionalUsd=round(abs(order.volume_current * order.price_open * contract_size), 2),  # type: ignore[attr-defined]
            createdAt=(
                datetime.fromtimestamp(order.time_setup, tz=timezone.utc)  # type: ignore[attr-defined]
                if getattr(order, "time_setup", None)
                else None
            ),
            updatedAt=None,
        )

    def _contract_size(self, symbol: str, market_specs: dict[str, MarketSpec]) -> float:
        spec = market_specs.get(symbol)
        return spec.contractSize if spec is not None and spec.contractSize else 1.0
