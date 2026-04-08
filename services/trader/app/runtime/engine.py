from __future__ import annotations

import asyncio
from dataclasses import dataclass
from importlib.util import find_spec
import random
from datetime import datetime, timezone
from statistics import mean
from uuid import uuid4

from app.config import Settings
from app.contracts import (
    AccountLinkResponse,
    ConfigReadiness,
    DiagnosticProbe,
    DiagnosticsResponse,
    EnginePosition,
    ExecutionMode,
    HealthResponse,
    OperatorActionResponse,
    OperatorSnapshot,
    ServiceHealth,
    SignalStatus,
    SignalPreviewResponse,
    StrategySignal,
    SystemStatus,
)
from app.core.audit import AuditLogger
from app.pacifica.client import PacificaClient
from app.pacifica.execution import PacificaExecutionService
from app.pacifica.market_data import PacificaMarketDataService
from app.pacifica.models import (
    RemoteOpenOrderSnapshot as PacificaRemoteOpenOrderSnapshot,
    RemotePositionSnapshot as PacificaRemotePositionSnapshot,
    RemoteTradingSnapshot,
)
from app.risk.manager import RiskManager
from app.runtime.persistence import RuntimeStateStore
from app.runtime.state import ComparisonPaperPosition, EngineRuntimeState, SymbolState
from app.strategy.ml_model import MlAssessment, MlSignalModel
from app.strategy.price_action import PriceActionStrategy, StrategyCandidate


@dataclass(slots=True)
class _ExecutionRiskBook:
    startingEquityUsd: float
    realizedPnlUsd: float
    positions: dict[str, object]
    currentEquityUsd: float
    availableMarginUsd: float


class TradingEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.state = EngineRuntimeState(startingEquityUsd=settings.startingEquityUsd)
        self.client = PacificaClient(settings)
        self.marketData = PacificaMarketDataService(settings, self.client)
        self.execution = PacificaExecutionService(settings, self.client)
        self.strategy = PriceActionStrategy(
            breakout_window=settings.priceActionBreakoutWindow,
            sweep_window=settings.priceActionSweepWindow,
            trend_fast_window=settings.priceActionTrendFastWindow,
            trend_slow_window=settings.priceActionTrendSlowWindow,
            momentum_window=settings.priceActionMomentumWindow,
            breakout_buffer=settings.priceActionBreakoutBuffer,
            reward_to_risk=settings.priceActionRewardToRisk,
        )
        self.mlModel = MlSignalModel(settings, self.client)
        self.audit = AuditLogger(settings)
        self.risk = RiskManager(settings)
        self.stateStore = RuntimeStateStore(settings)
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._paused = False
        self._lastSignalAt: dict[str, datetime] = {}
        self._lastNoticeAt: dict[str, datetime] = {}
        self._lastAccountSyncAttemptAt: datetime | None = None
        self._lastOperatorAction: str | None = None
        self._lastOperatorActionAt: datetime | None = None
        self._sessionAccountAddress: str | None = None
        self._lastCheckpointAt: datetime | None = None

    async def start(self) -> None:
        restored = self._restore_runtime_state()
        self.state.bootstrap_markets(self.settings.symbols)
        self.state.add_event(
            "info",
            f"Engine booted in {self.settings.botMode} mode on {self.settings.pacificaNetwork}.",
        )
        if restored:
            self.state.add_event("info", "Recovered durable runtime state from the state store.")
        self.audit.write(
            event_type="lifecycle",
            action="engine.start",
            status="success",
            details={
                "mode": self.settings.botMode,
                "network": self.settings.pacificaNetwork,
            },
        )
        if self.settings.pacificaBuilderCode:
            self.state.add_event(
                "info",
                f"Builder code {self.settings.pacificaBuilderCode} is configured for live execution.",
            )
        if self.settings.contrarianExecutionEnabled:
            self.state.add_event(
                "info",
                "Contrarian execution is enabled. Approved signals will flip direction and use the original stop as the profit target.",
            )
        await self._refresh_ml_model(force=True)
        if not self.settings.useSimulatedFeed:
            await self.marketData.start()
            market_spec_count = self.state.apply_market_specs(self.marketData.marketSpecs)
            if market_spec_count:
                self.state.add_event(
                    "info",
                    f"Loaded Pacifica market specs for {market_spec_count} symbols.",
                )
            await self._sync_remote_account_if_due(force=True)
        self._running = True
        self._checkpoint_state(force=True)
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._running = False
        self._checkpoint_state(force=True)
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self.marketData.stop()
        await self.client.close()
        self.audit.write(
            event_type="lifecycle",
            action="engine.stop",
            status="success",
        )

    def health(self) -> HealthResponse:
        service_health = self._service_health()
        status: SystemStatus = "healthy" if self._running else "offline"
        message = "Trader engine is healthy."

        degraded = [
            service
            for service in service_health
            if service.status != "healthy"
            and not (service.id == "execution" and not self.settings.enableLiveTrading)
        ]
        if degraded and self._running:
            status = "degraded"
            message = degraded[0].message

        return HealthResponse(
            status=status,
            mode=self.settings.botMode,
            network=self.settings.pacificaNetwork,
            liveTradingEnabled=self.settings.enableLiveTrading,
            message=message,
        )

    def dashboard_snapshot(self):
        return self.state.build_snapshot(
            self.settings,
            self._service_health(),
            self.operator_snapshot(),
            self.mlModel.snapshot(),
        )

    def operator_snapshot(self) -> OperatorSnapshot:
        account_address = self._effective_account_address()
        return OperatorSnapshot(
            paused=self._paused,
            canSyncAccount=bool(account_address),
            canPreviewOrders=True,
            canSubmitOrders=(
                self.settings.enableLiveTrading
                and self.settings.botMode in {"testnet", "mainnet"}
                and bool(account_address)
                and bool(self.settings.pacificaAgentPrivateKey)
            ),
            lastAction=self._lastOperatorAction,
            lastActionAt=self._lastOperatorActionAt,
        )

    def pause(self) -> OperatorActionResponse:
        if self._paused:
            return OperatorActionResponse(
                ok=True,
                message="Strategy scanning is already paused.",
                operator=self.operator_snapshot(),
            )

        self._paused = True
        self._record_operator_action("Paused strategy scanning from operator console.")
        self.state.add_event("warning", "Operator paused strategy scanning.")
        self._audit_operator_action("operator.pause", "success")
        self._checkpoint_state(force=True)
        return OperatorActionResponse(
            ok=True,
            message="Strategy scanning is paused. Market/account updates will keep flowing.",
            operator=self.operator_snapshot(),
        )

    def resume(self) -> OperatorActionResponse:
        if not self._paused:
            return OperatorActionResponse(
                ok=True,
                message="Strategy scanning is already active.",
                operator=self.operator_snapshot(),
            )

        self._paused = False
        self._record_operator_action("Resumed strategy scanning from operator console.")
        self.state.add_event("success", "Operator resumed strategy scanning.")
        self._audit_operator_action("operator.resume", "success")
        self._checkpoint_state(force=True)
        return OperatorActionResponse(
            ok=True,
            message="Strategy scanning is active again.",
            operator=self.operator_snapshot(),
        )

    def reset_paper_account(self) -> OperatorActionResponse:
        if self.settings.botMode != "paper":
            return OperatorActionResponse(
                ok=False,
                message="Paper balance controls are only available while the bot is in paper mode.",
                operator=self.operator_snapshot(),
            )

        cleared_positions = len(self.state.positions)
        paper_account = self.state.reset_paper_account(self.settings.startingEquityUsd)
        self._record_operator_action("Reset paper testing balance from operator console.")
        self.state.add_event(
            "success",
            (
                f"Paper testing balance reset to {paper_account.equityUsd:.2f} USD."
                f" Cleared {cleared_positions} open paper positions."
            ),
        )
        self._audit_operator_action(
            "operator.paper_balance.reset",
            "success",
            details={
                "equityUsd": paper_account.equityUsd,
                "clearedPositions": cleared_positions,
            },
        )
        self._checkpoint_state(force=True)
        return OperatorActionResponse(
            ok=True,
            message=(
                f"Paper testing balance reset to {paper_account.equityUsd:.2f} USD."
                if cleared_positions == 0
                else (
                    f"Paper testing balance reset to {paper_account.equityUsd:.2f} USD"
                    f" and {cleared_positions} paper position(s) were cleared."
                )
            ),
            operator=self.operator_snapshot(),
        )

    def top_up_paper_account(self, amount_usd: float) -> OperatorActionResponse:
        if self.settings.botMode != "paper":
            return OperatorActionResponse(
                ok=False,
                message="Paper balance top-ups are only available while the bot is in paper mode.",
                operator=self.operator_snapshot(),
            )

        if amount_usd <= 0:
            return OperatorActionResponse(
                ok=False,
                message="Top-up amount must be greater than zero.",
                operator=self.operator_snapshot(),
            )

        paper_account = self.state.top_up_paper_account(amount_usd)
        self._record_operator_action("Added paper testing capital from operator console.")
        self.state.add_event(
            "success",
            (
                f"Added {amount_usd:.2f} USD to the paper testing balance."
                f" New paper equity: {paper_account.equityUsd:.2f} USD."
            ),
        )
        self._audit_operator_action(
            "operator.paper_balance.top_up",
            "success",
            details={
                "amountUsd": amount_usd,
                "equityUsd": paper_account.equityUsd,
            },
        )
        self._checkpoint_state(force=True)
        return OperatorActionResponse(
            ok=True,
            message=(
                f"Added {amount_usd:.2f} USD to the paper testing balance."
                f" New paper equity is {paper_account.equityUsd:.2f} USD."
            ),
            operator=self.operator_snapshot(),
        )

    def link_account(self, account_address: str) -> AccountLinkResponse:
        normalized = account_address.strip()
        if not self._looks_like_account_address(normalized):
            return AccountLinkResponse(
                ok=False,
                message="That does not look like a valid Pacifica account address.",
                operator=self.operator_snapshot(),
                linkedAccountAddress=self._effective_account_address(),
                accountConfigurationSource=self._account_configuration_source(),
            )

        self._sessionAccountAddress = normalized
        self.execution.sessionAccountAddress = normalized
        self.state.remoteSnapshot = None
        self.execution.remoteSnapshot = None
        self.execution.lastAccountSyncAt = None
        self._lastAccountSyncAttemptAt = None
        self._record_operator_action("Linked Pacifica account from onboarding flow.")
        self.state.add_event("success", f"Linked Pacifica account {self._shorten_account(normalized)}.")
        self._audit_operator_action(
            "operator.account.link",
            "success",
            details={"accountAddress": normalized},
        )
        self._checkpoint_state(force=True)
        return AccountLinkResponse(
            ok=True,
            message="Pacifica account linked for this session.",
            operator=self.operator_snapshot(),
            linkedAccountAddress=normalized,
            accountConfigurationSource=self._account_configuration_source(),
        )

    def unlink_account(self) -> AccountLinkResponse:
        if self._sessionAccountAddress is None:
            return AccountLinkResponse(
                ok=True,
                message="No session-linked Pacifica account was active.",
                operator=self.operator_snapshot(),
                linkedAccountAddress=self._effective_account_address(),
                accountConfigurationSource=self._account_configuration_source(),
            )

        previous = self._sessionAccountAddress
        self._sessionAccountAddress = None
        self.execution.sessionAccountAddress = None
        self.state.remoteSnapshot = None
        self.execution.remoteSnapshot = None
        self.execution.lastAccountSyncAt = None
        self._lastAccountSyncAttemptAt = None
        self._record_operator_action("Unlinked Pacifica account from onboarding flow.")
        self.state.add_event(
            "warning",
            f"Removed session-linked Pacifica account {self._shorten_account(previous)}.",
        )
        self._audit_operator_action(
            "operator.account.unlink",
            "success",
            details={"accountAddress": previous},
        )
        self._checkpoint_state(force=True)
        return AccountLinkResponse(
            ok=True,
            message=(
                "Session-linked Pacifica account removed."
                if not self.settings.pacificaAccountAddress
                else "Session-linked account removed. Falling back to the env-configured account."
            ),
            operator=self.operator_snapshot(),
            linkedAccountAddress=self._effective_account_address(),
            accountConfigurationSource=self._account_configuration_source(),
        )

    async def force_account_sync(self) -> OperatorActionResponse:
        if not self._effective_account_address():
            return OperatorActionResponse(
                ok=False,
                message="No Pacifica account address is configured yet.",
                operator=self.operator_snapshot(),
            )

        try:
            self._lastAccountSyncAttemptAt = datetime.now(timezone.utc)
            snapshot = await self.execution.sync_remote_account()
            if snapshot is not None:
                self._apply_remote_snapshot(snapshot)
            self._record_operator_action("Forced Pacifica account sync from operator console.")
            self._audit_operator_action("operator.account.sync", "success")
            self._checkpoint_state(force=True)
            return OperatorActionResponse(
                ok=True,
                message="Pacifica account sync completed.",
                operator=self.operator_snapshot(),
            )
        except Exception as exc:
            self.execution.lastError = f"Manual account sync failed: {exc}"
            self.state.add_event("warning", f"Manual Pacifica account sync failed: {exc}")
            self._audit_operator_action(
                "operator.account.sync",
                "failure",
                details={"error": str(exc)},
            )
            return OperatorActionResponse(
                ok=False,
                message=f"Pacifica account sync failed: {exc}",
                operator=self.operator_snapshot(),
            )

    def preview_signal(self, signal_id: str) -> SignalPreviewResponse:
        signal = self._find_signal(signal_id)
        if signal is None:
            return SignalPreviewResponse(
                ok=False,
                message="Signal not found. Refresh the dashboard and try again.",
                operator=self.operator_snapshot(),
            )

        if signal.status == "blocked":
            return SignalPreviewResponse(
                ok=False,
                message="Blocked signals cannot be previewed for execution.",
                operator=self.operator_snapshot(),
                signal=signal,
            )

        try:
            payload = self.execution.build_market_order_payload(signal, self.marketData.marketSpecs)
        except Exception as exc:
            return SignalPreviewResponse(
                ok=False,
                message=f"Unable to build market order payload: {exc}",
                operator=self.operator_snapshot(),
                signal=signal,
            )

        self._record_operator_action(f"Previewed signal {signal.symbol} {signal.setup}.")
        self._audit_operator_action(
            "operator.signal.preview",
            "success",
            details={
                "signalId": signal.id,
                "symbol": signal.symbol,
                "setup": signal.setup,
            },
        )
        return SignalPreviewResponse(
            ok=True,
            message=(
                "Execution preview is ready."
                if self.settings.enableLiveTrading
                else "Preview built in safe mode. Live submission is still disabled."
            ),
            operator=self.operator_snapshot(),
            signal=signal,
            payload=payload,
            marketSpecApplied=signal.symbol in self.marketData.marketSpecs,
        )

    async def submit_smoke_test_order(self, symbol: str) -> OperatorActionResponse:
        normalized_symbol = symbol.strip().upper()
        if self.settings.botMode != "testnet":
            return OperatorActionResponse(
                ok=False,
                message="Manual smoke-test orders are only enabled in testnet mode.",
                operator=self.operator_snapshot(),
            )

        if not self.settings.enableLiveTrading:
            return OperatorActionResponse(
                ok=False,
                message="Live trading is disabled. Enable testnet execution before sending a smoke-test order.",
                operator=self.operator_snapshot(),
            )

        if normalized_symbol not in self.settings.symbols:
            return OperatorActionResponse(
                ok=False,
                message=f"{normalized_symbol} is not in the configured symbol watchlist.",
                operator=self.operator_snapshot(),
            )

        if not self._effective_account_address():
            return OperatorActionResponse(
                ok=False,
                message="No Pacifica account is configured for live execution.",
                operator=self.operator_snapshot(),
            )

        if not self.settings.pacificaAgentPrivateKey:
            return OperatorActionResponse(
                ok=False,
                message="No Pacifica API agent key is configured for live execution.",
                operator=self.operator_snapshot(),
            )

        await self._sync_remote_account_if_due(force=True)
        remote_snapshot = self.state.remoteSnapshot
        if remote_snapshot is not None:
            if any(position.symbol == normalized_symbol for position in remote_snapshot.positions):
                return OperatorActionResponse(
                    ok=False,
                    message=(
                        f"{normalized_symbol} already has an open Pacifica position. "
                        "Close it before sending a smoke-test order."
                    ),
                    operator=self.operator_snapshot(),
                )

            if any(order.symbol == normalized_symbol for order in remote_snapshot.openOrders):
                return OperatorActionResponse(
                    ok=False,
                    message=(
                        f"{normalized_symbol} already has a pending Pacifica order. "
                        "Wait for it to resolve before sending a smoke-test order."
                    ),
                    operator=self.operator_snapshot(),
                )

            live_exposure_count = len(remote_snapshot.positions) + len(remote_snapshot.openOrders)
            if live_exposure_count >= self.settings.maxOpenPositions:
                return OperatorActionResponse(
                    ok=False,
                    message="Live exposure is already at the configured max-open-position limit.",
                    operator=self.operator_snapshot(),
                )

        quotes = await self.marketData.refresh_quotes([normalized_symbol])
        quote = quotes.get(normalized_symbol)
        market = self.state.markets.get(normalized_symbol)
        entry_price = quote.markPrice if quote is not None else (market.lastPrice if market else 0.0)
        if entry_price <= 0:
            return OperatorActionResponse(
                ok=False,
                message=f"No fresh market price is available for {normalized_symbol} yet.",
                operator=self.operator_snapshot(),
            )

        if normalized_symbol not in self.marketData.marketSpecs:
            market_specs = await self.client.get_market_info([normalized_symbol])
            self.marketData.marketSpecs.update(market_specs)
            self.state.apply_market_specs(self.marketData.marketSpecs)

        market_spec = self.marketData.marketSpecs.get(normalized_symbol)
        if market_spec is None:
            return OperatorActionResponse(
                ok=False,
                message=f"Pacifica market specs are not available for {normalized_symbol}.",
                operator=self.operator_snapshot(),
            )

        target_notional = max(
            market_spec.minOrderSizeUsd * 1.25,
            market_spec.minOrderSizeUsd + 2.0,
        )
        leverage_limit = max(
            1.0,
            min(float(market_spec.maxLeverage), self.settings.defaultLeverage),
        )
        available_margin = (
            self.state.remoteAccount.availableMarginUsd
            if self.state.remoteAccount is not None
            else self.state.availableMarginUsd
        )
        required_margin = target_notional / leverage_limit
        if available_margin < required_margin:
            return OperatorActionResponse(
                ok=False,
                message=(
                    f"Available Pacifica margin is too low for a {normalized_symbol} smoke-test order. "
                    f"Need about {required_margin:.2f} USD."
                ),
                operator=self.operator_snapshot(),
            )

        observed_spread_bps = 0.0
        if quote is not None and quote.spreadBps > 0:
            observed_spread_bps = quote.spreadBps
        elif market is not None:
            observed_spread_bps = market.spreadBps
        base_move_pct = max((observed_spread_bps / 10_000) * 8, 0.0025)
        stop_loss = entry_price * (1 - base_move_pct)
        take_profit = entry_price * (1 + (base_move_pct * 1.2))
        signal = StrategySignal(
            id=str(uuid4()),
            symbol=normalized_symbol,
            setup="manual_test",
            bias="long",
            confidence=0.99,
            entryPrice=round(entry_price, 4),
            stopLoss=round(stop_loss, 4),
            takeProfit=round(take_profit, 4),
            size=round(target_notional / entry_price, 6),
            notionalUsd=round(target_notional, 2),
            status="approved",
            reason=(
                "Manual testnet smoke order requested from the operator console. "
                "This bypasses the strategy queue so execution plumbing can be verified immediately."
            ),
            createdAt=datetime.now(timezone.utc),
        )

        try:
            result = await self.execution.execute_signal(signal, self.marketData.marketSpecs)
        except Exception as exc:
            self.execution.lastError = str(exc)
            self._record_operator_action(
                f"Manual smoke-test order failed for {normalized_symbol}."
            )
            self.state.add_event(
                "warning",
                f"{normalized_symbol} manual smoke-test execution failed: {exc}",
            )
            self._audit_operator_action(
                "operator.test_order.submit",
                "failure",
                details={"symbol": normalized_symbol, "error": str(exc)},
            )
            self._checkpoint_state(force=True)
            return OperatorActionResponse(
                ok=False,
                message=f"Manual smoke-test execution failed: {exc}",
                operator=self.operator_snapshot(),
            )

        signal.status = "executed" if result.accepted else "blocked"
        signal.reason = result.message
        self.state.add_signal(signal)
        self.state.add_trade_activity(
            kind="live_execution_submitted" if result.accepted else "live_execution_failed",
            symbol=signal.symbol,
            title=(
                f"{signal.symbol} manual test order sent"
                if result.accepted
                else f"{signal.symbol} manual test order failed"
            ),
            message=result.message,
            level="success" if result.accepted else "warning",
            side=signal.bias,
            price=signal.entryPrice,
            size=signal.size,
            notional_usd=signal.notionalUsd,
            signal_id=signal.id,
            order_id=result.orderId,
        )
        self.state.add_event(
            "success" if result.accepted else "warning",
            result.message,
        )
        if result.accepted:
            await self._sync_remote_account_if_due(force=True)
        self._record_operator_action(
            (
                f"Submitted manual smoke-test order for {normalized_symbol}."
                if result.accepted
                else f"Manual smoke-test order was rejected for {normalized_symbol}."
            )
        )
        self._audit_operator_action(
            "operator.test_order.submit",
            "success" if result.accepted else "failure",
            details={
                "symbol": normalized_symbol,
                "accepted": result.accepted,
                "orderId": result.orderId,
                "notionalUsd": signal.notionalUsd,
            },
        )
        self._checkpoint_state(force=True)
        return OperatorActionResponse(
            ok=result.accepted,
            message=result.message,
            operator=self.operator_snapshot(),
        )

    async def diagnostics(self, live_probe: bool = False) -> DiagnosticsResponse:
        account_address = self._effective_account_address()
        probes: list[DiagnosticProbe] = []
        probes.append(self._config_probe())
        probes.append(self._dependency_probe())
        probes.append(self._cache_probe())

        if live_probe:
            probes.extend(await self._live_probes())
        else:
            probes.append(
                DiagnosticProbe(
                    id="live_probe",
                    label="Live Pacifica Probe",
                    status="skipped",
                    message="Live REST probes were not requested.",
                )
            )

        return DiagnosticsResponse(
            generatedAt=datetime.now(timezone.utc),
            config=ConfigReadiness(
                mode=self.settings.botMode,
                network=self.settings.pacificaNetwork,
                restUrl=self.settings.pacificaRestUrl,
                websocketUrl=self.settings.pacificaWsUrl,
                useSimulatedFeed=self.settings.useSimulatedFeed,
                preferWebsocketFeed=self.settings.preferWebsocketFeed,
                liveTradingEnabled=self.settings.enableLiveTrading,
                accountConfigured=bool(account_address),
                effectiveAccountAddress=account_address,
                accountConfigurationSource=self._account_configuration_source(),
                agentKeyConfigured=bool(self.settings.pacificaAgentPrivateKey),
                apiConfigKeyConfigured=bool(self.settings.pacificaApiConfigKey),
                builderCode=self.settings.pacificaBuilderCode,
                symbols=self.settings.symbols,
            ),
            services=self._service_health(),
            probes=probes,
        )

    async def _run_loop(self) -> None:
        while self._running:
            await self._tick()
            await asyncio.sleep(self.settings.pollIntervalSec)

    async def _tick(self) -> None:
        await self._refresh_ml_model()
        if self.settings.useSimulatedFeed:
            for market in self.state.markets.values():
                self._simulate_market_tick(market)
                self._handle_positions(market)
                if not self._paused:
                    await self._scan_market(market)
            self._checkpoint_state()
            return

        quotes = await self.marketData.refresh_quotes(self.settings.symbols)
        if not quotes:
            self._emit_notice(
                key="market-data-empty",
                level="warning",
                message="Pacifica market data is not available yet. Waiting for a fresh snapshot.",
                cooldown_seconds=60,
            )
            await self._sync_remote_account_if_due()
            self._checkpoint_state()
            return

        for symbol in self.settings.symbols:
            quote = quotes.get(symbol)
            if quote is None:
                continue
            self.state.ingest_quote(quote)
            market = self.state.markets.get(symbol)
            if market is None:
                continue
            self._handle_positions(market)
            if not self._paused:
                await self._scan_market(market)

        await self._sync_remote_account_if_due()
        self._checkpoint_state()

    def _simulate_market_tick(self, market: SymbolState) -> None:
        history = list(market.priceHistory)
        momentum_bias = 0.0
        mean_reversion = 0.0

        if len(history) >= 12:
            fast_ma = mean(history[-6:])
            slow_ma = mean(history[-12:])
            momentum_bias = max(min((fast_ma - slow_ma) / max(slow_ma, 1.0), 0.0015), -0.0015)

        if len(history) >= 30:
            anchor = mean(history[-30:])
            stretch = (market.lastPrice - anchor) / max(anchor, 1.0)
            mean_reversion = -stretch * 0.18

        drift = random.gauss(
            market.lastPrice * (momentum_bias * 0.85 + mean_reversion * 0.35),
            market.lastPrice * 0.00055,
        )
        impulse = 0.0
        trigger = random.random()

        if trigger > 0.992:
            impulse = market.lastPrice * random.choice([0.0034, -0.0034])
        elif trigger > 0.972:
            impulse = market.lastPrice * random.choice([0.0018, -0.0018])

        next_price = max(market.lastPrice + drift + impulse, 1.0)
        next_spread = round(
            max(
                0.8,
                min(
                    3.2,
                    1.2 + abs(drift + impulse) / max(next_price, 1.0) * 8_000,
                ),
            ),
            2,
        )
        self.state.record_market_price(
            market.symbol,
            next_price,
            spread_bps=next_spread,
            observed_at=datetime.now(timezone.utc),
        )

    async def _scan_market(self, market: SymbolState) -> None:
        candidates = self.strategy.evaluate(market.symbol, list(market.priceHistory))
        for candidate in candidates:
            await self._handle_candidate(candidate)

    async def _handle_candidate(self, candidate: StrategyCandidate) -> None:
        key = f"{candidate.symbol}:{candidate.setup}:{candidate.bias}"
        now = datetime.now(timezone.utc)
        prior = self._lastSignalAt.get(key)
        if prior and (now - prior).total_seconds() < self.settings.signalCooldownSeconds:
            return

        ml_assessment = self._assess_candidate_with_ml(candidate)
        if ml_assessment.ready:
            candidate.confidence = round(
                (candidate.confidence * 0.58) + (ml_assessment.selectedProbability * 0.42),
                2,
            )
            candidate.reason = f"{candidate.reason} {ml_assessment.reason}"
            if not ml_assessment.approved:
                self._lastSignalAt[key] = now
                signal = StrategySignal(
                    id=str(uuid4()),
                    symbol=candidate.symbol,
                    setup=candidate.setup,
                    bias=candidate.bias,
                    confidence=round(candidate.confidence, 2),
                    entryPrice=round(candidate.entryPrice, 4),
                    stopLoss=round(candidate.stopLoss, 4),
                    takeProfit=round(candidate.takeProfit, 4),
                    size=0.0,
                    notionalUsd=0.0,
                    status="blocked",
                    reason=(
                        "ML classifier filtered this setup. "
                        f"{ml_assessment.reason}"
                    ),
                    createdAt=now,
                )
                self.state.add_signal(signal)
                self.state.add_event(
                    "warning",
                    f"{candidate.symbol} {candidate.setup} blocked: ML confidence rejected the setup.",
                )
                self._checkpoint_state(force=True)
                return

        self._lastSignalAt[key] = now
        execution_candidate, execution_note = self._apply_execution_policy(candidate)
        decision = self.risk.review(
            execution_candidate,
            self._execution_risk_book(),
        )
        comparison_candidate = self._build_comparison_candidate(execution_candidate)
        comparison_decision = (
            self.risk.review(comparison_candidate, self.state.comparison_risk_book())
            if self.settings.botMode == "paper"
            else None
        )
        status: SignalStatus = "approved" if decision.approved else "blocked"

        signal = StrategySignal(
            id=str(uuid4()),
            symbol=execution_candidate.symbol,
            setup=execution_candidate.setup,
            bias=execution_candidate.bias,
            confidence=round(execution_candidate.confidence, 2),
            entryPrice=round(execution_candidate.entryPrice, 4),
            stopLoss=round(execution_candidate.stopLoss, 4),
            takeProfit=round(execution_candidate.takeProfit, 4),
            size=decision.size,
            notionalUsd=decision.notionalUsd,
            status=status,
            reason=self._merge_signal_reason(decision.reason, execution_note),
            createdAt=now,
        )

        if decision.approved:
            if self.settings.enableLiveTrading and self.settings.botMode in {"testnet", "mainnet"}:
                try:
                    result = await self.execution.execute_signal(signal, self.marketData.marketSpecs)
                    signal.status = "executed" if result.accepted else "blocked"
                    signal.reason = self._merge_signal_reason(result.message, execution_note)
                    self.state.add_signal(signal)
                    if result.accepted:
                        self.state.add_trade_activity(
                            kind="live_execution_submitted",
                            symbol=signal.symbol,
                            title=f"{signal.symbol} {signal.bias} sent to Pacifica",
                            message=result.message,
                            level="success",
                            side=signal.bias,
                            price=signal.entryPrice,
                            size=signal.size,
                            notional_usd=signal.notionalUsd,
                            signal_id=signal.id,
                            order_id=result.orderId,
                        )
                    else:
                        self.state.add_trade_activity(
                            kind="live_execution_failed",
                            symbol=signal.symbol,
                            title=f"{signal.symbol} {signal.bias} was not submitted",
                            message=result.message,
                            level="warning",
                            side=signal.bias,
                            price=signal.entryPrice,
                            size=signal.size,
                            notional_usd=signal.notionalUsd,
                            signal_id=signal.id,
                        )
                    self.state.add_event(
                        "success" if result.accepted else "warning",
                        result.message,
                    )
                    if result.accepted:
                        await self._sync_remote_account_if_due(force=True)
                    self._checkpoint_state(force=True)
                except Exception as exc:
                    self.execution.lastError = str(exc)
                    signal.status = "blocked"
                    signal.reason = self._merge_signal_reason(
                        f"Execution failed: {exc}",
                        execution_note,
                    )
                    self.state.add_signal(signal)
                    self.state.add_trade_activity(
                        kind="live_execution_failed",
                        symbol=signal.symbol,
                        title=f"{signal.symbol} {signal.bias} execution failed",
                        message=str(exc),
                        level="warning",
                        side=signal.bias,
                        price=signal.entryPrice,
                        size=signal.size,
                        notional_usd=signal.notionalUsd,
                        signal_id=signal.id,
                    )
                    self.state.add_event(
                        "warning",
                        f"{candidate.symbol} {candidate.setup} execution failed: {exc}",
                    )
                    self._checkpoint_state(force=True)
            else:
                comparison_opened = False
                signal.status = "executed"
                approval_reason = (
                    "Risk approved and paper trade opened."
                    if not ml_assessment.ready
                    else f"Risk approved after ML confirmation. {ml_assessment.reason}"
                )
                signal.reason = self._merge_signal_reason(approval_reason, execution_note)
                self.state.add_signal(signal)
                self.state.open_position(
                    signal,
                    decision.riskState,
                    self._current_execution_mode(),
                )
                self.state.add_trade_activity(
                    kind="paper_entry",
                    symbol=signal.symbol,
                    title=f"{signal.symbol} {signal.bias} opened",
                    message=signal.reason,
                    level="success",
                    side=signal.bias,
                    price=signal.entryPrice,
                    size=signal.size,
                    notional_usd=signal.notionalUsd,
                    signal_id=signal.id,
                )
                self.state.add_event(
                    "success",
                    f"Paper {signal.bias} opened on {signal.symbol} via {signal.setup}.",
                )
                if comparison_decision and comparison_decision.approved:
                    comparison_signal = self._build_shadow_signal(
                        source_signal=signal,
                        comparison_candidate=comparison_candidate,
                        comparison_decision=comparison_decision,
                        created_at=now,
                        ml_assessment=ml_assessment,
                    )
                    self.state.open_comparison_position(
                        comparison_signal,
                        comparison_decision.riskState,
                        self._comparison_execution_mode(),
                    )
                    comparison_opened = True
                if comparison_opened:
                    self.state.add_event(
                        "info",
                        (
                            f"Comparison book opened {comparison_candidate.bias} on "
                            f"{comparison_candidate.symbol} for side-by-side tracking."
                        ),
                    )
                self._checkpoint_state(force=True)
        else:
            self.state.add_signal(signal)
            self.state.add_event(
                "warning",
                f"{candidate.symbol} {candidate.setup} blocked: {decision.reason}",
            )
            if comparison_decision and comparison_decision.approved:
                comparison_signal = self._build_shadow_signal(
                    source_signal=signal,
                    comparison_candidate=comparison_candidate,
                    comparison_decision=comparison_decision,
                    created_at=now,
                    ml_assessment=ml_assessment,
                )
                self.state.open_comparison_position(
                    comparison_signal,
                    comparison_decision.riskState,
                    self._comparison_execution_mode(),
                )
                self.state.add_event(
                    "info",
                    (
                        f"Comparison book still opened {comparison_candidate.bias} on "
                        f"{comparison_candidate.symbol} after the primary book blocked the trade."
                    ),
                )
            self._checkpoint_state(force=True)

    def _handle_positions(self, market: SymbolState) -> None:
        position = self.state.positions.get(market.symbol)
        if position is not None:
            self.state.update_position_mark(market.symbol, market.lastPrice)
            refreshed = self.state.positions.get(market.symbol)
            if refreshed is not None:
                self._apply_breakeven_rule(refreshed, market.lastPrice)
                exit_reason = self._resolve_exit_reason(refreshed, market.lastPrice)
                if exit_reason is not None:
                    self.state.close_position(market.symbol, market.lastPrice, exit_reason)
                    self._checkpoint_state(force=True)

        comparison_position = self.state.comparisonPositions.get(market.symbol)
        if comparison_position is not None:
            self.state.update_comparison_position_mark(market.symbol, market.lastPrice)
            refreshed_comparison = self.state.comparisonPositions.get(market.symbol)
            if refreshed_comparison is not None:
                self._apply_breakeven_rule(refreshed_comparison, market.lastPrice)
                comparison_exit_reason = self._resolve_exit_reason(
                    refreshed_comparison,
                    market.lastPrice,
                )
                if comparison_exit_reason is not None:
                    self.state.close_comparison_position(
                        market.symbol,
                        market.lastPrice,
                        comparison_exit_reason,
                    )
                    self._checkpoint_state(force=True)

    def _service_health(self) -> list[ServiceHealth]:
        return [
            ServiceHealth(
                id="engine",
                label="Strategy Engine",
                status="healthy",
                message=self._engine_status_message(),
            ),
            ServiceHealth(
                id="risk",
                label="Risk Layer",
                status="healthy",
                message=(
                    "Position sizing and daily loss controls are active."
                    if self.settings.enforceDailyLossLimit
                    else "Position sizing is active. Daily loss stop is disabled for testing."
                ),
            ),
            self.stateStore.health(),
            self.marketData.health(),
            self.execution.health(),
        ]

    def _assess_candidate_with_ml(self, candidate: StrategyCandidate) -> MlAssessment:
        market = self.state.markets.get(candidate.symbol)
        if market is None:
            return MlAssessment(
                ready=False,
                reason="ML classifier could not find local market history for this symbol.",
            )
        return self.mlModel.assess(list(market.priceHistory), candidate.bias)

    async def _refresh_ml_model(self, force: bool = False) -> None:
        try:
            retrained = await self.mlModel.refresh_if_due(self.settings.symbols, force=force)
            if retrained:
                self._emit_notice(
                    key="ml-model-trained",
                    level="success",
                    message=self.mlModel.lastSummary,
                    cooldown_seconds=300,
                )
                self.audit.write(
                    event_type="ml",
                    action="ml_model.refresh",
                    status="success",
                    details={
                        "trainingSource": self.mlModel.trainingSource,
                        "trainingSamples": self.mlModel.trainingSamples,
                        "decisionPrecision": self.mlModel.snapshot().decisionPrecision,
                        "decisionSamples": self.mlModel.snapshot().decisionSamples,
                    },
                )
                self._checkpoint_state(force=True)
        except Exception as exc:
            self.mlModel.ready = False
            self.mlModel.lastError = str(exc)
            self.mlModel.lastSummary = f"ML training failed. Falling back to rules only: {exc}"
            self._emit_notice(
                key="ml-model-failed",
                level="warning",
                message=self.mlModel.lastSummary,
                cooldown_seconds=300,
            )
            self.audit.write(
                event_type="ml",
                action="ml_model.refresh",
                status="failure",
                details={"error": str(exc)},
            )
            self._checkpoint_state(force=True)

    def _audit_operator_action(
        self,
        action: str,
        status: str,
        details: dict[str, object] | None = None,
    ) -> None:
        self.audit.write(
            event_type="operator",
            action=action,
            status=status,
            details=details,
        )

    def _config_probe(self) -> DiagnosticProbe:
        account_address = self._effective_account_address()
        missing_for_live: list[str] = []
        if self.settings.enableLiveTrading and not account_address:
            missing_for_live.append("PACIFICA_ACCOUNT_ADDRESS")
        if self.settings.enableLiveTrading and not self.settings.pacificaAgentPrivateKey:
            missing_for_live.append("PACIFICA_AGENT_PRIVATE_KEY")

        status = "healthy" if not missing_for_live else "degraded"
        message = (
            "Configuration is ready for the current mode."
            if not missing_for_live
            else f"Missing live-trading settings: {', '.join(missing_for_live)}."
        )
        return DiagnosticProbe(
            id="config",
            label="Configuration",
            status=status,
            message=message,
            details={
                "mode": self.settings.botMode,
                "network": self.settings.pacificaNetwork,
                "liveTradingEnabled": self.settings.enableLiveTrading,
                "useSimulatedFeed": self.settings.useSimulatedFeed,
                "contrarianExecutionEnabled": self.settings.contrarianExecutionEnabled,
                "effectiveAccountAddress": account_address,
                "accountConfigurationSource": self._account_configuration_source(),
            },
        )

    def _dependency_probe(self) -> DiagnosticProbe:
        availability = {
            "fastapi": find_spec("fastapi") is not None,
            "httpx": find_spec("httpx") is not None,
            "websockets": find_spec("websockets") is not None,
            "solders": find_spec("solders") is not None,
            "base58": find_spec("base58") is not None,
        }
        missing = [name for name, present in availability.items() if not present]
        status = "healthy" if not missing else "degraded"
        message = "Runtime dependencies are installed." if not missing else f"Missing dependencies: {', '.join(missing)}."
        return DiagnosticProbe(
            id="dependencies",
            label="Runtime Dependencies",
            status=status,
            message=message,
            details=availability,
        )

    def _cache_probe(self) -> DiagnosticProbe:
        return DiagnosticProbe(
            id="runtime_cache",
            label="Runtime Cache",
            status="healthy",
            message="Cached runtime state is available.",
            details={
                "marketSpecs": len(self.marketData.marketSpecs),
                "quotes": len(self.marketData.quotes),
                "remoteAccountSynced": self.state.remoteAccount is not None,
                "paperPositions": len(self.state.positions),
                "remotePositions": len(self.state.remoteSnapshot.positions) if self.state.remoteSnapshot else 0,
                "openOrders": len(self.state.remoteSnapshot.openOrders) if self.state.remoteSnapshot else 0,
                "lastOrderId": self.state.remoteSnapshot.lastOrderId if self.state.remoteSnapshot else None,
            },
        )

    async def _live_probes(self) -> list[DiagnosticProbe]:
        probes: list[DiagnosticProbe] = []

        try:
            market_specs = await self.client.get_market_info(self.settings.symbols)
            if market_specs:
                self.marketData.marketSpecs.update(market_specs)
                self.state.apply_market_specs(market_specs)
            probes.append(
                DiagnosticProbe(
                    id="rest_market_info",
                    label="REST Market Info",
                    status="healthy",
                    message=f"Fetched market specs for {len(market_specs)} symbols.",
                    details={"symbols": sorted(market_specs.keys())},
                )
            )
        except Exception as exc:
            probes.append(
                DiagnosticProbe(
                    id="rest_market_info",
                    label="REST Market Info",
                    status="degraded",
                    message=f"Failed to fetch market info: {exc}",
                )
            )

        try:
            quotes = await self.client.get_prices(self.settings.symbols)
            if quotes:
                for quote in quotes.values():
                    self.state.ingest_quote(quote)
                self.marketData.quotes.update(quotes)
            probes.append(
                DiagnosticProbe(
                    id="rest_prices",
                    label="REST Prices",
                    status="healthy",
                    message=f"Fetched price snapshots for {len(quotes)} symbols.",
                    details={
                        "symbols": sorted(quotes.keys()),
                        "source": "rest",
                    },
                )
            )
        except Exception as exc:
            probes.append(
                DiagnosticProbe(
                    id="rest_prices",
                    label="REST Prices",
                    status="degraded",
                    message=f"Failed to fetch prices: {exc}",
                )
            )

        account_address = self._effective_account_address()
        if account_address:
            try:
                account = await self.client.get_account_info(account_address)
                probes.append(
                    DiagnosticProbe(
                        id="account_info",
                        label="Account Info",
                        status="healthy",
                        message="Fetched Pacifica account info.",
                        details={
                            "fields": sorted(account.keys()),
                            "positionsCount": account.get("positions_count"),
                            "ordersCount": account.get("orders_count"),
                            "stopOrdersCount": account.get("stop_orders_count"),
                        },
                    )
                )
            except Exception as exc:
                probes.append(
                    DiagnosticProbe(
                        id="account_info",
                        label="Account Info",
                        status="degraded",
                        message=f"Failed to fetch account info: {exc}",
                    )
                )

            try:
                positions, last_order_id = await self.client.get_positions(account_address)
                probes.append(
                    DiagnosticProbe(
                        id="account_positions",
                        label="Account Positions",
                        status="healthy",
                        message=f"Fetched {len(positions)} Pacifica positions.",
                        details={
                            "count": len(positions),
                            "symbols": sorted(
                                {
                                    str(item.get("symbol"))
                                    for item in positions
                                    if item.get("symbol") is not None
                                }
                            ),
                            "lastOrderId": last_order_id,
                        },
                    )
                )
            except Exception as exc:
                probes.append(
                    DiagnosticProbe(
                        id="account_positions",
                        label="Account Positions",
                        status="degraded",
                        message=f"Failed to fetch positions: {exc}",
                    )
                )

            try:
                open_orders, last_order_id = await self.client.get_open_orders(account_address)
                probes.append(
                    DiagnosticProbe(
                        id="open_orders",
                        label="Open Orders",
                        status="healthy",
                        message=f"Fetched {len(open_orders)} open Pacifica orders.",
                        details={
                            "count": len(open_orders),
                            "symbols": sorted(
                                {
                                    str(item.get("symbol"))
                                    for item in open_orders
                                    if item.get("symbol") is not None
                                }
                            ),
                            "lastOrderId": last_order_id,
                        },
                    )
                )
            except Exception as exc:
                probes.append(
                    DiagnosticProbe(
                        id="open_orders",
                        label="Open Orders",
                        status="degraded",
                        message=f"Failed to fetch open orders: {exc}",
                    )
                )
        else:
            probes.append(
                DiagnosticProbe(
                    id="account_info",
                    label="Account Info",
                    status="skipped",
                    message="No Pacifica account address is configured.",
                )
            )
            probes.append(
                DiagnosticProbe(
                    id="account_positions",
                    label="Account Positions",
                    status="skipped",
                    message="No Pacifica account address is configured.",
                )
            )
            probes.append(
                DiagnosticProbe(
                    id="open_orders",
                    label="Open Orders",
                    status="skipped",
                    message="No Pacifica account address is configured.",
                )
            )

        return probes

    def _find_signal(self, signal_id: str) -> StrategySignal | None:
        for signal in self.state.signals:
            if signal.id == signal_id:
                return signal
        return None

    def _engine_status_message(self) -> str:
        if self._paused:
            return "Strategy scanning is paused by the operator."

        contrarian_note = (
            "Contrarian execution is active. Approved signals are inverted before sizing and execution. "
            if self.settings.contrarianExecutionEnabled
            else ""
        )
        ml_snapshot = self.mlModel.snapshot()
        if self.mlModel.ready:
            if ml_snapshot.decisionPrecision is None:
                return (
                    contrarian_note
                    + "ML-enhanced breakout and liquidity-sweep detection is active. "
                    + "Holdout precision is pending enough approved validation decisions."
                )
            return (
                contrarian_note
                + "ML-enhanced breakout and liquidity-sweep detection is active. "
                + f"Holdout precision {ml_snapshot.decisionPrecision:.0%}."
            )
        return contrarian_note + f"Rule-based price action is active while ML warm-up runs. {self.mlModel.lastSummary}"

    def _apply_execution_policy(
        self,
        candidate: StrategyCandidate,
    ) -> tuple[StrategyCandidate, str | None]:
        if not self.settings.contrarianExecutionEnabled:
            return candidate, None

        executed_bias = "short" if candidate.bias == "long" else "long"
        execution_candidate = StrategyCandidate(
            symbol=candidate.symbol,
            setup=candidate.setup,
            bias=executed_bias,
            confidence=candidate.confidence,
            entryPrice=candidate.entryPrice,
            stopLoss=candidate.takeProfit,
            takeProfit=candidate.stopLoss,
            reason=candidate.reason,
        )
        execution_note = (
            f"Contrarian execution flipped the original {candidate.bias} idea into a "
            f"{executed_bias} trade. Original stop is now take profit."
        )
        return execution_candidate, execution_note

    def _build_comparison_candidate(
        self,
        execution_candidate: StrategyCandidate,
    ) -> StrategyCandidate:
        comparison_bias = "short" if execution_candidate.bias == "long" else "long"
        return StrategyCandidate(
            symbol=execution_candidate.symbol,
            setup=execution_candidate.setup,
            bias=comparison_bias,
            confidence=execution_candidate.confidence,
            entryPrice=execution_candidate.entryPrice,
            stopLoss=execution_candidate.takeProfit,
            takeProfit=execution_candidate.stopLoss,
            reason=execution_candidate.reason,
        )

    def _build_shadow_signal(
        self,
        *,
        source_signal: StrategySignal,
        comparison_candidate: StrategyCandidate,
        comparison_decision,
        created_at: datetime,
        ml_assessment: MlAssessment,
    ) -> StrategySignal:
        comparison_reason = (
            "Comparison paper book opened the opposite execution policy."
            if not ml_assessment.ready
            else (
                "Comparison paper book opened the opposite execution policy after ML "
                f"confirmation. {ml_assessment.reason}"
            )
        )
        return StrategySignal(
            id=source_signal.id,
            symbol=comparison_candidate.symbol,
            setup=comparison_candidate.setup,
            bias=comparison_candidate.bias,
            confidence=round(comparison_candidate.confidence, 2),
            entryPrice=round(comparison_candidate.entryPrice, 4),
            stopLoss=round(comparison_candidate.stopLoss, 4),
            takeProfit=round(comparison_candidate.takeProfit, 4),
            size=comparison_decision.size,
            notionalUsd=comparison_decision.notionalUsd,
            status="executed",
            reason=comparison_reason,
            createdAt=created_at,
        )

    def _current_execution_mode(self) -> ExecutionMode:
        return "contrarian" if self.settings.contrarianExecutionEnabled else "normal"

    def _comparison_execution_mode(self) -> ExecutionMode:
        return "normal" if self.settings.contrarianExecutionEnabled else "contrarian"

    def _execution_risk_book(self):
        if not (
            self.settings.enableLiveTrading
            and self.settings.botMode in {"testnet", "mainnet"}
        ):
            return self.state

        remote_account = self.state.remoteAccount
        remote_snapshot = self.state.remoteSnapshot
        if remote_account is None or remote_snapshot is None:
            return self.state

        active_symbols: dict[str, object] = {}
        for position in remote_snapshot.positions:
            active_symbols[position.symbol] = position
        for order in remote_snapshot.openOrders:
            active_symbols.setdefault(order.symbol, order)

        return _ExecutionRiskBook(
            startingEquityUsd=(
                remote_account.balanceUsd
                if remote_account.balanceUsd > 0
                else remote_account.equityUsd
            ),
            realizedPnlUsd=0.0,
            positions=active_symbols,
            currentEquityUsd=remote_account.equityUsd,
            availableMarginUsd=remote_account.availableMarginUsd,
        )

    def _apply_breakeven_rule(
        self,
        position: EnginePosition | ComparisonPaperPosition,
        last_price: float,
    ) -> None:
        initial_risk = abs(position.entryPrice - position.stopLoss)
        if initial_risk <= 0:
            return

        if position.side == "long":
            breakeven_trigger = position.entryPrice + initial_risk
            if last_price >= breakeven_trigger and position.stopLoss < position.entryPrice:
                position.stopLoss = position.entryPrice
        else:
            breakeven_trigger = position.entryPrice - initial_risk
            if last_price <= breakeven_trigger and position.stopLoss > position.entryPrice:
                position.stopLoss = position.entryPrice

    def _resolve_exit_reason(
        self,
        position: EnginePosition | ComparisonPaperPosition,
        last_price: float,
    ) -> str | None:
        if position.side == "long":
            if last_price <= position.stopLoss:
                return "Stop loss hit."
            if last_price >= position.takeProfit:
                return "Take profit hit."
            return None

        if last_price >= position.stopLoss:
            return "Stop loss hit."
        if last_price <= position.takeProfit:
            return "Take profit hit."
        return None

    def _merge_signal_reason(self, base_reason: str, execution_note: str | None) -> str:
        if not execution_note:
            return base_reason
        return f"{execution_note} {base_reason}"

    def _record_operator_action(self, action: str) -> None:
        self._lastOperatorAction = action
        self._lastOperatorActionAt = datetime.now(timezone.utc)

    def _effective_account_address(self) -> str | None:
        return self._sessionAccountAddress or self.settings.pacificaAccountAddress

    def _account_configuration_source(self) -> str | None:
        if self._sessionAccountAddress:
            return "session"
        if self.settings.pacificaAccountAddress:
            return "env"
        return None

    def _looks_like_account_address(self, value: str) -> bool:
        if len(value) < 32 or len(value) > 48:
            return False
        allowed = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
        return all(char in allowed for char in value)

    def _shorten_account(self, value: str) -> str:
        if len(value) <= 12:
            return value
        return f"{value[:4]}...{value[-4:]}"

    def _apply_remote_snapshot(self, snapshot: RemoteTradingSnapshot) -> None:
        previous_snapshot = self.state.remoteSnapshot
        if previous_snapshot is not None:
            self._record_remote_position_feedback(previous_snapshot, snapshot)
        self.state.update_remote_account(snapshot)
        if previous_snapshot is None:
            self.state.add_event(
                "info",
                "Remote Pacifica account sync is active.",
            )

    def _record_remote_position_feedback(
        self,
        previous_snapshot: RemoteTradingSnapshot,
        next_snapshot: RemoteTradingSnapshot,
    ) -> None:
        previous_positions = {position.symbol: position for position in previous_snapshot.positions}
        next_positions = {position.symbol: position for position in next_snapshot.positions}
        previous_orders_by_symbol: dict[str, list[PacificaRemoteOpenOrderSnapshot]] = {}
        for order in previous_snapshot.openOrders:
            previous_orders_by_symbol.setdefault(order.symbol, []).append(order)

        for symbol, previous_position in previous_positions.items():
            next_position = next_positions.get(symbol)
            if next_position is None:
                self._record_remote_position_closed(
                    previous_position,
                    previous_orders_by_symbol.get(symbol, []),
                )
                continue

            if next_position.side != previous_position.side:
                self._record_remote_position_closed(
                    previous_position,
                    previous_orders_by_symbol.get(symbol, []),
                )

    def _record_remote_position_closed(
        self,
        position: PacificaRemotePositionSnapshot,
        previous_orders: list[PacificaRemoteOpenOrderSnapshot],
    ) -> None:
        exit_price, exit_reason, level, stop_loss_price, take_profit_price = (
            self._infer_remote_exit_feedback(position, previous_orders)
        )
        self.state.record_live_closed_trade(
            position=position,
            execution_mode=self._current_execution_mode(),
            exit_price=exit_price,
            reason=exit_reason,
            stop_loss=stop_loss_price,
            take_profit=take_profit_price,
        )
        pnl_usd = (
            self._calculate_pnl(position.side, position.entryPrice, exit_price, position.size)
            if exit_price is not None
            else None
        )
        notional_usd = (
            abs(position.size * exit_price)
            if exit_price is not None
            else position.notionalUsd
        )
        self.state.add_trade_activity(
            kind="live_exit",
            symbol=position.symbol,
            title=f"{position.symbol} {position.side} closed",
            message=exit_reason,
            level=level,
            side=position.side,
            price=exit_price,
            size=position.size,
            notional_usd=notional_usd,
            pnl_usd=pnl_usd,
        )
        pnl_suffix = f" Estimated PnL: {pnl_usd:.2f} USD." if pnl_usd is not None else ""
        price_suffix = f" Exit price {exit_price:.4f}." if exit_price is not None else ""
        self.state.add_event(
            level,
            f"{position.symbol} live position closed. {exit_reason}{price_suffix}{pnl_suffix}",
        )

    def _infer_remote_exit_feedback(
        self,
        position: PacificaRemotePositionSnapshot,
        previous_orders: list[PacificaRemoteOpenOrderSnapshot],
    ) -> tuple[float | None, str, str, float | None, float | None]:
        take_profit_price: float | None = None
        stop_loss_price: float | None = None

        for order in previous_orders:
            if not order.reduceOnly:
                continue
            trigger_price = order.stopPrice if order.stopPrice and order.stopPrice > 0 else None
            if trigger_price is None:
                continue
            order_type = order.orderType.lower()
            if "take_profit" in order_type:
                take_profit_price = trigger_price
            elif "stop_loss" in order_type or order_type == "stop_market":
                stop_loss_price = trigger_price

        current_price = None
        market = self.state.markets.get(position.symbol)
        if market is not None:
            current_price = market.lastPrice

        if take_profit_price is not None and stop_loss_price is not None:
            if current_price is not None:
                midpoint = (take_profit_price + stop_loss_price) / 2
                take_profit_hit = (
                    current_price >= midpoint
                    if position.side == "long"
                    else current_price <= midpoint
                )
                if take_profit_hit:
                    return (
                        take_profit_price,
                        "Take profit hit on Pacifica live position.",
                        "success",
                        stop_loss_price,
                        take_profit_price,
                    )
                return (
                    stop_loss_price,
                    "Stop loss hit on Pacifica live position.",
                    "warning",
                    stop_loss_price,
                    take_profit_price,
                )
            return (
                None,
                "Pacifica live position closed after an attached TP/SL order filled.",
                "info",
                stop_loss_price,
                take_profit_price,
            )

        if take_profit_price is not None:
            if current_price is None or (
                current_price >= take_profit_price
                if position.side == "long"
                else current_price <= take_profit_price
            ):
                return (
                    take_profit_price,
                    "Take profit hit on Pacifica live position.",
                    "success",
                    stop_loss_price,
                    take_profit_price,
                )

        if stop_loss_price is not None:
            if current_price is None or (
                current_price <= stop_loss_price
                if position.side == "long"
                else current_price >= stop_loss_price
            ):
                return (
                    stop_loss_price,
                    "Stop loss hit on Pacifica live position.",
                    "warning",
                    stop_loss_price,
                    take_profit_price,
                )

        if current_price is not None:
            estimated_pnl = self._calculate_pnl(
                position.side,
                position.entryPrice,
                current_price,
                position.size,
            )
            return (
                current_price,
                "Pacifica live position closed. Exit order filled or position was closed manually.",
                "success" if estimated_pnl >= 0 else "warning",
                stop_loss_price,
                take_profit_price,
            )

        return (
            None,
            "Pacifica live position closed. Exit order filled or position was closed manually.",
            "info",
            stop_loss_price,
            take_profit_price,
        )

    async def _sync_remote_account_if_due(self, force: bool = False) -> None:
        if not self._effective_account_address():
            return

        now = datetime.now(timezone.utc)
        if (
            not force
            and self._lastAccountSyncAttemptAt is not None
            and (now - self._lastAccountSyncAttemptAt).total_seconds()
            < self.settings.accountSyncIntervalSec
        ):
            return

        self._lastAccountSyncAttemptAt = now
        try:
            snapshot = await self.execution.sync_remote_account()
            if snapshot is not None:
                self._apply_remote_snapshot(snapshot)
        except Exception as exc:
            self.execution.lastError = f"Account sync failed: {exc}"
            self._emit_notice(
                key="account-sync-failed",
                level="warning",
                message=f"Pacifica account sync failed: {exc}",
                cooldown_seconds=90,
            )

    def _emit_notice(
        self,
        *,
        key: str,
        level: str,
        message: str,
        cooldown_seconds: int,
    ) -> None:
        now = datetime.now(timezone.utc)
        previous = self._lastNoticeAt.get(key)
        if previous and (now - previous).total_seconds() < cooldown_seconds:
            return
        self._lastNoticeAt[key] = now
        self.state.add_event(level, message)

    def _restore_runtime_state(self) -> bool:
        snapshot = self.stateStore.load()
        if snapshot is None:
            if self.stateStore.lastError:
                self.audit.write(
                    event_type="lifecycle",
                    action="engine.restore",
                    status="failure",
                    details={"error": self.stateStore.lastError},
                )
            return False

        self.state.restore_from_persisted_state(snapshot)
        self._paused = snapshot.operator.paused
        restored_session_account = snapshot.operator.sessionAccountAddress
        self._sessionAccountAddress = None
        self._lastOperatorAction = snapshot.operator.lastOperatorAction
        self._lastOperatorActionAt = snapshot.operator.lastOperatorActionAt
        self._lastAccountSyncAttemptAt = snapshot.operator.lastAccountSyncAttemptAt
        self.execution.sessionAccountAddress = None
        if (
            restored_session_account
            and restored_session_account != self.settings.pacificaAccountAddress
        ):
            self.state.remoteSnapshot = None
        self.execution.remoteSnapshot = self.state.remoteSnapshot
        self.execution.lastAccountSyncAt = (
            self.state.remoteSnapshot.syncedAt if self.state.remoteSnapshot else None
        )
        self.audit.write(
            event_type="lifecycle",
            action="engine.restore",
            status="success",
            details={
                "persistedAt": snapshot.persistedAt.isoformat(),
                "positions": len(snapshot.positions),
                "signals": len(snapshot.signals),
                "restoredSessionAccountIgnored": bool(restored_session_account),
            },
        )
        return True

    def _checkpoint_state(self, force: bool = False) -> None:
        if not self.settings.persistRuntimeState:
            return

        now = datetime.now(timezone.utc)
        if (
            not force
            and self._lastCheckpointAt is not None
            and (now - self._lastCheckpointAt).total_seconds()
            < self.settings.stateCheckpointIntervalSec
        ):
            return

        snapshot = self.state.to_persisted_state(
            paused=self._paused,
            session_account_address=None,
            last_operator_action=self._lastOperatorAction,
            last_operator_action_at=self._lastOperatorActionAt,
            last_account_sync_attempt_at=self._lastAccountSyncAttemptAt,
        )
        self.stateStore.save(snapshot)
        if self.stateStore.lastError is None:
            self._lastCheckpointAt = now
