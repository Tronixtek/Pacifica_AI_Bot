"use client";

import type { MarketChart, SignalBias, TradeActivity } from "@pacifica-hackathon/shared";
import { useEffect, useId, useMemo, useRef, useState } from "react";

const usd = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2
});

const CHARTING_LIBRARY_SCRIPT_PATH = "/charting_library/charting_library.js";
const CHARTING_LIBRARY_ASSET_PATH = "/charting_library/";
const SUPPORTED_RESOLUTIONS = ["1", "5", "15", "60", "240", "1D"] as const;
const BUY_MARKER_ICON = 0xf062;
const SELL_MARKER_ICON = 0xf063;

let chartingLibraryScriptPromise: Promise<boolean> | null = null;

type TradingViewWidgetLike = {
  activeChart: () => TradingViewChartApiLike;
  onChartReady: (callback: () => void) => void;
  remove?: () => void;
};

type TradingViewChartApiLike = {
  createShape: (
    point: { time: number; price: number },
    options: Record<string, unknown>
  ) => Promise<string | number>;
  removeEntity?: (entityId: string | number) => void;
};

type TradingViewBar = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
};

type TradingViewSubscriber = {
  callback: (bar: TradingViewBar) => void;
  lastKey: string | null;
  resolution: string;
};

type TradingViewSymbolInfo = {
  data_status: "streaming";
  description: string;
  exchange: string;
  has_daily: boolean;
  has_intraday: boolean;
  has_weekly_and_monthly: boolean;
  listed_exchange: string;
  minmov: number;
  name: string;
  pricescale: number;
  session: string;
  supported_resolutions: readonly string[];
  ticker: string;
  timezone: string;
  type: string;
  volume_precision: number;
};

declare global {
  interface Window {
    TradingView?: {
      widget: new (options: Record<string, unknown>) => TradingViewWidgetLike;
    };
  }
}

export function LiveMarketWorkspace({
  marketCharts,
  tradeActivity,
  selectedSymbol,
  onSelectSymbol
}: {
  marketCharts: MarketChart[];
  tradeActivity: TradeActivity[];
  selectedSymbol: string;
  onSelectSymbol: (symbol: string) => void;
}) {
  const activeChart =
    marketCharts.find((chart) => chart.symbol === selectedSymbol) ?? marketCharts[0] ?? null;
  const activeSymbol = activeChart?.symbol ?? selectedSymbol;
  const latestCandle = activeChart?.candles.at(-1) ?? null;
  const symbolTrades = tradeActivity.filter((item) => item.symbol === activeSymbol);
  const visibleTrades = tradeActivity.slice(0, 8);

  return (
    <>
      <article className="live-panel chart-panel">
        <div className="panel-topline chart-topline">
          <div>
            <strong>Market chart</strong>
            <p>Intraday price action with execution markers for the selected symbol.</p>
          </div>
          <div className="symbol-chip-row">
            {marketCharts.map((chart) => (
              <button
                key={chart.symbol}
                className={`symbol-chip ${chart.symbol === activeSymbol ? "active" : ""}`}
                type="button"
                onClick={() => onSelectSymbol(chart.symbol)}
              >
                {chart.symbol}
              </button>
            ))}
          </div>
        </div>

        {activeChart ? (
          <>
            <TradingChart chart={activeChart} tradeActivity={symbolTrades} />
            <div className="chart-metric-row">
              <ChartMetric
                label="Last close"
                value={latestCandle ? usd.format(latestCandle.close) : "Not available"}
              />
              <ChartMetric label="Session range" value={formatRange(activeChart)} />
              <ChartMetric label="Trade markers" value={String(countRenderableTradeMarkers(symbolTrades))} />
            </div>
          </>
        ) : (
          <p className="empty-state">No chart data available yet.</p>
        )}
      </article>

      <article className="live-panel trade-chat-panel">
        <div className="panel-topline">
          <div>
            <strong>Trade chat</strong>
            <p>Executed trades and execution failures show up here in plain language.</p>
          </div>
          <span className="chat-count">{visibleTrades.length} updates</span>
        </div>

        <div className="trade-chat-stack">
          {visibleTrades.length === 0 ? (
            <p className="empty-state">No trade activity yet. New executions will appear here.</p>
          ) : (
            visibleTrades.map((activity) => (
              <TradeBubble key={activity.id} activity={activity} highlighted={activity.symbol === activeSymbol} />
            ))
          )}
        </div>
      </article>
    </>
  );
}

