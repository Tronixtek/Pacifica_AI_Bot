export type BotMode = "paper" | "testnet" | "mainnet";
export type NetworkMode = "testnet" | "mainnet";
export type SystemStatus = "healthy" | "degraded" | "offline";
export type ProbeStatus = "healthy" | "degraded" | "offline" | "skipped";
export type AccountSource = "paper" | "pacifica";
export type AccountConfigurationSource = "env" | "session";
export type SignalSetup = "breakout" | "liquidity_sweep" | "manual_test";
export type SignalBias = "long" | "short";
export type SignalStatus = "candidate" | "approved" | "blocked" | "executed";
export type RiskState = "normal" | "warning" | "reduced";
export type ExecutionMode = "normal" | "contrarian";
export type EventLevel = "info" | "warning" | "success";
export type OrderSide = "buy" | "sell";
export type PaperTradeBook = "primary" | "comparison";
export type PaperTradeOutcome = "take_profit" | "stop_loss" | "breakeven" | "manual" | "other";
export type TradeActivityKind =
  | "paper_entry"
  | "paper_exit"
  | "live_execution_submitted"
  | "live_execution_failed"
  | "live_exit";

export interface ServiceHealth {
  id: string;
  label: string;
  status: SystemStatus;
  message: string;
}

export interface BotSnapshot {
  mode: BotMode;
  network: NetworkMode;
  status: SystemStatus;
  liveTradingEnabled: boolean;
  builderCode: string | null;
  agentWalletConfigured: boolean;
}

export interface OperatorSnapshot {
  paused: boolean;
  canSyncAccount: boolean;
  canPreviewOrders: boolean;
  canSubmitOrders: boolean;
  lastAction: string | null;
  lastActionAt: string | null;
}

export interface AccountSnapshot {
  source: AccountSource;
  equityUsd: number;
  availableMarginUsd: number;
  balanceUsd: number | null;
  availableToWithdrawUsd: number | null;
  pendingBalanceUsd: number | null;
  totalMarginUsedUsd: number | null;
  crossMaintenanceMarginUsd: number | null;
  pnlUsd: number;
  pnlLabel: string;
  openPositions: number;
  openOrders: number;
  stopOrders: number;
  maxDailyLossPct: number;
  feeLevel: number | null;
  makerFeeRate: number | null;
  takerFeeRate: number | null;
  useLastTradedPriceForStops: boolean | null;
  lastSyncedAt: string | null;
  lastOrderId: number | null;
}

export interface PaperAccountSnapshot {
  startingEquityUsd: number;
  equityUsd: number;
  availableMarginUsd: number;
  realizedPnlUsd: number;
  unrealizedPnlUsd: number;
  openPositions: number;
}

export interface MarketSnapshot {
  symbol: string;
  lastPrice: number;
  movePctFromOpen: number;
  spreadBps: number;
}

export interface ChartCandle {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
}

export interface MarketChart {
  symbol: string;
  candles: ChartCandle[];
}

export interface StrategySignal {
  id: string;
  symbol: string;
  setup: SignalSetup;
  bias: SignalBias;
  confidence: number;
  entryPrice: number;
  stopLoss: number;
  takeProfit: number;
  size: number;
  notionalUsd: number;
  status: SignalStatus;
  reason: string;
  createdAt: string;
}

export interface EnginePosition {
  symbol: string;
  side: SignalBias;
  size: number;
  entryPrice: number;
  markPrice: number;
  stopLoss: number;
  takeProfit: number;
  pnlUsd: number;
  pnlPct: number;
  riskState: RiskState;
  openedAt: string;
  signalId: string | null;
  setup: SignalSetup | null;
  confidence: number | null;
  notionalUsd: number | null;
  riskUsd: number | null;
  executionMode: ExecutionMode | null;
}

export interface RemotePositionSnapshot {
  symbol: string;
  side: SignalBias;
  size: number;
  entryPrice: number;
  notionalUsd: number;
  marginUsd: number | null;
  fundingUsd: number | null;
  isolated: boolean;
  openedAt: string | null;
  updatedAt: string | null;
}

export interface OpenOrderSnapshot {
  orderId: number;
  clientOrderId: string | null;
  symbol: string;
  side: OrderSide;
  orderType: string;
  price: number;
  stopPrice: number | null;
  initialAmount: number;
  filledAmount: number;
  cancelledAmount: number;
  remainingAmount: number;
  notionalUsd: number;
  reduceOnly: boolean;
  createdAt: string | null;
  updatedAt: string | null;
}

export interface EventLog {
  id: string;
  level: EventLevel;
  message: string;
  createdAt: string;
}

export interface TradeActivity {
  id: string;
  kind: TradeActivityKind;
  symbol: string;
  title: string;
  message: string;
  level: EventLevel;
  side: SignalBias | null;
  price: number | null;
  size: number | null;
  notionalUsd: number | null;
  pnlUsd: number | null;
  signalId: string | null;
  orderId: number | null;
  createdAt: string;
}

