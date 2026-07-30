import type {
  DashboardSnapshot,
  DiagnosticProbe,
  DiagnosticsResponse,
  OperatorActionResponse,
  SignalPreviewResponse
} from "@vtfx-mt5-bot/shared";

const DEFAULT_API_URL = process.env.NEXT_PUBLIC_TRADER_API_URL ?? "http://127.0.0.1:8011";

export interface DashboardData {
  snapshot: DashboardSnapshot;
  diagnostics: DiagnosticsResponse;
  usingFallback: boolean;
}

export async function getDashboardData(): Promise<DashboardData> {
  try {
    const fallback = buildFallbackData();
    const [snapshotResponse, diagnosticsResponse] = await Promise.all([
      fetch(`${DEFAULT_API_URL}/api/overview`, {
        cache: "no-store"
      }),
      fetch(`${DEFAULT_API_URL}/api/diagnostics?live_probe=true`, {
        cache: "no-store"
      })
    ]);

    if (!snapshotResponse.ok) {
      throw new Error(`Trader overview API returned ${snapshotResponse.status}`);
    }

    if (!diagnosticsResponse.ok) {
      throw new Error(`Trader diagnostics API returned ${diagnosticsResponse.status}`);
    }

    return {
      snapshot: normalizeSnapshot(await snapshotResponse.json(), fallback.snapshot),
      diagnostics: normalizeDiagnostics(await diagnosticsResponse.json(), fallback.diagnostics),
      usingFallback: false
    };
  } catch {
    return buildFallbackData();
  }
}

export async function pauseStrategy(): Promise<OperatorActionResponse> {
  return postOperatorAction("/api/operator/pause");
}

export async function resumeStrategy(): Promise<OperatorActionResponse> {
  return postOperatorAction("/api/operator/resume");
}

export async function syncAccount(): Promise<OperatorActionResponse> {
  return postOperatorAction("/api/operator/sync-account");
}

export async function resetPaperAccount(): Promise<OperatorActionResponse> {
  return postOperatorAction("/api/operator/paper-account/reset");
}

export async function topUpPaperAccount(amountUsd: number): Promise<OperatorActionResponse> {
  const response = await fetch(`${DEFAULT_API_URL}/api/operator/paper-account/top-up`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ amountUsd })
  });

  if (!response.ok) {
    throw new Error(`Paper balance top-up returned ${response.status}`);
  }

  return (await response.json()) as OperatorActionResponse;
}

export async function previewSignal(signalId: string): Promise<SignalPreviewResponse> {
  const response = await fetch(`${DEFAULT_API_URL}/api/operator/signals/${signalId}/preview`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    }
  });

  if (!response.ok) {
    throw new Error(`Preview request returned ${response.status}`);
  }

  return (await response.json()) as SignalPreviewResponse;
}

export async function submitSmokeTestOrder(symbol: string): Promise<OperatorActionResponse> {
  const response = await fetch(`${DEFAULT_API_URL}/api/operator/test-order`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ symbol })
  });

  if (!response.ok) {
    throw new Error(`Smoke-test order request returned ${response.status}`);
  }

  return (await response.json()) as OperatorActionResponse;
}