function TradingChart({
  chart,
  tradeActivity
}: {
  chart: MarketChart;
  tradeActivity: TradeActivity[];
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef(chart);
  const widgetRef = useRef<TradingViewWidgetLike | null>(null);
  const markerIdsRef = useRef<Array<string | number>>([]);
  const subscribersRef = useRef<Map<string, TradingViewSubscriber>>(new Map());
  const widgetId = useId().replace(/:/g, "_");
  const tradingViewSymbol = useMemo(() => resolveTradingViewSymbol(chart.symbol), [chart.symbol]);
  const [chartMode, setChartMode] = useState<"loading" | "charting_library" | "embed">("loading");

  useEffect(() => {
    chartRef.current = chart;
  }, [chart]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) {
      return;
    }

    let closed = false;
    subscribersRef.current.clear();
    markerIdsRef.current = [];
    widgetRef.current?.remove?.();
    widgetRef.current = null;
    container.innerHTML = "";
    setChartMode("loading");

    const mount = async () => {
      const chartingLibraryReady = await loadChartingLibraryScript();
      if (closed || !containerRef.current) {
        return;
      }

      if (chartingLibraryReady && window.TradingView?.widget) {
        const widget = new window.TradingView.widget({
          allow_symbol_change: false,
          autosize: true,
          container,
          datafeed: createLocalDatafeed(chart.symbol, chartRef, subscribersRef),
          disabled_features: [
            "header_symbol_search",
            "symbol_search_hot_key",
            "show_symbol_logo_in_legend"
          ],
          enabled_features: ["hide_left_toolbar_by_default"],
          fullscreen: false,
          interval: "1",
          library_path: CHARTING_LIBRARY_ASSET_PATH,
          loading_screen: {
            backgroundColor: "#f7faff",
            foregroundColor: "#12bfd4"
          },
          locale: "en",
          overrides: {
            "mainSeriesProperties.candleStyle.downColor": "#f38b78",
            "mainSeriesProperties.candleStyle.upColor": "#17bf82",
            "mainSeriesProperties.candleStyle.borderDownColor": "#f38b78",
            "mainSeriesProperties.candleStyle.borderUpColor": "#17bf82",
            "mainSeriesProperties.candleStyle.wickDownColor": "#f38b78",
            "mainSeriesProperties.candleStyle.wickUpColor": "#17bf82",
            "paneProperties.background": "#f7faff",
            "paneProperties.gridProperties.color": "rgba(23, 27, 93, 0.08)",
            "scalesProperties.lineColor": "rgba(23, 27, 93, 0.08)"
          },
          save_image: false,
          symbol: buildChartingLibrarySymbol(chart.symbol),
          theme: "light",
          timezone: "Etc/UTC",
          toolbar_bg: "#f7faff"
        });

        widgetRef.current = widget;
        widget.onChartReady(() => {
          if (closed) {
            return;
          }
          setChartMode("charting_library");
          void syncTradeMarkers(widgetRef.current, markerIdsRef, chartRef.current, tradeActivity);
        });
        return;
      }

      mountEmbedWidget(containerRef.current, widgetId, tradingViewSymbol);
      setChartMode("embed");
    };

    void mount();

    return () => {
      closed = true;
      subscribersRef.current.clear();
      markerIdsRef.current = [];
      widgetRef.current?.remove?.();
      widgetRef.current = null;
      container.innerHTML = "";
    };
  }, [chart.symbol, tradingViewSymbol, widgetId]);

  useEffect(() => {
    if (chartMode !== "charting_library") {
      return;
    }

    for (const subscriber of subscribersRef.current.values()) {
      const latestBar = getLatestBar(chart, subscriber.resolution);
      if (!latestBar) {
        continue;
      }
      const nextKey = buildBarKey(latestBar);
      if (subscriber.lastKey === nextKey) {
        continue;
      }
      subscriber.lastKey = nextKey;
      subscriber.callback(latestBar);
    }
  }, [chart, chartMode]);

  useEffect(() => {
    if (chartMode !== "charting_library") {
      return;
    }

    void syncTradeMarkers(widgetRef.current, markerIdsRef, chartRef.current, tradeActivity);
  }, [chartMode, tradeActivity]);

  if (chart.candles.length === 0) {
    return <p className="empty-state">The chart will populate once price history is available.</p>;
  }

  return (
    <div className="chart-stage">
      <div className="tv-widget-shell">
        <div
          ref={containerRef}
          className="tradingview-widget-container"
          role="img"
          aria-label={`${chart.symbol} TradingView chart`}
        />
      </div>
    </div>
  );
}

