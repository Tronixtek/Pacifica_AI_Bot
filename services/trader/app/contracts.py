from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

BotMode = Literal["paper", "testnet", "mainnet"]
NetworkMode = Literal["testnet", "mainnet"]
SystemStatus = Literal["healthy", "degraded", "offline"]
AccountSource = Literal["paper", "pacifica"]
AccountConfigurationSource = Literal["env", "session"]
SignalSetup = Literal["breakout", "liquidity_sweep", "manual_test"]
SignalBias = Literal["long", "short"]
SignalStatus = Literal["candidate", "approved", "blocked", "executed"]
RiskState = Literal["normal", "warning", "reduced"]
ExecutionMode = Literal["normal", "contrarian"]
EventLevel = Literal["info", "warning", "success"]
ProbeStatus = Literal["healthy", "degraded", "offline", "skipped"]
OrderSide = Literal["buy", "sell"]
PaperTradeBook = Literal["primary", "comparison"]
PaperTradeOutcome = Literal["take_profit", "stop_loss", "breakeven", "manual", "other"]
TradeActivityKind = Literal[
    "paper_entry",
    "paper_exit",
    "live_execution_submitted",
    "live_execution_failed",
    "live_exit",
]


class ServiceHealth(BaseModel):
    id: str
    label: str
    status: SystemStatus
    message: str


class BotSnapshot(BaseModel):
    mode: BotMode
    network: NetworkMode
    status: SystemStatus
    liveTradingEnabled: bool
    builderCode: str | None
    agentWalletConfigured: bool


class OperatorSnapshot(BaseModel):
    paused: bool
    canSyncAccount: bool
    canPreviewOrders: bool
    canSubmitOrders: bool
    lastAction: str | None = None
    lastActionAt: datetime | None = None


class AccountSnapshot(BaseModel):
    source: AccountSource
    equityUsd: float
    availableMarginUsd: float
    balanceUsd: float | None = None
    availableToWithdrawUsd: float | None = None
    pendingBalanceUsd: float | None = None
    totalMarginUsedUsd: float | None = None
    crossMaintenanceMarginUsd: float | None = None
    pnlUsd: float
    pnlLabel: str
    openPositions: int
    openOrders: int
    stopOrders: int
    maxDailyLossPct: float
    feeLevel: int | None = None
    makerFeeRate: float | None = None
    takerFeeRate: float | None = None
    useLastTradedPriceForStops: bool | None = None
    lastSyncedAt: datetime | None = None
    lastOrderId: int | None = None


class PaperAccountSnapshot(BaseModel):
    startingEquityUsd: float
    equityUsd: float
    availableMarginUsd: float
    realizedPnlUsd: float
    unrealizedPnlUsd: float
    openPositions: int


class MarketSnapshot(BaseModel):
    symbol: str
    lastPrice: float
    movePctFromOpen: float
    spreadBps: float


class ChartCandle(BaseModel):
    time: datetime
    open: float
    high: float
    low: float
    close: float


class MarketChart(BaseModel):
    symbol: str
    candles: list[ChartCandle]


class StrategySignal(BaseModel):
    id: str
    symbol: str
    setup: SignalSetup
    bias: SignalBias
    confidence: float
    entryPrice: float
    stopLoss: float
    takeProfit: float
    size: float
    notionalUsd: float
    status: SignalStatus
    reason: str
    createdAt: datetime


class EnginePosition(BaseModel):
    symbol: str
    side: SignalBias
    size: float
    entryPrice: float
    markPrice: float
    stopLoss: float
    takeProfit: float
    pnlUsd: float
    pnlPct: float
    riskState: RiskState
    openedAt: datetime
    signalId: str | None = None
    setup: SignalSetup | None = None
    confidence: float | None = None
    notionalUsd: float | None = None
    riskUsd: float | None = None
    executionMode: ExecutionMode | None = None


class RemotePositionSnapshot(BaseModel):
    symbol: str
    side: SignalBias
    size: float
    entryPrice: float
    notionalUsd: float
    marginUsd: float | None = None
    fundingUsd: float | None = None
    isolated: bool
    openedAt: datetime | None = None
    updatedAt: datetime | None = None


class OpenOrderSnapshot(BaseModel):
    orderId: int
    clientOrderId: str | None = None
    symbol: str
    side: OrderSide
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


class EventLog(BaseModel):
    id: str
    level: EventLevel
    message: str
    createdAt: datetime


class TradeActivity(BaseModel):
    id: str
    kind: TradeActivityKind
    symbol: str
    title: str
    message: str
    level: EventLevel
    side: SignalBias | None = None
    price: float | None = None
    size: float | None = None
    notionalUsd: float | None = None
    pnlUsd: float | None = None
    signalId: str | None = None
    orderId: int | None = None
    createdAt: datetime


class MlModelSnapshot(BaseModel):
    ready: bool
    summary: str
    trainingSource: str | None = None
    trainingSamples: int = 0
    trainingSymbols: list[str] = Field(default_factory=list)
    lastTrainedAt: datetime | None = None
    validationSamples: int = 0
    decisionSamples: int = 0
    decisionCoverage: float = 0.0
    decisionPrecision: float | None = None
    longPrecision: float | None = None
    shortPrecision: float | None = None