function normalizeSnapshot(raw: unknown, fallback: DashboardSnapshot): DashboardSnapshot {
  const payload = asRecord(raw);
  const account = asRecord(payload.account);
  const bot = asRecord(payload.bot);
  const operator = asRecord(payload.operator);
  const paperAccount = asRecord(payload.paperAccount);
  const mlModel = asRecord(payload.mlModel);
  const paperPerformance = asRecord(payload.paperPerformance);
  const currentModeSummary = asRecord(paperPerformance.currentModeSummary);
  const comparisonModeSummary = asRecord(paperPerformance.comparisonModeSummary);
  const livePerformance = asRecord(payload.livePerformance);
  const livePerformanceSummary = asRecord(livePerformance.summary);

  return {
    ...fallback,
    ...payload,
    bot: {
      ...fallback.bot,
      ...bot
    },
    operator: {
      ...fallback.operator,
      ...operator
    },
    account: {
      ...fallback.account,
      ...account,
      source:
        account.source === "mt5" || account.source === "paper"
          ? account.source
          : fallback.account.source,
      pnlUsd:
        typeof account.pnlUsd === "number"
          ? account.pnlUsd
          : typeof account.dailyPnlUsd === "number"
            ? account.dailyPnlUsd
            : fallback.account.pnlUsd,
      pnlLabel:
        typeof account.pnlLabel === "string"
          ? account.pnlLabel
          : typeof account.dailyPnlUsd === "number"
            ? "Legacy snapshot PnL from the backend."
            : fallback.account.pnlLabel,
      openOrders: typeof account.openOrders === "number" ? account.openOrders : 0,
      lastSyncedAt: typeof account.lastSyncedAt === "string" ? account.lastSyncedAt : null
    },
    paperAccount: {
      ...fallback.paperAccount,
      ...paperAccount,
      startingEquityUsd:
        typeof paperAccount.startingEquityUsd === "number"
          ? paperAccount.startingEquityUsd
          : fallback.paperAccount.startingEquityUsd,
      equityUsd:
        typeof paperAccount.equityUsd === "number"
          ? paperAccount.equityUsd
          : account.source === "paper" && typeof account.equityUsd === "number"
            ? account.equityUsd
            : fallback.paperAccount.equityUsd,
      availableMarginUsd:
        typeof paperAccount.availableMarginUsd === "number"
          ? paperAccount.availableMarginUsd
          : account.source === "paper" && typeof account.availableMarginUsd === "number"
            ? account.availableMarginUsd
            : fallback.paperAccount.availableMarginUsd,
      realizedPnlUsd:
        typeof paperAccount.realizedPnlUsd === "number" ? paperAccount.realizedPnlUsd : fallback.paperAccount.realizedPnlUsd,
      unrealizedPnlUsd:
        typeof paperAccount.unrealizedPnlUsd === "number" ? paperAccount.unrealizedPnlUsd : fallback.paperAccount.unrealizedPnlUsd,
      openPositions:
        typeof paperAccount.openPositions === "number"
          ? paperAccount.openPositions
          : Array.isArray(payload.positions)
            ? payload.positions.length
            : fallback.paperAccount.openPositions
    },
    services: asArray(payload.services, fallback.services),
    watchlist: asArray(payload.watchlist, fallback.watchlist),
    marketCharts: asArray(payload.marketCharts, fallback.marketCharts),
    signals: asArray(payload.signals, fallback.signals),
    positions: asArray(payload.positions, fallback.positions),
    remotePositions: asArray(payload.remotePositions, []),
    openOrders: asArray(payload.openOrders, []),
    events: asArray(payload.events, fallback.events),
    tradeActivity: asArray(payload.tradeActivity, fallback.tradeActivity),
    mlModel: {
      ...fallback.mlModel,
      ...mlModel,
      ready: typeof mlModel.ready === "boolean" ? mlModel.ready : fallback.mlModel.ready,
      trainingSource:
        typeof mlModel.trainingSource === "string"
          ? mlModel.trainingSource
          : fallback.mlModel.trainingSource,
      trainingSamples:
        typeof mlModel.trainingSamples === "number"
          ? mlModel.trainingSamples
          : fallback.mlModel.trainingSamples,
      trainingSymbols: asArray(mlModel.trainingSymbols, fallback.mlModel.trainingSymbols),
      lastTrainedAt:
        typeof mlModel.lastTrainedAt === "string"
          ? mlModel.lastTrainedAt
          : fallback.mlModel.lastTrainedAt,
      validationSamples:
        typeof mlModel.validationSamples === "number"
          ? mlModel.validationSamples
          : fallback.mlModel.validationSamples,
      decisionSamples:
        typeof mlModel.decisionSamples === "number"
          ? mlModel.decisionSamples
          : fallback.mlModel.decisionSamples,
      decisionCoverage:
        typeof mlModel.decisionCoverage === "number"
          ? mlModel.decisionCoverage
          : fallback.mlModel.decisionCoverage,
      decisionPrecision:
        typeof mlModel.decisionPrecision === "number"
          ? mlModel.decisionPrecision
          : fallback.mlModel.decisionPrecision,
      longPrecision:
        typeof mlModel.longPrecision === "number"
          ? mlModel.longPrecision
          : fallback.mlModel.longPrecision,
      shortPrecision:
        typeof mlModel.shortPrecision === "number"
          ? mlModel.shortPrecision
          : fallback.mlModel.shortPrecision
    },
    paperPerformance: {
      ...fallback.paperPerformance,
      ...paperPerformance,
      currentMode:
        paperPerformance.currentMode === "normal" || paperPerformance.currentMode === "contrarian"
          ? paperPerformance.currentMode
          : fallback.paperPerformance.currentMode,
      comparisonMode:
        paperPerformance.comparisonMode === "normal" || paperPerformance.comparisonMode === "contrarian"
          ? paperPerformance.comparisonMode
          : fallback.paperPerformance.comparisonMode,
      currentModeSummary: {
        ...fallback.paperPerformance.currentModeSummary,
        ...currentModeSummary
      },
      comparisonModeSummary: {
        ...fallback.paperPerformance.comparisonModeSummary,
        ...comparisonModeSummary
      },
      recentClosedTrades: asArray(
        paperPerformance.recentClosedTrades,
        fallback.paperPerformance.recentClosedTrades
      ),
      pairedClosedSignals:
        typeof paperPerformance.pairedClosedSignals === "number"
          ? paperPerformance.pairedClosedSignals
          : fallback.paperPerformance.pairedClosedSignals,
      comparisonMethod:
        typeof paperPerformance.comparisonMethod === "string"
          ? paperPerformance.comparisonMethod
          : fallback.paperPerformance.comparisonMethod
    },
    livePerformance:
      Object.keys(livePerformance).length === 0
        ? null
        : {
            ...livePerformance,
            summary: {
              executionMode:
                livePerformanceSummary.executionMode === "normal" ||
                livePerformanceSummary.executionMode === "contrarian"
                  ? livePerformanceSummary.executionMode
                  : "normal",
              startingEquityUsd:
                typeof livePerformanceSummary.startingEquityUsd === "number"
                  ? livePerformanceSummary.startingEquityUsd
                  : 0,
              currentEquityUsd:
                typeof livePerformanceSummary.currentEquityUsd === "number"
                  ? livePerformanceSummary.currentEquityUsd
                  : 0,
              netPnlUsd:
                typeof livePerformanceSummary.netPnlUsd === "number"
                  ? livePerformanceSummary.netPnlUsd
                  : 0,
              closedTrades:
                typeof livePerformanceSummary.closedTrades === "number"
                  ? livePerformanceSummary.closedTrades
                  : 0,
              wins:
                typeof livePerformanceSummary.wins === "number"
                  ? livePerformanceSummary.wins
                  : 0,
              losses:
                typeof livePerformanceSummary.losses === "number"
                  ? livePerformanceSummary.losses
                  : 0,
              breakevenTrades:
                typeof livePerformanceSummary.breakevenTrades === "number"
                  ? livePerformanceSummary.breakevenTrades
                  : 0,
              winRate:
                typeof livePerformanceSummary.winRate === "number"
                  ? livePerformanceSummary.winRate
                  : null,
              takeProfitHits:
                typeof livePerformanceSummary.takeProfitHits === "number"
                  ? livePerformanceSummary.takeProfitHits
                  : 0,
              stopLossHits:
                typeof livePerformanceSummary.stopLossHits === "number"
                  ? livePerformanceSummary.stopLossHits
                  : 0,
              breakevenExits:
                typeof livePerformanceSummary.breakevenExits === "number"
                  ? livePerformanceSummary.breakevenExits
                  : 0,
              averageRMultiple:
                typeof livePerformanceSummary.averageRMultiple === "number"
                  ? livePerformanceSummary.averageRMultiple
                  : null,
              profitFactor:
                typeof livePerformanceSummary.profitFactor === "number"
                  ? livePerformanceSummary.profitFactor
                  : null,
              averageWinUsd:
                typeof livePerformanceSummary.averageWinUsd === "number"
                  ? livePerformanceSummary.averageWinUsd
                  : null,
              averageLossUsd:
                typeof livePerformanceSummary.averageLossUsd === "number"
                  ? livePerformanceSummary.averageLossUsd
                  : null,
              peakEquityUsd:
                typeof livePerformanceSummary.peakEquityUsd === "number"
                  ? livePerformanceSummary.peakEquityUsd
                  : 0,
              maxDrawdownUsd:
                typeof livePerformanceSummary.maxDrawdownUsd === "number"
                  ? livePerformanceSummary.maxDrawdownUsd
                  : 0,
              maxDrawdownPct:
                typeof livePerformanceSummary.maxDrawdownPct === "number"
                  ? livePerformanceSummary.maxDrawdownPct
                  : 0,
              openPositions:
                typeof livePerformanceSummary.openPositions === "number"
                  ? livePerformanceSummary.openPositions
                  : 0,
              lastClosedAt:
                typeof livePerformanceSummary.lastClosedAt === "string"
                  ? livePerformanceSummary.lastClosedAt
                  : null
            },
            recentClosedTrades: asArray(livePerformance.recentClosedTrades, []),
            trackingBasis:
              typeof livePerformance.trackingBasis === "string"
                ? livePerformance.trackingBasis
                : ""
          }
  };
}