function TradeBubble({
  activity,
  highlighted
}: {
  activity: TradeActivity;
  highlighted: boolean;
}) {
  return (
    <div className={`trade-bubble ${activity.level} ${highlighted ? "highlighted" : ""}`}>
      <div className="trade-bubble-top">
        <div>
          <strong>{activity.title}</strong>
          <p>{activity.message}</p>
        </div>
        <span className="trade-bubble-time">{formatTimestamp(activity.createdAt)}</span>
      </div>
      <div className="trade-bubble-meta">
        <span>{activity.symbol}</span>
        <span>{activity.side ? activity.side.toUpperCase() : activity.kind.replaceAll("_", " ")}</span>
        <span>{activity.price == null ? "Price n/a" : usd.format(activity.price)}</span>
        <span>{activity.pnlUsd == null ? formatSize(activity.size) : `PnL ${usd.format(activity.pnlUsd)}`}</span>
      </div>
    </div>
  );
}

function ChartMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="chart-metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

async function loadChartingLibraryScript() {
  if (typeof window === "undefined") {
    return false;
  }
  if (window.TradingView?.widget) {
    return true;
  }
  if (chartingLibraryScriptPromise) {
    return chartingLibraryScriptPromise;
  }

  chartingLibraryScriptPromise = new Promise((resolve) => {
    const script = document.createElement("script");
    script.src = CHARTING_LIBRARY_SCRIPT_PATH;
    script.async = true;
    script.onload = () => resolve(Boolean(window.TradingView?.widget));
    script.onerror = () => resolve(false);
    document.head.appendChild(script);
  });

  return chartingLibraryScriptPromise;
}

function mountEmbedWidget(container: HTMLDivElement, widgetId: string, symbol: string) {
  container.innerHTML = "";

  const widgetHost = document.createElement("div");
  widgetHost.id = widgetId;
  widgetHost.className = "tradingview-widget-container__widget";
  container.appendChild(widgetHost);

  const script = document.createElement("script");
  script.src = "https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js";
  script.type = "text/javascript";
  script.async = true;
  script.innerHTML = JSON.stringify({
    autosize: true,
    symbol,
    interval: "1",
    timezone: "Etc/UTC",
    theme: "light",
    style: "1",
    locale: "en",
    backgroundColor: "#f7faff",
    gridColor: "rgba(23, 27, 93, 0.08)",
    allow_symbol_change: false,
    hide_top_toolbar: false,
    hide_legend: false,
    save_image: false,
    details: false,
    hotlist: false,
    calendar: false,
    support_host: "https://www.tradingview.com",
    container_id: widgetId
  });
  container.appendChild(script);
}

