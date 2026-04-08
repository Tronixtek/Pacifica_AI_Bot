from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from math import sin
from typing import Iterable, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from app.config import Settings
from app.contracts import (
    AccountSnapshot,
    BotSnapshot,
    ChartCandle,
    DashboardSnapshot,
    EnginePosition,
    EventLog,
    ExecutionMode,
    LivePerformanceSnapshot,
    LivePerformanceSummary,
    MarketSnapshot,
    MarketChart,
    MlModelSnapshot,
    OpenOrderSnapshot,
    OperatorSnapshot,
    PaperAccountSnapshot,
    PaperClosedTrade,
    PaperPerformanceSnapshot,
    PaperPerformanceSummary,
    RemotePositionSnapshot as DashboardRemotePositionSnapshot,
    ServiceHealth,
    StrategySignal,
    TradeActivity,
)
from app.pacifica.models import (
    MarketQuote,
    MarketSpec,
    RemoteAccountSnapshot as PacificaRemoteAccountSnapshot,
    RemoteOpenOrderSnapshot as PacificaRemoteOpenOrderSnapshot,
    RemotePositionSnapshot as PacificaRemotePositionSnapshot,
    RemoteTradingSnapshot,
)


PRICE_HISTORY_WINDOW = 180
CHART_WINDOW_SIZE = 72
SIGNAL_BUFFER_SIZE = 12
EVENT_BUFFER_SIZE = 20
ACTIVITY_BUFFER_SIZE = 32
RECENT_CLOSED_TRADE_LIMIT = 10


@dataclass(slots=True)
class SymbolState:
    symbol: str
    baselinePrice: float
    lastPrice: float
    spreadBps: float
    bidPrice: float | None = None
    askPrice: float | None = None
    tickSize: float | None = None
    lotSize: float | None = None
    minOrderSizeUsd: float | None = None
    maxLeverage: int | None = None
    updatedAt: datetime | None = None
    priceHistory: deque[float] = field(default_factory=lambda: deque(maxlen=PRICE_HISTORY_WINDOW))
    candles: deque[ChartCandle] = field(default_factory=lambda: deque(maxlen=CHART_WINDOW_SIZE))

    def __post_init__(self) -> None:
        observed_at = self.updatedAt or datetime.now(timezone.utc)
        self.priceHistory.append(self.lastPrice)
        candle_time = observed_at.replace(second=0, microsecond=0)
        self.candles.append(
            ChartCandle(
                time=candle_time,
                open=self.lastPrice,
                high=self.lastPrice,
                low=self.lastPrice,
                close=self.lastPrice,
            )
        )


@dataclass(slots=True)
class _PaperRiskBook:
    startingEquityUsd: float
    realizedPnlUsd: float
    positions: dict[str, EnginePosition | "ComparisonPaperPosition"]

    @property
    def unrealizedPnlUsd(self) -> float:
        return round(sum(position.pnlUsd for position in self.positions.values()), 2)

    @property
    def currentEquityUsd(self) -> float:
        return round(self.startingEquityUsd + self.realizedPnlUsd + self.unrealizedPnlUsd, 2)

    @property
    def availableMarginUsd(self) -> float:
        used_margin = 0.0
        for position in self.positions.values():
            used_margin += abs(position.size * position.entryPrice) / 3.0
        return round(max(self.currentEquityUsd - used_margin, 0.0), 2)