function normalizeDiagnostics(raw: unknown, fallback: DiagnosticsResponse): DiagnosticsResponse {
  const payload = asRecord(raw);
  const config = asRecord(payload.config);

  return {
    ...fallback,
    ...payload,
    config: {
      ...fallback.config,
      ...config,
      mt5LoginConfigured:
        typeof config.mt5LoginConfigured === "boolean"
          ? config.mt5LoginConfigured
          : fallback.config.mt5LoginConfigured,
      mt5ServerConfigured:
        typeof config.mt5ServerConfigured === "boolean"
          ? config.mt5ServerConfigured
          : fallback.config.mt5ServerConfigured,
      mt5Connected:
        typeof config.mt5Connected === "boolean" ? config.mt5Connected : fallback.config.mt5Connected,
      mt5Server: typeof config.mt5Server === "string" ? config.mt5Server : fallback.config.mt5Server,
      symbols: asArray(config.symbols, fallback.config.symbols)
    },
    services: asArray(payload.services, fallback.services),
    probes: asArray(payload.probes, fallback.probes)
  };
}

function asRecord(value: unknown): Record<string, any> {
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    return value as Record<string, any>;
  }
  return {};
}

function asArray<T>(value: unknown, fallback: T[]): T[] {
  return Array.isArray(value) ? (value as T[]) : fallback;
}