function createLocalDatafeed(
  symbol: string,
  chartRef: React.MutableRefObject<MarketChart>,
  subscribersRef: React.MutableRefObject<Map<string, TradingViewSubscriber>>
) {
  const localSymbol = buildChartingLibrarySymbol(symbol);

  return {
    getBars(
      _symbolInfo: TradingViewSymbolInfo,
      resolution: string,
      periodParams: { from: number; to: number },
      onHistoryCallback: (bars: TradingViewBar[], meta: { noData: boolean }) => void
    ) {
      const bars = getBarsForResolution(chartRef.current, resolution).filter((bar) => {
        const barTimeSeconds = Math.floor(bar.time / 1000);
        return barTimeSeconds >= periodParams.from && barTimeSeconds <= periodParams.to;
      });

      if (bars.length === 0) {
        onHistoryCallback([], { noData: true });
        return;
      }

      onHistoryCallback(bars, { noData: false });
    },
    onReady(callback: (configuration: Record<string, unknown>) => void) {
      window.setTimeout(() => {
        callback({
          supports_marks: false,
          supports_search: false,
          supports_time: true,
          supports_timescale_marks: false,
          supported_resolutions: [...SUPPORTED_RESOLUTIONS]
        });
      }, 0);
    },
    resolveSymbol(
      requestedSymbol: string,
      onResolve: (symbolInfo: TradingViewSymbolInfo) => void
    ) {
      window.setTimeout(() => {
        onResolve({
          data_status: "streaming",
          description: `${symbol} Pacifica feed`,
          exchange: "Pacifica",
          has_daily: true,
          has_intraday: true,
          has_weekly_and_monthly: false,
          listed_exchange: "Pacifica",
          minmov: 1,
          name: requestedSymbol,
          pricescale: calculatePriceScale(chartRef.current),
          session: "24x7",
          supported_resolutions: SUPPORTED_RESOLUTIONS,
          ticker: localSymbol,
          timezone: "Etc/UTC",
          type: "crypto",
          volume_precision: 4
        });
      }, 0);
    },
    searchSymbols(
      _userInput: string,
      _exchange: string,
      _symbolType: string,
      onResult: (results: Array<Record<string, string>>) => void
    ) {
      window.setTimeout(() => {
        onResult([
          {
            description: `${symbol} Pacifica feed`,
            exchange: "Pacifica",
            full_name: localSymbol,
            symbol: localSymbol,
            ticker: localSymbol,
            type: "crypto"
          }
        ]);
      }, 0);
    },
    subscribeBars(
      _symbolInfo: TradingViewSymbolInfo,
      resolution: string,
      onRealtimeCallback: (bar: TradingViewBar) => void,
      subscriberId: string
    ) {
      subscribersRef.current.set(subscriberId, {
        callback: onRealtimeCallback,
        lastKey: null,
        resolution
      });
    },
    unsubscribeBars(subscriberId: string) {
      subscribersRef.current.delete(subscriberId);
    }
  };
}

async function syncTradeMarkers(
  widget: TradingViewWidgetLike | null,
  markerIdsRef: React.MutableRefObject<Array<string | number>>,
  chart: MarketChart,
  tradeActivity: TradeActivity[]
) {
  if (!widget) {
    return;
  }

  const activeChart = widget.activeChart();
  for (const markerId of markerIdsRef.current) {
    try {
      activeChart.removeEntity?.(markerId);
    } catch {
      continue;
    }
  }
  markerIdsRef.current = [];

  const renderableTrades = [...tradeActivity]
    .filter(isRenderableTradeMarker)
    .sort((left, right) => Date.parse(left.createdAt) - Date.parse(right.createdAt));

  for (const activity of renderableTrades) {
    const markerPrice = activity.price ?? findNearestCandlePrice(chart, activity.createdAt);
    if (markerPrice == null) {
      continue;
    }

    const executionSide = resolveExecutionSide(activity);
    const label = buildTradeMarkerLabel(activity);
    const markerId = await activeChart.createShape(
      {
        time: toUnixSeconds(activity.createdAt),
        price: markerPrice
      },
      {
        shape: "icon",
        icon: executionSide === "long" ? BUY_MARKER_ICON : SELL_MARKER_ICON,
        lock: true,
        disableSave: true,
        disableSelection: true,
        disableUndo: true,
        overrides: {
          color: executionSide === "long" ? "#17bf82" : "#f38b78"
        },
        text: label
      }
    );
    markerIdsRef.current.push(markerId);
  }
}

function getBarsForResolution(chart: MarketChart, resolution: string) {
  const bucketSeconds = resolutionToBucketSeconds(resolution);
  if (bucketSeconds === 60) {
    return chart.candles.map((candle) => ({
      close: candle.close,
      high: candle.high,
      low: candle.low,
      open: candle.open,
      time: toUnixMilliseconds(candle.time)
    }));
  }

  const bars = new Map<number, TradingViewBar>();
  for (const candle of chart.candles) {
    const candleTimeSeconds = toUnixSeconds(candle.time);
    const bucketStartSeconds = Math.floor(candleTimeSeconds / bucketSeconds) * bucketSeconds;
    const existingBar = bars.get(bucketStartSeconds);
    if (!existingBar) {
      bars.set(bucketStartSeconds, {
        close: candle.close,
        high: candle.high,
        low: candle.low,
        open: candle.open,
        time: bucketStartSeconds * 1000
      });
      continue;
    }

    existingBar.high = Math.max(existingBar.high, candle.high);
    existingBar.low = Math.min(existingBar.low, candle.low);
    existingBar.close = candle.close;
  }

  return [...bars.values()].sort((left, right) => left.time - right.time);
}