@dataclass(slots=True)
class EngineRuntimeState:
    startingEquityUsd: float
    markets: dict[str, SymbolState] = field(default_factory=dict)
    positions: dict[str, EnginePosition] = field(default_factory=dict)
    comparisonPositions: dict[str, "ComparisonPaperPosition"] = field(default_factory=dict)
    signals: deque[StrategySignal] = field(default_factory=lambda: deque(maxlen=SIGNAL_BUFFER_SIZE))
    events: deque[EventLog] = field(default_factory=lambda: deque(maxlen=EVENT_BUFFER_SIZE))
    tradeActivity: deque[TradeActivity] = field(default_factory=lambda: deque(maxlen=ACTIVITY_BUFFER_SIZE))
    closedPaperTrades: list[PaperClosedTrade] = field(default_factory=list)
    closedLiveTrades: list[PaperClosedTrade] = field(default_factory=list)
    realizedPnlUsd: float = 0.0
    comparisonRealizedPnlUsd: float = 0.0
    paperPeakEquityUsd: float = 0.0
    paperMaxDrawdownUsd: float = 0.0
    paperMaxDrawdownPct: float = 0.0
    comparisonPeakEquityUsd: float = 0.0
    comparisonMaxDrawdownUsd: float = 0.0
    comparisonMaxDrawdownPct: float = 0.0
    liveStartingEquityUsd: float | None = None
    livePeakEquityUsd: float = 0.0
    liveMaxDrawdownUsd: float = 0.0
    liveMaxDrawdownPct: float = 0.0
    remoteSnapshot: RemoteTradingSnapshot | None = None

    def __post_init__(self) -> None:
        starting_equity = round(self.startingEquityUsd, 2)
        if self.paperPeakEquityUsd <= 0:
            self.paperPeakEquityUsd = starting_equity
        if self.comparisonPeakEquityUsd <= 0:
            self.comparisonPeakEquityUsd = starting_equity
        self._refresh_drawdown_tracking()

    @property
    def remoteAccount(self) -> PacificaRemoteAccountSnapshot | None:
        if self.remoteSnapshot is None:
            return None
        return self.remoteSnapshot.account

    @property
    def unrealizedPnlUsd(self) -> float:
        return round(sum(position.pnlUsd for position in self.positions.values()), 2)

    @property
    def currentEquityUsd(self) -> float:
        return round(self.startingEquityUsd + self.realizedPnlUsd + self.unrealizedPnlUsd, 2)

    @property
    def availableMarginUsd(self) -> float:
        return self._estimate_available_margin(self.positions, self.currentEquityUsd)

    @property
    def comparisonUnrealizedPnlUsd(self) -> float:
        return round(sum(position.pnlUsd for position in self.comparisonPositions.values()), 2)

    @property
    def comparisonCurrentEquityUsd(self) -> float:
        return round(
            self.startingEquityUsd
            + self.comparisonRealizedPnlUsd
            + self.comparisonUnrealizedPnlUsd,
            2,
        )

    @property
    def comparisonAvailableMarginUsd(self) -> float:
        return self._estimate_available_margin(
            self.comparisonPositions,
            self.comparisonCurrentEquityUsd,
        )

    @property
    def liveCurrentEquityUsd(self) -> float:
        if self.remoteAccount is not None:
            return round(self.remoteAccount.equityUsd, 2)
        if self.liveStartingEquityUsd is None:
            return 0.0
        return round(
            self.liveStartingEquityUsd + sum(trade.pnlUsd for trade in self.closedLiveTrades),
            2,
        )

    def paper_account_snapshot(self) -> PaperAccountSnapshot:
        return PaperAccountSnapshot(
            startingEquityUsd=round(self.startingEquityUsd, 2),
            equityUsd=self.currentEquityUsd,
            availableMarginUsd=self.availableMarginUsd,
            realizedPnlUsd=round(self.realizedPnlUsd, 2),
            unrealizedPnlUsd=self.unrealizedPnlUsd,
            openPositions=len(self.positions),
        )

    def comparison_risk_book(self) -> _PaperRiskBook:
        return _PaperRiskBook(
            startingEquityUsd=self.startingEquityUsd,
            realizedPnlUsd=self.comparisonRealizedPnlUsd,
            positions=self.comparisonPositions,
        )

    def bootstrap_markets(self, symbols: Iterable[str]) -> None:
        baselines = {
            "BTC": 89_000.0,
            "ETH": 3_200.0,
            "SOL": 180.0,
            "BNB": 640.0,
        }
        for symbol in symbols:
            if symbol in self.markets:
                continue
            baseline = baselines.get(symbol, 100.0)
            self.markets[symbol] = SymbolState(
                symbol=symbol,
                baselinePrice=baseline,
                lastPrice=baseline,
                spreadBps=1.5,
                updatedAt=datetime.now(timezone.utc),
            )
            self._seed_market_history(self.markets[symbol])

    def _seed_market_history(self, market: SymbolState) -> None:
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        seeded_closes = [
            round(
                market.baselinePrice
                * (
                    1
                    + (sin((step + len(market.symbol)) / 4.2) * 0.0042)
                    + (sin((step + 3) / 9.5) * 0.0028)
                ),
                4,
            )
            for step in range(CHART_WINDOW_SIZE)
        ]
        market.priceHistory = deque(seeded_closes[-PRICE_HISTORY_WINDOW:], maxlen=PRICE_HISTORY_WINDOW)
        market.candles = deque(maxlen=CHART_WINDOW_SIZE)

        for index, close_price in enumerate(seeded_closes):
            candle_time = now - timedelta(minutes=(CHART_WINDOW_SIZE - 1 - index))
            open_price = seeded_closes[index - 1] if index > 0 else close_price
            high_price = max(open_price, close_price) * 1.0006
            low_price = min(open_price, close_price) * 0.9994
            market.candles.append(
                ChartCandle(
                    time=candle_time,
                    open=round(open_price, 4),
                    high=round(high_price, 4),
                    low=round(low_price, 4),
                    close=round(close_price, 4),
                )
            )

        market.lastPrice = seeded_closes[-1]
        market.updatedAt = now

    def record_market_price(
        self,
        symbol: str,
        price: float,
        *,
        spread_bps: float,
        observed_at: datetime | None = None,
        bid_price: float | None = None,
        ask_price: float | None = None,
    ) -> None:
        market = self.markets.get(symbol)
        if market is None:
            market = SymbolState(
                symbol=symbol,
                baselinePrice=price,
                lastPrice=price,
                spreadBps=spread_bps,
                updatedAt=observed_at or datetime.now(timezone.utc),
            )
            self.markets[symbol] = market

        observed_at = observed_at or datetime.now(timezone.utc)
        market.lastPrice = round(price, 4)
        market.spreadBps = round(spread_bps, 2)
        market.bidPrice = bid_price
        market.askPrice = ask_price
        market.updatedAt = observed_at
        market.priceHistory.append(market.lastPrice)
        self._append_chart_candle(market, market.lastPrice, observed_at)

    def _append_chart_candle(
        self,
        market: SymbolState,
        price: float,
        observed_at: datetime,
    ) -> None:
        candle_time = observed_at.replace(second=0, microsecond=0)
        if market.candles and market.candles[-1].time == candle_time:
            candle = market.candles[-1]
            candle.high = round(max(candle.high, price), 4)
            candle.low = round(min(candle.low, price), 4)
            candle.close = round(price, 4)
            return

        open_price = market.candles[-1].close if market.candles else price
        market.candles.append(
            ChartCandle(
                time=candle_time,
                open=round(open_price, 4),
                high=round(max(open_price, price), 4),
                low=round(min(open_price, price), 4),
                close=round(price, 4),
            )
        )

    def apply_market_specs(self, market_specs: dict[str, MarketSpec]) -> int:
        updated = 0
        for symbol, spec in market_specs.items():
            market = self.markets.get(symbol)
            if market is None:
                continue
            market.tickSize = spec.tickSize
            market.lotSize = spec.lotSize
            market.minOrderSizeUsd = spec.minOrderSizeUsd
            market.maxLeverage = spec.maxLeverage
            updated += 1
        return updated

    def ingest_quote(self, quote: MarketQuote) -> None:
        market = self.markets.get(quote.symbol)
        self.record_market_price(
            quote.symbol,
            quote.markPrice,
            spread_bps=quote.spreadBps or (market.spreadBps if market else 0.0),
            observed_at=quote.updatedAt,
            bid_price=quote.bidPrice,
            ask_price=quote.askPrice,
        )

    def update_remote_account(self, snapshot: RemoteTradingSnapshot) -> None:
        self.remoteSnapshot = snapshot
        starting_equity = (
            snapshot.account.balanceUsd
            if snapshot.account.balanceUsd > 0
            else snapshot.account.equityUsd
        )
        if self.liveStartingEquityUsd is None or self.liveStartingEquityUsd <= 0:
            self.liveStartingEquityUsd = round(starting_equity, 2)
        if self.livePeakEquityUsd <= 0:
            self.livePeakEquityUsd = round(snapshot.account.equityUsd, 2)
        self._refresh_live_drawdown_tracking()

    def to_persisted_state(
        self,
        *,
        paused: bool,
        session_account_address: str | None,
        last_operator_action: str | None,
        last_operator_action_at: datetime | None,
        last_account_sync_attempt_at: datetime | None,
    ) -> "PersistedEngineState":
        return PersistedEngineState(
            startingEquityUsd=round(self.startingEquityUsd, 2),
            realizedPnlUsd=round(self.realizedPnlUsd, 2),
            comparisonRealizedPnlUsd=round(self.comparisonRealizedPnlUsd, 2),
            paperPeakEquityUsd=round(self.paperPeakEquityUsd, 2),
            paperMaxDrawdownUsd=round(self.paperMaxDrawdownUsd, 2),
            paperMaxDrawdownPct=round(self.paperMaxDrawdownPct, 4),
            comparisonPeakEquityUsd=round(self.comparisonPeakEquityUsd, 2),
            comparisonMaxDrawdownUsd=round(self.comparisonMaxDrawdownUsd, 2),
            comparisonMaxDrawdownPct=round(self.comparisonMaxDrawdownPct, 4),
            liveStartingEquityUsd=(
                round(self.liveStartingEquityUsd, 2)
                if self.liveStartingEquityUsd is not None
                else None
            ),
            livePeakEquityUsd=round(self.livePeakEquityUsd, 2),
            liveMaxDrawdownUsd=round(self.liveMaxDrawdownUsd, 2),
            liveMaxDrawdownPct=round(self.liveMaxDrawdownPct, 4),
            markets=[
                PersistedSymbolState(
                    symbol=market.symbol,
                    baselinePrice=market.baselinePrice,
                    lastPrice=market.lastPrice,
                    spreadBps=market.spreadBps,
                    bidPrice=market.bidPrice,
                    askPrice=market.askPrice,
                    tickSize=market.tickSize,
                    lotSize=market.lotSize,
                    minOrderSizeUsd=market.minOrderSizeUsd,
                    maxLeverage=market.maxLeverage,
                    updatedAt=market.updatedAt,
                    priceHistory=list(market.priceHistory),
                    candles=list(market.candles),
                )
                for market in self.markets.values()
            ],
            positions=list(self.positions.values()),
            comparisonPositions=list(self.comparisonPositions.values()),
            signals=list(self.signals),
            events=list(self.events),
            tradeActivity=list(self.tradeActivity),
            closedPaperTrades=self.closedPaperTrades,
            closedLiveTrades=self.closedLiveTrades,
            remoteSnapshot=(
                PersistedRemoteTradingSnapshot.from_runtime(self.remoteSnapshot)
                if self.remoteSnapshot
                else None
            ),
            operator=PersistedOperatorState(
                paused=paused,
                sessionAccountAddress=session_account_address,
                lastOperatorAction=last_operator_action,
                lastOperatorActionAt=last_operator_action_at,
                lastAccountSyncAttemptAt=last_account_sync_attempt_at,
            ),
        )

    def restore_from_persisted_state(self, snapshot: "PersistedEngineState") -> None:
        self.startingEquityUsd = round(snapshot.startingEquityUsd, 2)
        self.realizedPnlUsd = round(snapshot.realizedPnlUsd, 2)
        self.comparisonRealizedPnlUsd = round(snapshot.comparisonRealizedPnlUsd, 2)
        self.paperPeakEquityUsd = round(snapshot.paperPeakEquityUsd, 2)
        self.paperMaxDrawdownUsd = round(snapshot.paperMaxDrawdownUsd, 2)
        self.paperMaxDrawdownPct = round(snapshot.paperMaxDrawdownPct, 4)
        self.comparisonPeakEquityUsd = round(snapshot.comparisonPeakEquityUsd, 2)
        self.comparisonMaxDrawdownUsd = round(snapshot.comparisonMaxDrawdownUsd, 2)
        self.comparisonMaxDrawdownPct = round(snapshot.comparisonMaxDrawdownPct, 4)
        self.liveStartingEquityUsd = (
            round(snapshot.liveStartingEquityUsd, 2)
            if snapshot.liveStartingEquityUsd is not None
            else None
        )
        self.livePeakEquityUsd = round(snapshot.livePeakEquityUsd, 2)
        self.liveMaxDrawdownUsd = round(snapshot.liveMaxDrawdownUsd, 2)
        self.liveMaxDrawdownPct = round(snapshot.liveMaxDrawdownPct, 4)
        self.markets = {}
        for market in snapshot.markets:
            restored_market = SymbolState(
                symbol=market.symbol,
                baselinePrice=market.baselinePrice,
                lastPrice=market.lastPrice,
                spreadBps=market.spreadBps,
                bidPrice=market.bidPrice,
                askPrice=market.askPrice,
                tickSize=market.tickSize,
                lotSize=market.lotSize,
                minOrderSizeUsd=market.minOrderSizeUsd,
                maxLeverage=market.maxLeverage,
                updatedAt=market.updatedAt,
            )
            restored_market.priceHistory = deque(
                market.priceHistory or [market.lastPrice],
                maxlen=PRICE_HISTORY_WINDOW,
            )
            restored_market.candles = deque(
                market.candles
                or [
                    ChartCandle(
                        time=market.updatedAt or datetime.now(timezone.utc),
                        open=market.lastPrice,
                        high=market.lastPrice,
                        low=market.lastPrice,
                        close=market.lastPrice,
                    )
                ],
                maxlen=CHART_WINDOW_SIZE,
            )
            self.markets[market.symbol] = restored_market
        self.positions = {position.symbol: position for position in snapshot.positions}
        self.comparisonPositions = {
            position.symbol: position for position in snapshot.comparisonPositions
        }
        self.signals = deque(snapshot.signals, maxlen=SIGNAL_BUFFER_SIZE)
        self.events = deque(snapshot.events, maxlen=EVENT_BUFFER_SIZE)
        self.tradeActivity = deque(snapshot.tradeActivity, maxlen=ACTIVITY_BUFFER_SIZE)
        self.closedPaperTrades = list(snapshot.closedPaperTrades)
        self.closedLiveTrades = list(snapshot.closedLiveTrades)
        self.remoteSnapshot = (
            snapshot.remoteSnapshot.to_runtime() if snapshot.remoteSnapshot else None
        )
        self._refresh_drawdown_tracking()
        self._refresh_live_drawdown_tracking()

    def reset_paper_account(self, starting_equity_usd: float) -> PaperAccountSnapshot:
        self.positions.clear()
        self.comparisonPositions.clear()
        self.realizedPnlUsd = 0.0
        self.comparisonRealizedPnlUsd = 0.0
        self.closedPaperTrades = []
        self.startingEquityUsd = round(starting_equity_usd, 2)
        self.paperPeakEquityUsd = round(starting_equity_usd, 2)
        self.paperMaxDrawdownUsd = 0.0
        self.paperMaxDrawdownPct = 0.0
        self.comparisonPeakEquityUsd = round(starting_equity_usd, 2)
        self.comparisonMaxDrawdownUsd = 0.0
        self.comparisonMaxDrawdownPct = 0.0
        self._refresh_drawdown_tracking()
        return self.paper_account_snapshot()

    def top_up_paper_account(self, amount_usd: float) -> PaperAccountSnapshot:
        self.startingEquityUsd = round(self.startingEquityUsd + amount_usd, 2)
        self._refresh_drawdown_tracking()
        return self.paper_account_snapshot()

    def add_event(self, level: str, message: str) -> None:
        self.events.append(
            EventLog(
                id=str(uuid4()),
                level=level,
                message=message,
                createdAt=datetime.now(timezone.utc),
            )
        )

    def add_signal(self, signal: StrategySignal) -> None:
        self.signals.append(signal)

    def add_trade_activity(
        self,
        *,
        kind: str,
        symbol: str,
        title: str,
        message: str,
        level: str,
        side: str | None = None,
        price: float | None = None,
        size: float | None = None,
        notional_usd: float | None = None,
        pnl_usd: float | None = None,
        signal_id: str | None = None,
        order_id: int | None = None,
    ) -> None:
        self.tradeActivity.append(
            TradeActivity(
                id=str(uuid4()),
                kind=kind,
                symbol=symbol,
                title=title,
                message=message,
                level=level,
                side=side,
                price=round(price, 4) if price is not None else None,
                size=round(size, 6) if size is not None else None,
                notionalUsd=round(notional_usd, 2) if notional_usd is not None else None,
                pnlUsd=round(pnl_usd, 2) if pnl_usd is not None else None,
                signalId=signal_id,
                orderId=order_id,
                createdAt=datetime.now(timezone.utc),
            )
        )

    def open_position(
        self,
        signal: StrategySignal,
        risk_state: str,
        execution_mode: ExecutionMode,
    ) -> None:
        self.positions[signal.symbol] = EnginePosition(
            symbol=signal.symbol,
            side=signal.bias,
            size=signal.size,
            entryPrice=signal.entryPrice,
            markPrice=signal.entryPrice,
            stopLoss=signal.stopLoss,
            takeProfit=signal.takeProfit,
            pnlUsd=0.0,
            pnlPct=0.0,
            riskState=risk_state,
            openedAt=datetime.now(timezone.utc),
            signalId=signal.id,
            setup=signal.setup,
            confidence=signal.confidence,
            notionalUsd=signal.notionalUsd,
            riskUsd=round(abs(signal.entryPrice - signal.stopLoss) * signal.size, 2),
            executionMode=execution_mode,
        )
        self._refresh_drawdown_tracking()

    def open_comparison_position(
        self,
        signal: StrategySignal,
        risk_state: str,
        execution_mode: ExecutionMode,
    ) -> None:
        self.comparisonPositions[signal.symbol] = ComparisonPaperPosition(
            symbol=signal.symbol,
            signalId=signal.id,
            setup=signal.setup,
            side=signal.bias,
            confidence=signal.confidence,
            size=signal.size,
            notionalUsd=signal.notionalUsd,
            entryPrice=signal.entryPrice,
            markPrice=signal.entryPrice,
            stopLoss=signal.stopLoss,
            takeProfit=signal.takeProfit,
            pnlUsd=0.0,
            pnlPct=0.0,
            riskState=risk_state,
            executionMode=execution_mode,
            riskUsd=round(abs(signal.entryPrice - signal.stopLoss) * signal.size, 2),
            openedAt=datetime.now(timezone.utc),
        )
        self._refresh_drawdown_tracking()

    def close_position(self, symbol: str, exit_price: float, reason: str) -> PaperClosedTrade | None:
        position = self.positions.pop(symbol, None)
        if position is None:
            return None

        closed_at = datetime.now(timezone.utc)
        pnl = self._calculate_pnl(position.side, position.entryPrice, exit_price, position.size)
        self.realizedPnlUsd = round(self.realizedPnlUsd + pnl, 2)
        closed_trade = self._build_closed_trade(
            book="primary",
            position=position,
            exit_price=exit_price,
            pnl=pnl,
            reason=reason,
            closed_at=closed_at,
        )
        self.closedPaperTrades.append(closed_trade)
        self.add_trade_activity(
            kind="paper_exit",
            symbol=position.symbol,
            title=f"{position.symbol} {position.side} closed",
            message=reason,
            level="success" if pnl >= 0 else "warning",
            side=position.side,
            price=exit_price,
            size=position.size,
            notional_usd=position.size * exit_price,
            pnl_usd=pnl,
            signal_id=position.signalId,
        )
        self.add_event(
            "success" if pnl >= 0 else "warning",
            f"{symbol} paper position closed at {exit_price:.2f}. {reason} PnL: {pnl:.2f} USD.",
        )
        self._refresh_drawdown_tracking()
        return closed_trade

    def close_comparison_position(
        self,
        symbol: str,
        exit_price: float,
        reason: str,
    ) -> PaperClosedTrade | None:
        position = self.comparisonPositions.pop(symbol, None)
        if position is None:
            return None

        closed_at = datetime.now(timezone.utc)
        pnl = self._calculate_pnl(position.side, position.entryPrice, exit_price, position.size)
        self.comparisonRealizedPnlUsd = round(self.comparisonRealizedPnlUsd + pnl, 2)
        closed_trade = self._build_closed_trade(
            book="comparison",
            position=position,
            exit_price=exit_price,
            pnl=pnl,
            reason=reason,
            closed_at=closed_at,
        )
        self.closedPaperTrades.append(closed_trade)
        self._refresh_drawdown_tracking()
        return closed_trade

    def record_live_closed_trade(
        self,
        *,
        position: PacificaRemotePositionSnapshot,
        execution_mode: ExecutionMode,
        exit_price: float | None,
        reason: str,
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> PaperClosedTrade:
        closed_at = datetime.now(timezone.utc)
        resolved_exit_price = round(exit_price if exit_price is not None else position.entryPrice, 4)
        pnl = (
            self._calculate_pnl(position.side, position.entryPrice, resolved_exit_price, position.size)
            if exit_price is not None
            else 0.0
        )
        risk_usd = (
            round(abs(position.entryPrice - stop_loss) * position.size, 2)
            if stop_loss is not None
            else None
        )
        closed_trade = PaperClosedTrade(
            id=str(uuid4()),
            book="primary",
            executionMode=execution_mode,
            signalId=None,
            symbol=position.symbol,
            setup=None,
            side=position.side,
            confidence=None,
            entryPrice=round(position.entryPrice, 4),
            exitPrice=resolved_exit_price,
            stopLoss=round(stop_loss if stop_loss is not None else position.entryPrice, 4),
            takeProfit=round(take_profit if take_profit is not None else position.entryPrice, 4),
            size=round(position.size, 6),
            notionalUsd=round(position.notionalUsd, 2),
            riskUsd=risk_usd,
            pnlUsd=round(pnl, 2),
            rMultiple=round(pnl / risk_usd, 3) if risk_usd and risk_usd > 0 else None,
            outcome=self._classify_trade_outcome(reason, pnl, risk_usd),
            exitReason=reason,
            openedAt=position.openedAt or closed_at,
            closedAt=closed_at,
        )
        self.closedLiveTrades.append(closed_trade)
        self._refresh_live_drawdown_tracking()
        return closed_trade

    def update_position_mark(self, symbol: str, mark_price: float) -> None:
        position = self.positions.get(symbol)
        if position is None:
            return
        self._update_mark(position, mark_price)
        self._refresh_drawdown_tracking()

    def update_comparison_position_mark(self, symbol: str, mark_price: float) -> None:
        position = self.comparisonPositions.get(symbol)
        if position is None:
            return
        self._update_mark(position, mark_price)
        self._refresh_drawdown_tracking()

    def paper_performance_snapshot(
        self,
        current_mode: ExecutionMode,
    ) -> PaperPerformanceSnapshot:
        comparison_mode: ExecutionMode = (
            "normal" if current_mode == "contrarian" else "contrarian"
        )
        primary_summary = self._build_performance_summary(
            book="primary",
            execution_mode=current_mode,
            positions=self.positions,
            current_equity=self.currentEquityUsd,
            peak_equity=self.paperPeakEquityUsd,
            max_drawdown_usd=self.paperMaxDrawdownUsd,
            max_drawdown_pct=self.paperMaxDrawdownPct,
        )
        comparison_summary = self._build_performance_summary(
            book="comparison",
            execution_mode=comparison_mode,
            positions=self.comparisonPositions,
            current_equity=self.comparisonCurrentEquityUsd,
            peak_equity=self.comparisonPeakEquityUsd,
            max_drawdown_usd=self.comparisonMaxDrawdownUsd,
            max_drawdown_pct=self.comparisonMaxDrawdownPct,
        )
        recent_closed_trades = list(
            reversed(self.closedPaperTrades[-RECENT_CLOSED_TRADE_LIMIT:])
        )
        primary_signals = {
            trade.signalId
            for trade in self.closedPaperTrades
            if trade.book == "primary" and trade.signalId is not None
        }
        comparison_signals = {
            trade.signalId
            for trade in self.closedPaperTrades
            if trade.book == "comparison" and trade.signalId is not None
        }

        return PaperPerformanceSnapshot(
            currentMode=current_mode,
            comparisonMode=comparison_mode,
            currentModeSummary=primary_summary,
            comparisonModeSummary=comparison_summary,
            recentClosedTrades=recent_closed_trades,
            pairedClosedSignals=len(primary_signals & comparison_signals),
            comparisonMethod=(
                "The comparison book runs the opposite execution policy on the same live "
                "signal stream without affecting the primary paper account."
            ),
        )

    def live_performance_snapshot(
        self,
        current_mode: ExecutionMode,
    ) -> LivePerformanceSnapshot | None:
        if self.liveStartingEquityUsd is None:
            return None

        trades = self.closedLiveTrades
        wins = [trade for trade in trades if trade.pnlUsd > 0]
        losses = [trade for trade in trades if trade.pnlUsd < 0]
        breakeven_trades = [trade for trade in trades if trade.outcome == "breakeven"]
        tp_hits = sum(1 for trade in trades if trade.outcome == "take_profit")
        sl_hits = sum(1 for trade in trades if trade.outcome == "stop_loss")
        r_values = [trade.rMultiple for trade in trades if trade.rMultiple is not None]
        gross_profit = round(sum(trade.pnlUsd for trade in wins), 2)
        gross_loss = round(abs(sum(trade.pnlUsd for trade in losses)), 2)
        recent_closed_trades = list(reversed(trades[-RECENT_CLOSED_TRADE_LIMIT:]))

        return LivePerformanceSnapshot(
            summary=LivePerformanceSummary(
                executionMode=current_mode,
                startingEquityUsd=round(self.liveStartingEquityUsd, 2),
                currentEquityUsd=self.liveCurrentEquityUsd,
                netPnlUsd=round(self.liveCurrentEquityUsd - self.liveStartingEquityUsd, 2),
                closedTrades=len(trades),
                wins=len(wins),
                losses=len(losses),
                breakevenTrades=len(breakeven_trades),
                winRate=self._safe_ratio(len(wins), len(trades)),
                takeProfitHits=tp_hits,
                stopLossHits=sl_hits,
                breakevenExits=len(breakeven_trades),
                averageRMultiple=(
                    round(sum(r_values) / len(r_values), 3) if r_values else None
                ),
                profitFactor=(
                    round(gross_profit / gross_loss, 3) if gross_loss > 0 else None
                ),
                averageWinUsd=round(gross_profit / len(wins), 2) if wins else None,
                averageLossUsd=round(gross_loss / len(losses), 2) if losses else None,
                peakEquityUsd=round(self.livePeakEquityUsd, 2),
                maxDrawdownUsd=round(self.liveMaxDrawdownUsd, 2),
                maxDrawdownPct=round(self.liveMaxDrawdownPct, 4),
                openPositions=len(self.remoteSnapshot.positions) if self.remoteSnapshot else 0,
                lastClosedAt=trades[-1].closedAt if trades else None,
            ),
            recentClosedTrades=recent_closed_trades,
            trackingBasis=(
                "Live testnet performance is built from synced Pacifica account equity and "
                "closed positions inferred from attached TP/SL orders."
            ),
        )

    def _build_performance_summary(
        self,
        *,
        book: Literal["primary", "comparison"],
        execution_mode: ExecutionMode,
        positions: dict[str, EnginePosition | "ComparisonPaperPosition"],
        current_equity: float,
        peak_equity: float,
        max_drawdown_usd: float,
        max_drawdown_pct: float,
    ) -> PaperPerformanceSummary:
        trades = [trade for trade in self.closedPaperTrades if trade.book == book]
        wins = [trade for trade in trades if trade.pnlUsd > 0]
        losses = [trade for trade in trades if trade.pnlUsd < 0]
        breakeven_trades = [trade for trade in trades if trade.outcome == "breakeven"]
        tp_hits = sum(1 for trade in trades if trade.outcome == "take_profit")
        sl_hits = sum(1 for trade in trades if trade.outcome == "stop_loss")
        r_values = [trade.rMultiple for trade in trades if trade.rMultiple is not None]
        gross_profit = round(sum(trade.pnlUsd for trade in wins), 2)
        gross_loss = round(abs(sum(trade.pnlUsd for trade in losses)), 2)

        return PaperPerformanceSummary(
            book=book,
            executionMode=execution_mode,
            closedTrades=len(trades),
            wins=len(wins),
            losses=len(losses),
            breakevenTrades=len(breakeven_trades),
            winRate=self._safe_ratio(len(wins), len(trades)),
            takeProfitHits=tp_hits,
            stopLossHits=sl_hits,
            breakevenExits=len(breakeven_trades),
            tpToSlRatio=self._safe_ratio(tp_hits, sl_hits),
            averageRMultiple=(
                round(sum(r_values) / len(r_values), 3) if r_values else None
            ),
            profitFactor=(
                round(gross_profit / gross_loss, 3) if gross_loss > 0 else None
            ),
            netPnlUsd=round(sum(trade.pnlUsd for trade in trades), 2),
            grossProfitUsd=gross_profit,
            grossLossUsd=gross_loss,
            averageWinUsd=(
                round(gross_profit / len(wins), 2) if wins else None
            ),
            averageLossUsd=(
                round(gross_loss / len(losses), 2) if losses else None
            ),
            peakEquityUsd=round(peak_equity, 2),
            endingEquityUsd=round(current_equity, 2),
            maxDrawdownUsd=round(max_drawdown_usd, 2),
            maxDrawdownPct=round(max_drawdown_pct, 4),
            openPositions=len(positions),
            lastClosedAt=trades[-1].closedAt if trades else None,
        )

    def _build_closed_trade(
        self,
        *,
        book: Literal["primary", "comparison"],
        position: EnginePosition | "ComparisonPaperPosition",
        exit_price: float,
        pnl: float,
        reason: str,
        closed_at: datetime,
    ) -> PaperClosedTrade:
        risk_usd = (
            round(position.riskUsd, 2)
            if position.riskUsd is not None
            else round(abs(position.entryPrice - position.stopLoss) * position.size, 2)
        )
        return PaperClosedTrade(
            id=str(uuid4()),
            book=book,
            executionMode=position.executionMode or "normal",
            signalId=position.signalId,
            symbol=position.symbol,
            setup=position.setup,
            side=position.side,
            confidence=position.confidence,
            entryPrice=round(position.entryPrice, 4),
            exitPrice=round(exit_price, 4),
            stopLoss=round(position.stopLoss, 4),
            takeProfit=round(position.takeProfit, 4),
            size=round(position.size, 6),
            notionalUsd=round(position.notionalUsd or (position.size * position.entryPrice), 2),
            riskUsd=risk_usd,
            pnlUsd=round(pnl, 2),
            rMultiple=(
                round(pnl / risk_usd, 3)
                if risk_usd and risk_usd > 0
                else None
            ),
            outcome=self._classify_trade_outcome(reason, pnl, risk_usd),
            exitReason=reason,
            openedAt=position.openedAt,
            closedAt=closed_at,
        )

    def _classify_trade_outcome(
        self,
        reason: str,
        pnl: float,
        risk_usd: float | None,
    ) -> str:
        normalized = reason.lower()
        breakeven_band = max((risk_usd or 0.0) * 0.02, 0.01)
        if abs(pnl) <= breakeven_band:
            return "breakeven"
        if "take profit" in normalized:
            return "take_profit"
        if "stop loss" in normalized:
            return "stop_loss"
        if "manual" in normalized:
            return "manual"
        return "other"

    def _safe_ratio(self, numerator: int, denominator: int) -> float | None:
        if denominator <= 0:
            return None
        return round(numerator / denominator, 4)

    def _update_mark(
        self,
        position: EnginePosition | "ComparisonPaperPosition",
        mark_price: float,
    ) -> None:
        pnl = self._calculate_pnl(position.side, position.entryPrice, mark_price, position.size)
        notional = max(position.entryPrice * position.size, 1.0)
        position.markPrice = round(mark_price, 4)
        position.pnlUsd = round(pnl, 2)
        position.pnlPct = round((pnl / notional) * 100, 2)

    def _estimate_available_margin(
        self,
        positions: dict[str, EnginePosition | "ComparisonPaperPosition"],
        equity_usd: float,
    ) -> float:
        used_margin = 0.0
        for position in positions.values():
            used_margin += abs(position.size * position.entryPrice) / 3.0
        return round(max(equity_usd - used_margin, 0.0), 2)

    def _refresh_drawdown_tracking(self) -> None:
        paper_equity = self.currentEquityUsd
        self.paperPeakEquityUsd = round(max(self.paperPeakEquityUsd, paper_equity), 2)
        current_paper_drawdown = max(self.paperPeakEquityUsd - paper_equity, 0.0)
        self.paperMaxDrawdownUsd = round(
            max(self.paperMaxDrawdownUsd, current_paper_drawdown),
            2,
        )
        if self.paperPeakEquityUsd > 0:
            self.paperMaxDrawdownPct = round(
                max(
                    self.paperMaxDrawdownPct,
                    current_paper_drawdown / self.paperPeakEquityUsd,
                ),
                4,
            )

        comparison_equity = self.comparisonCurrentEquityUsd
        self.comparisonPeakEquityUsd = round(
            max(self.comparisonPeakEquityUsd, comparison_equity),
            2,
        )
        current_comparison_drawdown = max(
            self.comparisonPeakEquityUsd - comparison_equity,
            0.0,
        )
        self.comparisonMaxDrawdownUsd = round(
            max(self.comparisonMaxDrawdownUsd, current_comparison_drawdown),
            2,
        )
        if self.comparisonPeakEquityUsd > 0:
            self.comparisonMaxDrawdownPct = round(
                max(
                    self.comparisonMaxDrawdownPct,
                    current_comparison_drawdown / self.comparisonPeakEquityUsd,
                ),
                4,
            )

    def _refresh_live_drawdown_tracking(self) -> None:
        if self.liveStartingEquityUsd is None:
            return
        live_equity = self.liveCurrentEquityUsd
        if self.livePeakEquityUsd <= 0:
            self.livePeakEquityUsd = round(max(self.liveStartingEquityUsd, live_equity), 2)
        self.livePeakEquityUsd = round(max(self.livePeakEquityUsd, live_equity), 2)
        current_live_drawdown = max(self.livePeakEquityUsd - live_equity, 0.0)
        self.liveMaxDrawdownUsd = round(max(self.liveMaxDrawdownUsd, current_live_drawdown), 2)
        if self.livePeakEquityUsd > 0:
            self.liveMaxDrawdownPct = round(
                max(
                    self.liveMaxDrawdownPct,
                    current_live_drawdown / self.livePeakEquityUsd,
                ),
                4,
            )

    def build_snapshot(
        self,
        settings: Settings,
        services: list[ServiceHealth],
        operator: OperatorSnapshot,
        ml_model: MlModelSnapshot,
    ) -> DashboardSnapshot:
        remote_account = self.remoteAccount
        if remote_account:
            pnl_usd = round(remote_account.equityUsd - remote_account.balanceUsd, 2)
            pnl_label = "Pacifica equity minus settled balance (open PnL estimate)."
        else:
            pnl_usd = round(self.realizedPnlUsd + self.unrealizedPnlUsd, 2)
            pnl_label = "Paper strategy PnL across open and closed trades."

        watchlist = [
            MarketSnapshot(
                symbol=market.symbol,
                lastPrice=round(market.lastPrice, 4),
                movePctFromOpen=round(
                    ((market.lastPrice - market.baselinePrice) / market.baselinePrice) * 100,
                    2,
                ),
                spreadBps=round(market.spreadBps, 2),
            )
            for market in self.markets.values()
        ]

        return DashboardSnapshot(
            generatedAt=datetime.now(timezone.utc),
            bot=BotSnapshot(
                mode=settings.botMode,
                network=settings.pacificaNetwork,
                status=(
                    "healthy"
                    if all(
                        service.status == "healthy"
                        or (service.id == "execution" and not settings.enableLiveTrading)
                        for service in services
                    )
                    else "degraded"
                ),
                liveTradingEnabled=settings.enableLiveTrading,
                builderCode=settings.pacificaBuilderCode,
                agentWalletConfigured=bool(settings.pacificaAgentPrivateKey),
            ),
            operator=operator,
            services=services,
            account=AccountSnapshot(
                source="pacifica" if remote_account else "paper",
                equityUsd=remote_account.equityUsd if remote_account else self.currentEquityUsd,
                availableMarginUsd=(
                    remote_account.availableMarginUsd if remote_account else self.availableMarginUsd
                ),
                balanceUsd=remote_account.balanceUsd if remote_account else None,
                availableToWithdrawUsd=(
                    remote_account.availableToWithdrawUsd if remote_account else None
                ),
                pendingBalanceUsd=remote_account.pendingBalanceUsd if remote_account else None,
                totalMarginUsedUsd=remote_account.totalMarginUsedUsd if remote_account else None,
                crossMaintenanceMarginUsd=(
                    remote_account.crossMaintenanceMarginUsd if remote_account else None
                ),
                pnlUsd=pnl_usd,
                pnlLabel=pnl_label,
                openPositions=remote_account.openPositions if remote_account else len(self.positions),
                openOrders=remote_account.openOrders if remote_account else 0,
                stopOrders=remote_account.stopOrders if remote_account else 0,
                maxDailyLossPct=settings.maxDailyLossPct,
                feeLevel=remote_account.feeLevel if remote_account else None,
                makerFeeRate=remote_account.makerFeeRate if remote_account else None,
                takerFeeRate=remote_account.takerFeeRate if remote_account else None,
                useLastTradedPriceForStops=(
                    remote_account.useLastTradedPriceForStops if remote_account else None
                ),
                lastSyncedAt=(
                    self.remoteSnapshot.syncedAt if self.remoteSnapshot else None
                ),
                lastOrderId=(
                    self.remoteSnapshot.lastOrderId if self.remoteSnapshot else None
                ),
            ),
            paperAccount=self.paper_account_snapshot(),
            paperPerformance=self.paper_performance_snapshot(
                "contrarian" if settings.contrarianExecutionEnabled else "normal"
            ),
            livePerformance=(
                self.live_performance_snapshot(
                    "contrarian" if settings.contrarianExecutionEnabled else "normal"
                )
                if settings.botMode in {"testnet", "mainnet"} and remote_account
                else None
            ),
            watchlist=watchlist,
            marketCharts=[
                MarketChart(
                    symbol=market.symbol,
                    candles=list(market.candles),
                )
                for market in self.markets.values()
            ],
            signals=list(reversed(self.signals)),
            positions=list(self.positions.values()),
            remotePositions=[
                DashboardRemotePositionSnapshot(
                    symbol=position.symbol,
                    side=position.side,
                    size=position.size,
                    entryPrice=position.entryPrice,
                    notionalUsd=position.notionalUsd,
                    marginUsd=position.marginUsd,
                    fundingUsd=position.fundingUsd,
                    isolated=position.isolated,
                    openedAt=position.openedAt,
                    updatedAt=position.updatedAt,
                )
                for position in (self.remoteSnapshot.positions if self.remoteSnapshot else [])
            ],
            openOrders=[
                OpenOrderSnapshot(
                    orderId=order.orderId,
                    clientOrderId=order.clientOrderId,
                    symbol=order.symbol,
                    side=order.side,
                    orderType=order.orderType,
                    price=order.price,
                    stopPrice=order.stopPrice,
                    initialAmount=order.initialAmount,
                    filledAmount=order.filledAmount,
                    cancelledAmount=order.cancelledAmount,
                    remainingAmount=order.remainingAmount,
                    notionalUsd=order.notionalUsd,
                    reduceOnly=order.reduceOnly,
                    createdAt=order.createdAt,
                    updatedAt=order.updatedAt,
                )
                for order in (self.remoteSnapshot.openOrders if self.remoteSnapshot else [])
            ],
            events=list(reversed(self.events)),
            tradeActivity=list(reversed(self.tradeActivity)),
            mlModel=ml_model,
        )

    def _calculate_pnl(self, side: str, entry_price: float, exit_price: float, size: float) -> float:
        if side == "long":
            return (exit_price - entry_price) * size
        return (entry_price - exit_price) * size


class ComparisonPaperPosition(BaseModel):
    symbol: str
    signalId: str | None = None
    setup: str | None = None
    side: str
    confidence: float | None = None
    size: float
    notionalUsd: float
    entryPrice: float
    markPrice: float
    stopLoss: float
    takeProfit: float
    pnlUsd: float
    pnlPct: float
    riskState: str
    executionMode: ExecutionMode
    riskUsd: float | None = None
    openedAt: datetime


class PersistedSymbolState(BaseModel):
    symbol: str
    baselinePrice: float
    lastPrice: float
    spreadBps: float
    bidPrice: float | None = None
    askPrice: float | None = None
    tickSize: float | None = None
    lotSize: float | None = None
    minOrderSizeUsd: float | None = None
    maxLeverage: int | None = None
    updatedAt: datetime | None = None
    priceHistory: list[float] = Field(default_factory=list)
    candles: list[ChartCandle] = Field(default_factory=list)


class PersistedRemoteAccountSnapshot(BaseModel):
    equityUsd: float
    availableMarginUsd: float
    balanceUsd: float
    openPositions: int
    availableToWithdrawUsd: float | None = None
    pendingBalanceUsd: float | None = None
    totalMarginUsedUsd: float | None = None
    crossMaintenanceMarginUsd: float | None = None
    openOrders: int = 0
    stopOrders: int = 0
    feeLevel: int | None = None
    makerFeeRate: float | None = None
    takerFeeRate: float | None = None
    useLastTradedPriceForStops: bool | None = None
    updatedAt: datetime | None = None

    @classmethod
    def from_runtime(
        cls,
        snapshot: PacificaRemoteAccountSnapshot,
    ) -> "PersistedRemoteAccountSnapshot":
        return cls(
            equityUsd=snapshot.equityUsd,
            availableMarginUsd=snapshot.availableMarginUsd,
            balanceUsd=snapshot.balanceUsd,
            openPositions=snapshot.openPositions,
            availableToWithdrawUsd=snapshot.availableToWithdrawUsd,
            pendingBalanceUsd=snapshot.pendingBalanceUsd,
            totalMarginUsedUsd=snapshot.totalMarginUsedUsd,
            crossMaintenanceMarginUsd=snapshot.crossMaintenanceMarginUsd,
            openOrders=snapshot.openOrders,
            stopOrders=snapshot.stopOrders,
            feeLevel=snapshot.feeLevel,
            makerFeeRate=snapshot.makerFeeRate,
            takerFeeRate=snapshot.takerFeeRate,
            useLastTradedPriceForStops=snapshot.useLastTradedPriceForStops,
            updatedAt=snapshot.updatedAt,
        )

    def to_runtime(self) -> PacificaRemoteAccountSnapshot:
        return PacificaRemoteAccountSnapshot(
            equityUsd=self.equityUsd,
            availableMarginUsd=self.availableMarginUsd,
            balanceUsd=self.balanceUsd,
            openPositions=self.openPositions,
            availableToWithdrawUsd=self.availableToWithdrawUsd,
            pendingBalanceUsd=self.pendingBalanceUsd,
            totalMarginUsedUsd=self.totalMarginUsedUsd,
            crossMaintenanceMarginUsd=self.crossMaintenanceMarginUsd,
            openOrders=self.openOrders,
            stopOrders=self.stopOrders,
            feeLevel=self.feeLevel,
            makerFeeRate=self.makerFeeRate,
            takerFeeRate=self.takerFeeRate,
            useLastTradedPriceForStops=self.useLastTradedPriceForStops,
            updatedAt=self.updatedAt,
        )


class PersistedRemotePositionSnapshot(BaseModel):
    symbol: str
    side: str
    size: float
    entryPrice: float
    notionalUsd: float
    marginUsd: float | None = None
    fundingUsd: float | None = None
    isolated: bool = False
    openedAt: datetime | None = None
    updatedAt: datetime | None = None

    @classmethod
    def from_runtime(
        cls,
        snapshot: PacificaRemotePositionSnapshot,
    ) -> "PersistedRemotePositionSnapshot":
        return cls(
            symbol=snapshot.symbol,
            side=snapshot.side,
            size=snapshot.size,
            entryPrice=snapshot.entryPrice,
            notionalUsd=snapshot.notionalUsd,
            marginUsd=snapshot.marginUsd,
            fundingUsd=snapshot.fundingUsd,
            isolated=snapshot.isolated,
            openedAt=snapshot.openedAt,
            updatedAt=snapshot.updatedAt,
        )

    def to_runtime(self) -> PacificaRemotePositionSnapshot:
        return PacificaRemotePositionSnapshot(
            symbol=self.symbol,
            side=self.side,
            size=self.size,
            entryPrice=self.entryPrice,
            notionalUsd=self.notionalUsd,
            marginUsd=self.marginUsd,
            fundingUsd=self.fundingUsd,
            isolated=self.isolated,
            openedAt=self.openedAt,
            updatedAt=self.updatedAt,
        )


class PersistedRemoteOpenOrderSnapshot(BaseModel):
    orderId: int
    clientOrderId: str | None = None
    symbol: str
    side: str
    orderType: str
    price: float
    stopPrice: float | None = None
    initialAmount: float
    filledAmount: float
    cancelledAmount: float
    remainingAmount: float
    notionalUsd: float
    reduceOnly: bool
    createdAt: datetime | None = None
    updatedAt: datetime | None = None

    @classmethod
    def from_runtime(
        cls,
        snapshot: PacificaRemoteOpenOrderSnapshot,
    ) -> "PersistedRemoteOpenOrderSnapshot":
        return cls(
            orderId=snapshot.orderId,
            clientOrderId=snapshot.clientOrderId,
            symbol=snapshot.symbol,
            side=snapshot.side,
            orderType=snapshot.orderType,
            price=snapshot.price,
            stopPrice=snapshot.stopPrice,
            initialAmount=snapshot.initialAmount,
            filledAmount=snapshot.filledAmount,
            cancelledAmount=snapshot.cancelledAmount,
            remainingAmount=snapshot.remainingAmount,
            notionalUsd=snapshot.notionalUsd,
            reduceOnly=snapshot.reduceOnly,
            createdAt=snapshot.createdAt,
            updatedAt=snapshot.updatedAt,
        )

    def to_runtime(self) -> PacificaRemoteOpenOrderSnapshot:
        return PacificaRemoteOpenOrderSnapshot(
            orderId=self.orderId,
            clientOrderId=self.clientOrderId,
            symbol=self.symbol,
            side=self.side,
            orderType=self.orderType,
            price=self.price,
            stopPrice=self.stopPrice,
            initialAmount=self.initialAmount,
            filledAmount=self.filledAmount,
            cancelledAmount=self.cancelledAmount,
            remainingAmount=self.remainingAmount,
            notionalUsd=self.notionalUsd,
            reduceOnly=self.reduceOnly,
            createdAt=self.createdAt,
            updatedAt=self.updatedAt,
        )


class PersistedRemoteTradingSnapshot(BaseModel):
    account: PersistedRemoteAccountSnapshot
    positions: list[PersistedRemotePositionSnapshot] = Field(default_factory=list)
    openOrders: list[PersistedRemoteOpenOrderSnapshot] = Field(default_factory=list)
    lastOrderId: int | None = None
    syncedAt: datetime | None = None

    @classmethod
    def from_runtime(
        cls,
        snapshot: RemoteTradingSnapshot,
    ) -> "PersistedRemoteTradingSnapshot":
        return cls(
            account=PersistedRemoteAccountSnapshot.from_runtime(snapshot.account),
            positions=[
                PersistedRemotePositionSnapshot.from_runtime(position)
                for position in snapshot.positions
            ],
            openOrders=[
                PersistedRemoteOpenOrderSnapshot.from_runtime(order)
                for order in snapshot.openOrders
            ],
            lastOrderId=snapshot.lastOrderId,
            syncedAt=snapshot.syncedAt,
        )

    def to_runtime(self) -> RemoteTradingSnapshot:
        return RemoteTradingSnapshot(
            account=self.account.to_runtime(),
            positions=[position.to_runtime() for position in self.positions],
            openOrders=[order.to_runtime() for order in self.openOrders],
            lastOrderId=self.lastOrderId,
            syncedAt=self.syncedAt,
        )


class PersistedOperatorState(BaseModel):
    paused: bool = False
    sessionAccountAddress: str | None = None
    lastOperatorAction: str | None = None
    lastOperatorActionAt: datetime | None = None
    lastAccountSyncAttemptAt: datetime | None = None


class PersistedEngineState(BaseModel):
    schemaVersion: int = 4
    persistedAt: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    startingEquityUsd: float
    realizedPnlUsd: float
    comparisonRealizedPnlUsd: float = 0.0
    paperPeakEquityUsd: float = 0.0
    paperMaxDrawdownUsd: float = 0.0
    paperMaxDrawdownPct: float = 0.0
    comparisonPeakEquityUsd: float = 0.0
    comparisonMaxDrawdownUsd: float = 0.0
    comparisonMaxDrawdownPct: float = 0.0
    liveStartingEquityUsd: float | None = None
    livePeakEquityUsd: float = 0.0
    liveMaxDrawdownUsd: float = 0.0
    liveMaxDrawdownPct: float = 0.0
    markets: list[PersistedSymbolState] = Field(default_factory=list)
    positions: list[EnginePosition] = Field(default_factory=list)
    comparisonPositions: list[ComparisonPaperPosition] = Field(default_factory=list)
    signals: list[StrategySignal] = Field(default_factory=list)
    events: list[EventLog] = Field(default_factory=list)
    tradeActivity: list[TradeActivity] = Field(default_factory=list)
    closedPaperTrades: list[PaperClosedTrade] = Field(default_factory=list)
    closedLiveTrades: list[PaperClosedTrade] = Field(default_factory=list)
    remoteSnapshot: PersistedRemoteTradingSnapshot | None = None
    operator: PersistedOperatorState = Field(default_factory=PersistedOperatorState)