export interface MlModelSnapshot {
  ready: boolean;
  summary: string;
  trainingSource: string | null;
  trainingSamples: number;
  trainingSymbols: string[];
  lastTrainedAt: string | null;
  validationSamples: number;
  decisionSamples: number;
  decisionCoverage: number;
  decisionPrecision: number | null;
  longPrecision: number | null;
  shortPrecision: number | null;
}

export interface PaperClosedTrade {
  id: string;
  book: PaperTradeBook;
  executionMode: ExecutionMode;
  signalId: string | null;
  symbol: string;
  setup: SignalSetup | null;
  side: SignalBias;
  confidence: number | null;
  entryPrice: number;
  exitPrice: number;
  stopLoss: number;
  takeProfit: number;
  size: number;
  notionalUsd: number;
  riskUsd: number | null;
  pnlUsd: number;
  rMultiple: number | null;
  outcome: PaperTradeOutcome;
  exitReason: string;
  openedAt: string;
  closedAt: string;
}

export interface PaperPerformanceSummary {
  book: PaperTradeBook;
  executionMode: ExecutionMode;
  closedTrades: number;
  wins: number;
  losses: number;
  breakevenTrades: number;
  winRate: number | null;
  takeProfitHits: number;
  stopLossHits: number;
  breakevenExits: number;
  tpToSlRatio: number | null;
  averageRMultiple: number | null;
  profitFactor: number | null;
  netPnlUsd: number;
  grossProfitUsd: number;
  grossLossUsd: number;
  averageWinUsd: number | null;
  averageLossUsd: number | null;
  peakEquityUsd: number;
  endingEquityUsd: number;
  maxDrawdownUsd: number;
  maxDrawdownPct: number;
  openPositions: number;
  lastClosedAt: string | null;
}

export interface PaperPerformanceSnapshot {
  currentMode: ExecutionMode;
  comparisonMode: ExecutionMode;
  currentModeSummary: PaperPerformanceSummary;
  comparisonModeSummary: PaperPerformanceSummary;
  recentClosedTrades: PaperClosedTrade[];
  pairedClosedSignals: number;
  comparisonMethod: string;
}

export interface LivePerformanceSummary {
  executionMode: ExecutionMode;
  startingEquityUsd: number;
  currentEquityUsd: number;
  netPnlUsd: number;
  closedTrades: number;
  wins: number;
  losses: number;
  breakevenTrades: number;
  winRate: number | null;
  takeProfitHits: number;
  stopLossHits: number;
  breakevenExits: number;
  averageRMultiple: number | null;
  profitFactor: number | null;
  averageWinUsd: number | null;
  averageLossUsd: number | null;
  peakEquityUsd: number;
  maxDrawdownUsd: number;
  maxDrawdownPct: number;
  openPositions: number;
  lastClosedAt: string | null;
}

export interface LivePerformanceSnapshot {
  summary: LivePerformanceSummary;
  recentClosedTrades: PaperClosedTrade[];
  trackingBasis: string;
}

export interface DashboardSnapshot {
  generatedAt: string;
  bot: BotSnapshot;
  operator: OperatorSnapshot;
  services: ServiceHealth[];
  account: AccountSnapshot;
  paperAccount: PaperAccountSnapshot;
  watchlist: MarketSnapshot[];
  marketCharts: MarketChart[];
  signals: StrategySignal[];
  positions: EnginePosition[];
  remotePositions: RemotePositionSnapshot[];
  openOrders: OpenOrderSnapshot[];
  events: EventLog[];
  tradeActivity: TradeActivity[];
  mlModel: MlModelSnapshot;
  paperPerformance: PaperPerformanceSnapshot;
  livePerformance: LivePerformanceSnapshot | null;
}

export interface OperatorActionResponse {
  ok: boolean;
  message: string;
  operator: OperatorSnapshot;
  generatedAt: string;
}

export interface SignalPreviewResponse {
  ok: boolean;
  message: string;
  operator: OperatorSnapshot;
  signal: StrategySignal | null;
  payload: Record<string, unknown>;
  marketSpecApplied: boolean;
  generatedAt: string;
}

export interface ConfigReadiness {
  mode: BotMode;
  network: NetworkMode;
  restUrl: string;
  websocketUrl: string;
  useSimulatedFeed: boolean;
  preferWebsocketFeed: boolean;
  liveTradingEnabled: boolean;
  accountConfigured: boolean;
  effectiveAccountAddress: string | null;
  accountConfigurationSource: AccountConfigurationSource | null;
  agentKeyConfigured: boolean;
  apiConfigKeyConfigured: boolean;
  builderCode: string | null;
  symbols: string[];
}

export interface DiagnosticProbe {
  id: string;
  label: string;
  status: ProbeStatus;
  message: string;
  details: Record<string, unknown>;
}

export interface DiagnosticsResponse {
  generatedAt: string;
  config: ConfigReadiness;
  services: ServiceHealth[];
  probes: DiagnosticProbe[];
}

export interface AccountLinkResponse {
  ok: boolean;
  message: string;
  operator: OperatorSnapshot;
  linkedAccountAddress: string | null;
  accountConfigurationSource: AccountConfigurationSource | null;
  generatedAt: string;
}

export interface SmokeTestOrderRequest {
  symbol: string;
}