function getLatestBar(chart: MarketChart, resolution: string) {
  const bars = getBarsForResolution(chart, resolution);
  return bars.at(-1) ?? null;
}

function buildBarKey(bar: TradingViewBar) {
  return `${bar.time}:${bar.open}:${bar.high}:${bar.low}:${bar.close}`;
}

function resolutionToBucketSeconds(resolution: string) {
  if (resolution === "1D") {
    return 86_400;
  }
  const minutes = Number.parseInt(resolution, 10);
  if (Number.isNaN(minutes) || minutes <= 0) {
    return 60;
  }
  return minutes * 60;
}

function calculatePriceScale(chart: MarketChart) {
  const observedDecimals = chart.candles.reduce((currentMax, candle) => {
    return Math.max(
      currentMax,
      decimalPlaces(candle.open),
      decimalPlaces(candle.high),
      decimalPlaces(candle.low),
      decimalPlaces(candle.close)
    );
  }, 0);

  return 10 ** Math.min(Math.max(observedDecimals, 2), 8);
}

function decimalPlaces(value: number) {
  const [, decimals = ""] = value.toString().split(".");
  return decimals.length;
}

function countRenderableTradeMarkers(trades: TradeActivity[]) {
  return trades.filter(isRenderableTradeMarker).length;
}

function isRenderableTradeMarker(activity: TradeActivity) {
  return activity.kind !== "live_execution_failed" && activity.side != null;
}

function resolveExecutionSide(activity: TradeActivity): SignalBias {
  if (activity.kind === "paper_exit" || activity.kind === "live_exit") {
    return activity.side === "long" ? "short" : "long";
  }
  return activity.side ?? "long";
}

function buildTradeMarkerLabel(activity: TradeActivity) {
  if (activity.kind === "paper_exit" || activity.kind === "live_exit") {
    const normalizedMessage = activity.message.toLowerCase();
    if (normalizedMessage.includes("take profit")) {
      return "TP";
    }
    if (normalizedMessage.includes("stop loss")) {
      return "SL";
    }
    return "EXIT";
  }
  if (activity.kind === "live_execution_submitted") {
    return "LIVE";
  }
  return "ENTRY";
}

function findNearestCandlePrice(chart: MarketChart, timestamp: string) {
  const targetTime = toUnixMilliseconds(timestamp);
  let nearestCandle = chart.candles[0] ?? null;
  let smallestGap = Number.POSITIVE_INFINITY;

  for (const candle of chart.candles) {
    const gap = Math.abs(toUnixMilliseconds(candle.time) - targetTime);
    if (gap >= smallestGap) {
      continue;
    }
    nearestCandle = candle;
    smallestGap = gap;
  }

  return nearestCandle?.close ?? null;
}

function formatRange(chart: MarketChart) {
  if (chart.candles.length === 0) {
    return "Not available";
  }
  const highs = chart.candles.map((candle) => candle.high);
  const lows = chart.candles.map((candle) => candle.low);
  return `${usd.format(Math.min(...lows))} - ${usd.format(Math.max(...highs))}`;
}

function formatSize(size: number | null) {
  if (size == null) {
    return "Size n/a";
  }
  return `Size ${size.toFixed(size >= 1 ? 3 : 4)}`;
}

function formatTimestamp(value: string) {
  return value.replace("T", " ").slice(0, 16);
}

function toUnixMilliseconds(value: string) {
  return Date.parse(value);
}

function toUnixSeconds(value: string) {
  return Math.floor(toUnixMilliseconds(value) / 1000);
}

function buildChartingLibrarySymbol(symbol: string) {
  return `PACIFICA:${symbol.trim().toUpperCase()}`;
}

function resolveTradingViewSymbol(symbol: string) {
  const normalized = symbol.trim().toUpperCase();
  const mapping: Record<string, string> = {
    BTC: "BINANCE:BTCUSDT",
    ETH: "BINANCE:ETHUSDT",
    SOL: "BINANCE:SOLUSDT"
  };

  return mapping[normalized] ?? `BINANCE:${normalized}USDT`;
}