class PaperClosedTrade(BaseModel):
    id: str
    book: PaperTradeBook
    executionMode: ExecutionMode
    signalId: str | None = None
    symbol: str
    setup: SignalSetup | None = None
    side: SignalBias
    confidence: float | None = None
    entryPrice: float
    exitPrice: float
    stopLoss: float
    takeProfit: float
    size: float
    notionalUsd: float
    riskUsd: float | None = None
    pnlUsd: float
    rMultiple: float | None = None
    outcome: PaperTradeOutcome
    exitReason: str
    openedAt: datetime
    closedAt: datetime


class PaperPerformanceSummary(BaseModel):
    book: PaperTradeBook
    executionMode: ExecutionMode
    closedTrades: int = 0
    wins: int = 0
    losses: int = 0
    breakevenTrades: int = 0
    winRate: float | None = None
    takeProfitHits: int = 0
    stopLossHits: int = 0
    breakevenExits: int = 0
    tpToSlRatio: float | None = None
    averageRMultiple: float | None = None
    profitFactor: float | None = None
    netPnlUsd: float = 0.0
    grossProfitUsd: float = 0.0
    grossLossUsd: float = 0.0
    averageWinUsd: float | None = None
    averageLossUsd: float | None = None
    peakEquityUsd: float = 0.0
    endingEquityUsd: float = 0.0
    maxDrawdownUsd: float = 0.0
    maxDrawdownPct: float = 0.0
    openPositions: int = 0
    lastClosedAt: datetime | None = None


class PaperPerformanceSnapshot(BaseModel):
    currentMode: ExecutionMode
    comparisonMode: ExecutionMode
    currentModeSummary: PaperPerformanceSummary
    comparisonModeSummary: PaperPerformanceSummary
    recentClosedTrades: list[PaperClosedTrade] = Field(default_factory=list)
    pairedClosedSignals: int = 0
    comparisonMethod: str


class LivePerformanceSummary(BaseModel):
    executionMode: ExecutionMode
    startingEquityUsd: float = 0.0
    currentEquityUsd: float = 0.0
    netPnlUsd: float = 0.0
    closedTrades: int = 0
    wins: int = 0
    losses: int = 0
    breakevenTrades: int = 0
    winRate: float | None = None
    takeProfitHits: int = 0
    stopLossHits: int = 0
    breakevenExits: int = 0
    averageRMultiple: float | None = None
    profitFactor: float | None = None
    averageWinUsd: float | None = None
    averageLossUsd: float | None = None
    peakEquityUsd: float = 0.0
    maxDrawdownUsd: float = 0.0
    maxDrawdownPct: float = 0.0
    openPositions: int = 0
    lastClosedAt: datetime | None = None


class LivePerformanceSnapshot(BaseModel):
    summary: LivePerformanceSummary
    recentClosedTrades: list[PaperClosedTrade] = Field(default_factory=list)
    trackingBasis: str


class DashboardSnapshot(BaseModel):
    generatedAt: datetime
    bot: BotSnapshot
    operator: OperatorSnapshot
    services: list[ServiceHealth]
    account: AccountSnapshot
    paperAccount: PaperAccountSnapshot
    watchlist: list[MarketSnapshot]
    marketCharts: list[MarketChart]
    signals: list[StrategySignal]
    positions: list[EnginePosition]
    remotePositions: list[RemotePositionSnapshot]
    openOrders: list[OpenOrderSnapshot]
    events: list[EventLog]
    tradeActivity: list[TradeActivity]
    mlModel: MlModelSnapshot
    paperPerformance: PaperPerformanceSnapshot
    livePerformance: LivePerformanceSnapshot | None = None


class OperatorActionResponse(BaseModel):
    ok: bool
    message: str
    operator: OperatorSnapshot
    generatedAt: datetime = Field(default_factory=datetime.utcnow)


class SignalPreviewResponse(BaseModel):
    ok: bool
    message: str
    operator: OperatorSnapshot
    signal: StrategySignal | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    marketSpecApplied: bool = False
    generatedAt: datetime = Field(default_factory=datetime.utcnow)


class HealthResponse(BaseModel):
    status: SystemStatus
    mode: BotMode
    network: NetworkMode
    liveTradingEnabled: bool
    message: str
    checkedAt: datetime = Field(default_factory=datetime.utcnow)


class ConfigReadiness(BaseModel):
    mode: BotMode
    network: NetworkMode
    restUrl: str
    websocketUrl: str
    useSimulatedFeed: bool
    preferWebsocketFeed: bool
    liveTradingEnabled: bool
    accountConfigured: bool
    effectiveAccountAddress: str | None = None
    accountConfigurationSource: AccountConfigurationSource | None = None
    agentKeyConfigured: bool
    apiConfigKeyConfigured: bool
    builderCode: str | None
    symbols: list[str]


class DiagnosticProbe(BaseModel):
    id: str
    label: str
    status: ProbeStatus
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class DiagnosticsResponse(BaseModel):
  generatedAt: datetime
  config: ConfigReadiness
  services: list[ServiceHealth]
  probes: list[DiagnosticProbe]


class AccountLinkRequest(BaseModel):
    accountAddress: str


class PaperBalanceTopUpRequest(BaseModel):
    amountUsd: float = Field(gt=0)


class SmokeTestOrderRequest(BaseModel):
    symbol: str = Field(min_length=2, max_length=16)


class AccountLinkResponse(BaseModel):
    ok: bool
    message: str
    operator: OperatorSnapshot
    linkedAccountAddress: str | None = None
    accountConfigurationSource: AccountConfigurationSource | None = None
    generatedAt: datetime = Field(default_factory=datetime.utcnow)