async function postOperatorAction(path: string): Promise<OperatorActionResponse> {
  const response = await fetch(`${DEFAULT_API_URL}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    }
  });

  if (!response.ok) {
    throw new Error(`Operator action returned ${response.status}`);
  }

  return (await response.json()) as OperatorActionResponse;
}

function buildFallbackCandles(anchor: number, drift: number) {
  const candles: Array<{
    time: string;
    open: number;
    high: number;
    low: number;
    close: number;
  }> = [];

  for (let index = 0; index < 36; index += 1) {
    const phase = index / 5;
    const open = anchor * (1 + drift * Math.sin(phase));
    const close = open * (1 + drift * 0.6 * Math.cos(phase / 1.7));
    candles.push({
      time: new Date(Date.now() - (35 - index) * 60_000).toISOString(),
      open: Number(open.toFixed(4)),
      high: Number((Math.max(open, close) * 1.0018).toFixed(4)),
      low: Number((Math.min(open, close) * 0.9982).toFixed(4)),
      close: Number(close.toFixed(4))
    });
  }

  return candles;
}

function buildFallbackData(): DashboardData {
  const now = new Date().toISOString();
  const probes: DiagnosticProbe[] = [
    {
      id: "config",
      label: "Configuration",
      status: "healthy",
      message: "Fallback mode assumes a paper-trading profile.",
      details: {
        mode: "paper",
        liveTradingEnabled: false,
        useSimulatedFeed: true
      }
    },
    {
      id: "dependencies",
      label: "Runtime Dependencies",
      status: "healthy",
      message: "Fallback preview assumes local dependencies are installed.",
      details: {
        fastapi: true,
        httpx: true,
        MetaTrader5: true
      }
    },
    {
      id: "live_probe",
      label: "Live MT5 Probe",
      status: "degraded",
      message: "Backend is offline, so MT5 terminal connectivity could not be checked.",
      details: {}
    }
  ];

  return {
    usingFallback: true,
    snapshot: {
      generatedAt: now,
      bot: {
        mode: "paper",
        status: "degraded",
        liveTradingEnabled: false,
        mt5Server: null,
        mt5LoginConfigured: false
      },
      operator: {
        paused: false,
        canSyncAccount: false,
        canPreviewOrders: true,
        canSubmitOrders: false,
        lastAction: null,
        lastActionAt: null
      },
      services: [
        {
          id: "engine",
          label: "Strategy Engine",
          status: "healthy",
          message: "Breakout and liquidity-sweep detection is active."
        },
        {
          id: "risk",
          label: "Risk Layer",
          status: "healthy",
          message: "Position sizing and daily loss controls are active."
        },
        {
          id: "execution",
          label: "Execution",
          status: "degraded",
          message: "Backend is offline, so the UI is showing fallback data."
        }
      ],
      account: {
        source: "paper",
        equityUsd: 10000,
        availableMarginUsd: 8300,
        balanceUsd: null,
        totalMarginUsedUsd: null,
        marginLevelPct: null,
        currency: null,
        leverage: null,
        pnlUsd: 0,
        pnlLabel: "Paper strategy PnL across open and closed trades.",
        openPositions: 0,
        openOrders: 0,
        maxDailyLossPct: 3,
        lastSyncedAt: null
      },
      paperAccount: {
        startingEquityUsd: 10000,
        equityUsd: 10000,
        availableMarginUsd: 8300,
        realizedPnlUsd: 0,
        unrealizedPnlUsd: 0,
        openPositions: 0
      },
      watchlist: [
        {
          symbol: "EURUSD",
          lastPrice: 1.085,
          movePctFromOpen: 0.12,
          spreadBps: 1.4
        },
        {
          symbol: "XAUUSD",
          lastPrice: 2350,
          movePctFromOpen: 0.45,
          spreadBps: 1.7
        },
        {
          symbol: "BTCUSD",
          lastPrice: 62000,
          movePctFromOpen: -0.11,
          spreadBps: 2.1
        }
      ],
      marketCharts: [
        {
          symbol: "EURUSD",
          candles: buildFallbackCandles(1.085, 0.0006)
        },
        {
          symbol: "XAUUSD",
          candles: buildFallbackCandles(2350, 0.004)
        },
        {
          symbol: "BTCUSD",
          candles: buildFallbackCandles(62000, 0.006)
        }
      ],
      signals: [
        {
          id: "fallback-signal",
          symbol: "EURUSD",
          setup: "breakout",
          bias: "long",
          confidence: 0.76,
          entryPrice: 1.085,
          stopLoss: 1.0836,
          takeProfit: 1.088,
          size: 0.5,
          notionalUsd: 1780,
          status: "approved",
          reason: "Fallback preview signal while the trader service is unavailable.",
          createdAt: now
        }
      ],
      positions: [],
      remotePositions: [],
      openOrders: [],
      events: [
        {
          id: "fallback-event",
          level: "info",
          message: "Connect the trader service to replace fallback data with live snapshots.",
          createdAt: now
        }
      ],
      tradeActivity: [
        {
          id: "fallback-trade-1",
          kind: "paper_entry",
          symbol: "EURUSD",
          title: "EURUSD long opened",
          message: "Fallback paper trade entry shown while the backend is offline.",
          level: "success",
          side: "long",
          price: 1.085,
          size: 0.5,
          notionalUsd: 1780,
          pnlUsd: null,
          signalId: "fallback-signal",
          orderId: null,
          createdAt: now
        },
        {
          id: "fallback-trade-2",
          kind: "paper_exit",
          symbol: "XAUUSD",
          title: "XAUUSD short closed",
          message: "Fallback trade history entry for the live cockpit preview.",
          level: "success",
          side: "short",
          price: 2348,
          size: 0.2,
          notionalUsd: 2553.6,
          pnlUsd: 42.8,
          signalId: null,
          orderId: null,
          createdAt: new Date(Date.now() - 1000 * 60 * 8).toISOString()
        }
      ],
      mlModel: {
        ready: true,
        summary: "Fallback ML summary: holdout precision passed the dashboard safety gate.",
        trainingSource: "fallback artifact",
        trainingSamples: 864,
        trainingSymbols: ["EURUSD", "XAUUSD", "BTCUSD"],
        lastTrainedAt: new Date(Date.now() - 1000 * 60 * 42).toISOString(),
        validationSamples: 144,
        decisionSamples: 21,
        decisionCoverage: 0.1458,
        decisionPrecision: 0.619,
        longPrecision: 0.636,
        shortPrecision: 0.6
      },
      paperPerformance: {
        currentMode: "contrarian",
        comparisonMode: "normal",
        currentModeSummary: {
          book: "primary",
          executionMode: "contrarian",
          closedTrades: 18,
          wins: 11,
          losses: 6,
          breakevenTrades: 1,
          winRate: 0.6111,
          takeProfitHits: 10,
          stopLossHits: 6,
          breakevenExits: 1,
          tpToSlRatio: 1.6667,
          averageRMultiple: 0.42,
          profitFactor: 1.71,
          netPnlUsd: 624.5,
          grossProfitUsd: 1498.1,
          grossLossUsd: 873.6,
          averageWinUsd: 136.19,
          averageLossUsd: 145.6,
          peakEquityUsd: 10682.4,
          endingEquityUsd: 10624.5,
          maxDrawdownUsd: 214.8,
          maxDrawdownPct: 0.0202,
          openPositions: 1,
          lastClosedAt: new Date(Date.now() - 1000 * 60 * 14).toISOString()
        },
        comparisonModeSummary: {
          book: "comparison",
          executionMode: "normal",
          closedTrades: 18,
          wins: 7,
          losses: 10,
          breakevenTrades: 1,
          winRate: 0.3889,
          takeProfitHits: 6,
          stopLossHits: 10,
          breakevenExits: 1,
          tpToSlRatio: 0.6,
          averageRMultiple: -0.19,
          profitFactor: 0.81,
          netPnlUsd: -241.3,
          grossProfitUsd: 998.6,
          grossLossUsd: 1239.9,
          averageWinUsd: 142.66,
          averageLossUsd: 123.99,
          peakEquityUsd: 10188.2,
          endingEquityUsd: 9758.7,
          maxDrawdownUsd: 471.4,
          maxDrawdownPct: 0.0463,
          openPositions: 0,
          lastClosedAt: new Date(Date.now() - 1000 * 60 * 12).toISOString()
        },
        recentClosedTrades: [
          {
            id: "fallback-closed-1",
            book: "primary",
            executionMode: "contrarian",
            signalId: "fallback-signal",
            symbol: "EURUSD",
            setup: "breakout",
            side: "long",
            confidence: 0.79,
            entryPrice: 1.0894,
            exitPrice: 1.0952,
            stopLoss: 1.0854,
            takeProfit: 1.095,
            size: 0.5,
            notionalUsd: 1867.74,
            riskUsd: 8.4,
            pnlUsd: 121.8,
            rMultiple: 1.45,
            outcome: "take_profit",
            exitReason: "Take profit hit.",
            openedAt: new Date(Date.now() - 1000 * 60 * 48).toISOString(),
            closedAt: new Date(Date.now() - 1000 * 60 * 14).toISOString()
          },
          {
            id: "fallback-closed-2",
            book: "comparison",
            executionMode: "normal",
            signalId: "fallback-signal",
            symbol: "EURUSD",
            setup: "breakout",
            side: "short",
            confidence: 0.79,
            entryPrice: 1.0894,
            exitPrice: 1.0952,
            stopLoss: 1.095,
            takeProfit: 1.0854,
            size: 0.3,
            notionalUsd: 1156.22,
            riskUsd: 7.28,
            pnlUsd: -75.4,
            rMultiple: -1.04,
            outcome: "stop_loss",
            exitReason: "Stop loss hit.",
            openedAt: new Date(Date.now() - 1000 * 60 * 48).toISOString(),
            closedAt: new Date(Date.now() - 1000 * 60 * 12).toISOString()
          }
        ],
        pairedClosedSignals: 18,
        comparisonMethod:
          "The comparison book runs the opposite execution policy on the same live signal stream without affecting the primary paper account."
      },
      livePerformance: null
    },
    diagnostics: {
      generatedAt: now,
      config: {
        mode: "paper",
        useSimulatedFeed: true,
        liveTradingEnabled: false,
        mt5LoginConfigured: false,
        mt5ServerConfigured: false,
        mt5Connected: false,
        mt5Server: null,
        symbols: ["EURUSD", "XAUUSD", "BTCUSD"]
      },
      services: [
        {
          id: "engine",
          label: "Strategy Engine",
          status: "healthy",
          message: "Fallback mode is active."
        },
        {
          id: "market_data",
          label: "Market Data",
          status: "degraded",
          message: "Backend is offline, so live connectivity was not verified."
        }
      ],
      probes
    }
  };
}
