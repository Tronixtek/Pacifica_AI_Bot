"use client";

import type {
  DashboardSnapshot,
  DiagnosticProbe,
  DiagnosticsResponse,
  EnginePosition,
  EventLog,
  OpenOrderSnapshot,
  OperatorActionResponse,
  RemotePositionSnapshot,
  ServiceHealth,
  SignalPreviewResponse,
  StrategySignal
} from "@vtfx-mt5-bot/shared";
import { startTransition, useDeferredValue, useEffect, useEffectEvent, useState } from "react";
import { useRouter } from "next/navigation";

import { pauseStrategy, previewSignal, resumeStrategy, syncAccount } from "../lib/api";

const usd = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2
});

const SECTION_IDS = {
  health: "system-health",
  mt5: "mt5-state",
  signals: "signal-queue",
  events: "recent-events"
} as const;

function isMt5Configured(diagnostics: DiagnosticsResponse) {
  return diagnostics.config.mt5LoginConfigured && diagnostics.config.mt5ServerConfigured;
}

export function DashboardConsole({
  snapshot,
  diagnostics,
  usingFallback
}: {
  snapshot: DashboardSnapshot;
  diagnostics: DiagnosticsResponse;
  usingFallback: boolean;
}) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [issuesOnly, setIssuesOnly] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [operatorState, setOperatorState] = useState(snapshot.operator);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [actionTone, setActionTone] = useState<"positive" | "negative" | "neutral">("neutral");
  const [actionPending, setActionPending] = useState<string | null>(null);
  const [preview, setPreview] = useState<SignalPreviewResponse | null>(null);
  const deferredQuery = useDeferredValue(query.trim().toLowerCase());
  const healthyProbeCount = diagnostics.probes.filter((probe) => probe.status === "healthy").length;
  const previewableSignal = snapshot.signals.find((signal) => signal.status !== "blocked") ?? null;

  useEffect(() => {
    setOperatorState(snapshot.operator);
  }, [snapshot.operator]);

  const refreshSnapshot = useEffectEvent(() => {
    setIsRefreshing(true);
    startTransition(() => {
      router.refresh();
    });
    window.setTimeout(() => setIsRefreshing(false), 700);
  });

  const applyOperatorResult = useEffectEvent(
    (
      result: Pick<OperatorActionResponse, "ok" | "message" | "operator"> |
      Pick<SignalPreviewResponse, "ok" | "message" | "operator">
    ) => {
      setOperatorState(result.operator);
      setActionMessage(result.message);
      setActionTone(result.ok ? "positive" : "negative");
    }
  );

  const runOperatorAction = useEffectEvent(async (actionId: string, request: () => Promise<OperatorActionResponse>) => {
    setActionPending(actionId);
    try {
      const result = await request();
      applyOperatorResult(result);
      refreshSnapshot();
    } catch (error) {
      setActionMessage(error instanceof Error ? error.message : "Operator action failed.");
      setActionTone("negative");
    } finally {
      setActionPending(null);
    }
  });

  const handlePauseResume = useEffectEvent(async () => {
    await runOperatorAction(operatorState.paused ? "resume" : "pause", operatorState.paused ? resumeStrategy : pauseStrategy);
  });

  const handleAccountSync = useEffectEvent(async () => {
    await runOperatorAction("sync-account", syncAccount);
  });

  const handlePreviewSignal = useEffectEvent(async (signal: StrategySignal) => {
    setActionPending(`preview:${signal.id}`);
    try {
      const result = await previewSignal(signal.id);
      setPreview(result);
      applyOperatorResult(result);
    } catch (error) {
      setActionMessage(error instanceof Error ? error.message : "Signal preview failed.");
      setActionTone("negative");
    } finally {
      setActionPending(null);
    }
  });

  useEffect(() => {
    if (!autoRefresh) {
      return;
    }
    const timer = window.setInterval(() => refreshSnapshot(), 15000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, refreshSnapshot]);

  const filteredWatchlist = snapshot.watchlist.filter((market) => matchesQuery(deferredQuery, market.symbol));
  const filteredSignals = snapshot.signals.filter((signal) => matchesQuery(deferredQuery, signal.symbol, signal.setup, signal.bias, signal.reason));
  const filteredRemotePositions = snapshot.remotePositions.filter((position) => matchesQuery(deferredQuery, position.symbol, position.side));
  const filteredOpenOrders = snapshot.openOrders.filter((order) => matchesQuery(deferredQuery, order.symbol, order.side, order.orderType, String(order.orderId)));
  const filteredEnginePositions = snapshot.positions.filter((position) => matchesQuery(deferredQuery, position.symbol, position.side, position.riskState));
  const filteredEvents = snapshot.events.filter((event) => matchesQuery(deferredQuery, event.level, event.message));
  const visibleProbes = diagnostics.probes.filter((probe) => !issuesOnly ? matchesQuery(deferredQuery, probe.label, probe.message) : probe.status !== "healthy" && matchesQuery(deferredQuery, probe.label, probe.message));
  const visibleSignals = filteredSignals.filter((signal) => signal.status !== "blocked");
  const setupSteps = buildSetupSteps(snapshot, diagnostics, usingFallback);
  const attentionItems = buildAttentionItems(snapshot, diagnostics, usingFallback);
  const nextAction = getNextAction({
    snapshot,
    diagnostics,
    usingFallback,
    previewableSignal
  });

  return (
    <main className="page-shell">
      <section className="hero hero-friendly">
        <div className="hero-copy hero-copy-friendly">
          <p className="eyebrow">VTFX MT5 Trading Bot</p>
          <h1>Understand your bot at a glance.</h1>
          <p className="hero-text">
            This project is an AI-assisted MT5 trading bot. It watches live markets for
            breakouts and liquidity sweeps, applies risk rules, and helps you review what it wants
            to trade before any live execution.
          </p>
          <div className="hero-meta">
            <span className={`badge ${snapshot.bot.status}`}>{snapshot.bot.status}</span>
            <span className={`badge ${operatorState.paused ? "degraded" : "healthy"}`}>
              {operatorState.paused ? "bot paused" : "bot running"}
            </span>
            <span className="mono">
              {snapshot.bot.mode.toUpperCase()} mode{snapshot.bot.mt5Server ? ` on ${snapshot.bot.mt5Server}` : ""}
            </span>
            <span className="mono">
              {usingFallback ? "Backend is offline, showing fallback data" : "Backend is connected"}
            </span>
          </div>
        </div>

        <div className="hero-stack">
          <article className="hero-card accent-card mission-card">
            <span className="label">Next Best Step</span>
            <strong>{nextAction.title}</strong>
            <p>{nextAction.description}</p>
            <div className="control-actions">
              <button
                className={`action-button primary ${operatorState.paused ? "" : "active"}`}
                type="button"
                onClick={() => void handlePauseResume()}
                disabled={actionPending === "pause" || actionPending === "resume"}
              >
                {actionPending === "pause" || actionPending === "resume"
                  ? "Updating..."
                  : operatorState.paused
                    ? "Resume Bot"
                    : "Pause Bot"}
              </button>
              <button
                className={`action-button ${operatorState.canSyncAccount ? "" : "disabled"}`}
                type="button"
                onClick={() => void handleAccountSync()}
                disabled={!operatorState.canSyncAccount || actionPending === "sync-account"}
              >
                {actionPending === "sync-account" ? "Syncing..." : "Sync Account"}
              </button>
              <button
                className="action-button"
                type="button"
                onClick={() => previewableSignal && void handlePreviewSignal(previewableSignal)}
                disabled={!previewableSignal}
              >
                Preview Best Trade
              </button>
            </div>
          </article>

          <article className="hero-card summary-card">
            <span className="label">Simple Status</span>
            <div className="summary-list">
              <QuickFact label="Trading mode" value={snapshot.bot.liveTradingEnabled ? "Live-ready" : "Safe / paper mode"} />
              <QuickFact label="Account view" value={snapshot.account.source === "mt5" ? "MT5 account synced" : "Paper account only"} />
              <QuickFact label="Signals ready" value={`${visibleSignals.length} tradable setup${visibleSignals.length === 1 ? "" : "s"}`} />
              <QuickFact label="MT5 readiness" value={`${healthyProbeCount}/${diagnostics.probes.length} checks healthy`} />
            </div>
          </article>
        </div>
      </section>

      <section className="panel-grid top-grid">
        <MetricCard label="Account Value" value={usd.format(snapshot.account.equityUsd)} detail={`${usd.format(snapshot.account.availableMarginUsd)} available to use`} />
        <MetricCard label="Open Profit / Loss" value={usd.format(snapshot.account.pnlUsd)} detail={snapshot.account.pnlLabel} tone={snapshot.account.pnlUsd >= 0 ? "positive" : "negative"} />
        <MetricCard label="Trade Opportunities" value={String(visibleSignals.length)} detail={previewableSignal ? `${previewableSignal.symbol} is the top current setup` : "No active setup right now"} />
        <MetricCard label="Protection Status" value={operatorState.paused ? "Paused" : "Active"} detail={snapshot.bot.liveTradingEnabled ? "Live execution can be enabled" : "Live trading is still off"} />
      </section>

      <section className="guided-grid">
        <article className="panel">
          <PanelHeading title="Start Here" subtitle="If this is your first time opening the bot, do these steps in order." />
          <div className="stack">
            {setupSteps.map((step) => (
              <ChecklistRow key={step.label} label={step.label} description={step.description} ready={step.ready} optional={step.optional} />
            ))}
          </div>
        </article>

        <article className="panel">
          <PanelHeading title="What Needs Attention" subtitle="These are the only things you should worry about right now." />
          <div className="stack">
            {attentionItems.length === 0 ? (
              <EmptyState message="Nothing critical needs attention. The bot looks healthy from the current checks." />
            ) : (
              attentionItems.map((item) => <AttentionRow key={item.title} title={item.title} message={item.message} />)
            )}
          </div>
        </article>
      </section>

      {actionMessage ? (
        <section className="panel wide info-banner">
          <p className={`operator-note ${actionTone}`}>{actionMessage}</p>
        </section>
      ) : null}

      {preview ? (
        <section className="panel wide">
          <PanelHeading title="Trade Preview" subtitle="This is the order payload the bot would prepare for the selected setup." />
          <div className="preview-card">
            <div className="signal-topline">
              <div>
                <strong>{preview.signal ? `${preview.signal.symbol} ${preview.signal.bias.toUpperCase()}` : "Signal Preview"}</strong>
                <p>{preview.message}</p>
              </div>
              <span className={`badge ${preview.ok ? "healthy" : "degraded"}`}>
                {preview.marketSpecApplied ? "market rules loaded" : "using fallback formatting"}
              </span>
            </div>
            {preview.signal ? (
              <div className="signal-grid">
                <span>Setup {preview.signal.setup.replace("_", " ")}</span>
                <span>Entry {usd.format(preview.signal.entryPrice)}</span>
                <span>Stop {usd.format(preview.signal.stopLoss)}</span>
                <span>Target {usd.format(preview.signal.takeProfit)}</span>
              </div>
            ) : null}
            <pre className="payload-preview">{JSON.stringify(preview.payload, null, 2)}</pre>
          </div>
        </section>
      ) : null}

      <section className="content-grid">
        <article className="panel wide" id={SECTION_IDS.signals}>
          <PanelHeading
            title="Trade Opportunities"
            subtitle="These are the setups the bot currently wants you to look at first."
          />
          <div className="stack">
            {filteredSignals.length === 0 ? (
              <EmptyState message="The bot has not found a clear setup yet. Let it keep watching the market." />
            ) : (
              filteredSignals.map((signal) => (
                <SignalRow
                  key={signal.id}
                  signal={signal}
                  onPreview={signal.status === "blocked" ? undefined : () => void handlePreviewSignal(signal)}
                  previewing={actionPending === `preview:${signal.id}`}
                />
              ))
            )}
          </div>
        </article>

        <article className="panel">
          <PanelHeading
            title="Market Watch"
            subtitle="A simple view of the symbols this bot is monitoring."
          />
          <div className="stack">
            {filteredWatchlist.length === 0 ? (
              <EmptyState message="No market symbols match your current search." />
            ) : (
              filteredWatchlist.map((market) => (
                <div key={market.symbol} className="list-row">
                  <div>
                    <strong>{market.symbol}</strong>
                    <p>{usd.format(market.lastPrice)} current price</p>
                  </div>
                  <div className="row-meta">
                    <span className={market.movePctFromOpen >= 0 ? "badge positive" : "badge negative"}>
                      {market.movePctFromOpen >= 0 ? "+" : ""}
                      {market.movePctFromOpen.toFixed(2)}%
                    </span>
                    <span className="mono">{market.spreadBps.toFixed(2)} bps spread</span>
                  </div>
                </div>
              ))
            )}
          </div>
        </article>

        <article className="panel">
          <PanelHeading
            title="Account Summary"
            subtitle="The most important account numbers without the extra noise."
          />
          <div className="stack">
            <AccountDetailRow label="Account type" value={snapshot.account.source === "mt5" ? "Connected MT5 account" : "Paper trading account"} />
            <AccountDetailRow label="Balance" value={optionalUsd(snapshot.account.balanceUsd)} />
            <AccountDetailRow label="Available funds" value={usd.format(snapshot.account.availableMarginUsd)} />
            <AccountDetailRow label="Margin in use" value={optionalUsd(snapshot.account.totalMarginUsedUsd)} />
            <AccountDetailRow label="Open positions" value={String(snapshot.account.openPositions)} />
            <AccountDetailRow label="Open orders" value={String(snapshot.account.openOrders)} />
            <AccountDetailRow label="Last synced" value={formatNullableTimestamp(snapshot.account.lastSyncedAt)} />
          </div>
        </article>

        <article className="panel wide">
          <PanelHeading
            title="Positions and Orders"
            subtitle="What is currently open on MT5 and inside the bot."
          />
          <div className="subpanel-grid">
            <div className="subpanel">
              <SubpanelHeading title="MT5 Positions" subtitle="Live positions mirrored from the MT5 terminal." />
              <div className="stack">
                {filteredRemotePositions.length === 0 ? (
                  <EmptyState message="No MT5 positions are open right now." />
                ) : (
                  filteredRemotePositions.map((position) => (
                    <RemotePositionRow key={position.ticket} position={position} />
                  ))
                )}
              </div>
            </div>

            <div className="subpanel">
              <SubpanelHeading title="Open Orders" subtitle="Pending orders currently waiting on MT5." />
              <div className="stack">
                {filteredOpenOrders.length === 0 ? (
                  <EmptyState message="No MT5 open orders right now." />
                ) : (
                  filteredOpenOrders.map((order) => <OrderRow key={order.orderId} order={order} />)
                )}
              </div>
            </div>
          </div>
        </article>

        <article className="panel wide" id={SECTION_IDS.events}>
          <PanelHeading
            title="Recent Activity"
            subtitle="A plain-language timeline of what the bot has been doing."
          />
          <div className="stack">
            {filteredEvents.length === 0 ? (
              <EmptyState message="No recent events match your current search." />
            ) : (
              filteredEvents.map((event) => <EventRow key={event.id} event={event} />)
            )}
          </div>
        </article>

        <details className="panel wide advanced-panel" id={SECTION_IDS.health}>
          <summary className="advanced-summary">Advanced Details</summary>
          <p className="advanced-copy">
            Use this section for debugging, demo prep, and lower-level MT5 checks.
          </p>

          <div className="controls-grid advanced-controls">
            <label className="control-field">
              <span className="label">Search Inside Details</span>
              <input
                className="control-input"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search symbols, messages, warnings..."
              />
            </label>
            <div className="control-actions">
              <button className="action-button primary" type="button" onClick={refreshSnapshot}>
                {isRefreshing ? "Refreshing..." : "Refresh Data"}
              </button>
              <button
                className={`action-button ${autoRefresh ? "active" : ""}`}
                type="button"
                onClick={() => setAutoRefresh((current) => !current)}
              >
                Auto Refresh {autoRefresh ? "On" : "Off"}
              </button>
              <button
                className={`action-button ${issuesOnly ? "active" : ""}`}
                type="button"
                onClick={() => setIssuesOnly((current) => !current)}
              >
                {issuesOnly ? "Show All Checks" : "Only Show Issues"}
              </button>
            </div>
          </div>

          <div className="subpanel-grid advanced-grid">
            <div className="subpanel">
              <SubpanelHeading title="System Health" subtitle="Service-level health inside the bot." />
              <div className="stack">
                {snapshot.services.map((service) => (
                  <ServiceRow key={service.id} service={service} />
                ))}
              </div>
            </div>

            <div className="subpanel">
              <SubpanelHeading title="Setup Details" subtitle="Technical configuration checks." />
              <div className="stack">
                <ReadinessRow label="MT5 Login" value={diagnostics.config.mt5LoginConfigured ? "Configured" : "Missing"} ready={diagnostics.config.mt5LoginConfigured} />
                <ReadinessRow label="MT5 Server" value={diagnostics.config.mt5Server ?? "Missing"} ready={diagnostics.config.mt5ServerConfigured} />
                <ReadinessRow label="MT5 Terminal" value={diagnostics.config.mt5Connected ? "Connected" : "Not connected"} ready={diagnostics.config.mt5Connected} />
                <ReadinessRow label="Feed Mode" value={diagnostics.config.useSimulatedFeed ? "Simulated" : "Live MT5"} ready={!diagnostics.config.useSimulatedFeed} optional={diagnostics.config.useSimulatedFeed} />
                <ReadinessRow label="Tracked Symbols" value={diagnostics.config.symbols.join(", ")} ready={diagnostics.config.symbols.length > 0} />
              </div>
            </div>

            <div className="subpanel">
              <SubpanelHeading title="Diagnostics" subtitle="Public API and runtime checks." />
              <div className="stack">
                {visibleProbes.length === 0 ? (
                  <EmptyState message="No diagnostics match your current filter." />
                ) : (
                  visibleProbes.map((probe) => <ProbeRow key={probe.id} probe={probe} />)
                )}
              </div>
            </div>

            <div className="subpanel">
              <SubpanelHeading title="Engine Positions" subtitle="Internal positions tracked by the paper engine." />
              <div className="stack">
                {filteredEnginePositions.length === 0 ? (
                  <EmptyState message="No internal engine positions right now." />
                ) : (
                  filteredEnginePositions.map((position) => <PositionRow key={position.symbol} position={position} />)
                )}
              </div>
            </div>
          </div>
        </details>
      </section>
    </main>
  );
}

function MetricCard({
  label,
  value,
  detail,
  tone = "neutral"
}: {
  label: string;
  value: string;
  detail: string;
  tone?: "neutral" | "positive" | "negative";
}) {
  return (
    <article className={`metric-card ${tone}`}>
      <span className="label">{label}</span>
      <strong>{value}</strong>
      <p>{detail}</p>
    </article>
  );
}

function QuickFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="quick-fact">
      <span className="label">{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function ChecklistRow({
  label,
  description,
  ready,
  optional = false
}: {
  label: string;
  description: string;
  ready: boolean;
  optional?: boolean;
}) {
  return (
    <div className="checklist-row">
      <div className={`check-dot ${ready ? "ready" : optional ? "optional" : "missing"}`} />
      <div>
        <strong>{label}</strong>
        <p>{description}</p>
      </div>
    </div>
  );
}

function AttentionRow({ title, message }: { title: string; message: string }) {
  return (
    <div className="attention-row">
      <strong>{title}</strong>
      <p>{message}</p>
    </div>
  );
}

function PanelHeading({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <header className="panel-heading">
      <div>
        <h2>{title}</h2>
        <p>{subtitle}</p>
      </div>
    </header>
  );
}

function SubpanelHeading({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <header className="subpanel-heading">
      <h3>{title}</h3>
      <p>{subtitle}</p>
    </header>
  );
}

function ServiceRow({ service }: { service: ServiceHealth }) {
  return (
    <div className="list-row">
      <div>
        <strong>{service.label}</strong>
        <p>{service.message}</p>
      </div>
      <span className={`badge ${service.status}`}>{service.status}</span>
    </div>
  );
}

function ReadinessRow({
  label,
  value,
  ready,
  optional = false
}: {
  label: string;
  value: string;
  ready: boolean;
  optional?: boolean;
}) {
  const badgeClass = ready ? "healthy" : optional ? "candidate" : "degraded";
  const badgeLabel = ready ? "ready" : optional ? "optional" : "missing";

  return (
    <div className="list-row">
      <div>
        <strong>{label}</strong>
        <p>{value}</p>
      </div>
      <span className={`badge ${badgeClass}`}>{badgeLabel}</span>
    </div>
  );
}

function AccountDetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="list-row">
      <div>
        <strong>{label}</strong>
        <p>{value}</p>
      </div>
    </div>
  );
}

function ProbeRow({ probe }: { probe: DiagnosticProbe }) {
  const detailText = summarizeDetails(probe.details);

  return (
    <div className="signal-row">
      <div className="signal-topline">
        <div>
          <strong>{probe.label}</strong>
          <p>{probe.message}</p>
        </div>
        <span className={`badge ${probe.status}`}>{probe.status}</span>
      </div>
      {detailText ? <p className="note mono break-anywhere">{detailText}</p> : null}
    </div>
  );
}

function SignalRow({
  signal,
  onPreview,
  previewing
}: {
  signal: StrategySignal;
  onPreview?: () => void;
  previewing: boolean;
}) {
  return (
    <div className="signal-row">
      <div className="signal-topline">
        <div>
          <strong>
            {signal.symbol} {signal.bias.toUpperCase()}
          </strong>
          <p>
            {signal.setup.replace("_", " ")} | confidence {(signal.confidence * 100).toFixed(0)}%
          </p>
        </div>
        <div className="row-meta">
          <span className={`badge ${signal.status}`}>{signal.status}</span>
          {onPreview ? (
            <button className="inline-action" type="button" onClick={onPreview} disabled={previewing}>
              {previewing ? "Previewing..." : "Preview Order"}
            </button>
          ) : null}
        </div>
      </div>
      <div className="signal-grid">
        <span>Entry {usd.format(signal.entryPrice)}</span>
        <span>Stop {usd.format(signal.stopLoss)}</span>
        <span>TP {usd.format(signal.takeProfit)}</span>
        <span>Notional {usd.format(signal.notionalUsd)}</span>
      </div>
      <p className="note">{signal.reason}</p>
    </div>
  );
}

function RemotePositionRow({ position }: { position: RemotePositionSnapshot }) {
  return (
    <div className="signal-row">
      <div className="signal-topline">
        <div>
          <strong>
            {position.symbol} {position.side.toUpperCase()}
          </strong>
          <p>Ticket #{position.ticket} | size {position.size}</p>
        </div>
        <span className={`badge ${position.side === "long" ? "positive" : "negative"}`}>{position.side}</span>
      </div>
      <div className="signal-grid">
        <span>Entry {usd.format(position.entryPrice)}</span>
        <span>Notional {usd.format(position.notionalUsd)}</span>
        <span>Swap {optionalUsd(position.swapUsd)}</span>
        <span>Floating PnL {optionalUsd(position.profitUsd)}</span>
      </div>
      <p className="note">Updated {formatNullableTimestamp(position.updatedAt)}</p>
    </div>
  );
}

function OrderRow({ order }: { order: OpenOrderSnapshot }) {
  return (
    <div className="signal-row">
      <div className="signal-topline">
        <div>
          <strong>
            {order.symbol} {order.side.toUpperCase()}
          </strong>
          <p>
            {order.orderType.replaceAll("_", " ")} | order #{order.orderId}
          </p>
        </div>
        <span className={`badge ${order.side === "buy" ? "positive" : "negative"}`}>{order.side}</span>
      </div>
      <div className="signal-grid">
        <span>Price {usd.format(order.price)}</span>
        <span>Remaining {order.volumeRemaining}</span>
        <span>Notional {usd.format(order.notionalUsd)}</span>
      </div>
      <p className="note">
        Stop {order.stopPrice == null ? "n/a" : usd.format(order.stopPrice)} | Updated{" "}
        {formatNullableTimestamp(order.updatedAt)}
      </p>
    </div>
  );
}

function PositionRow({ position }: { position: EnginePosition }) {
  return (
    <div className="list-row">
      <div>
        <strong>
          {position.symbol} {position.side.toUpperCase()}
        </strong>
        <p>
          Entry {usd.format(position.entryPrice)} | Mark {usd.format(position.markPrice)}
        </p>
      </div>
      <div className="row-meta">
        <span className={position.pnlUsd >= 0 ? "badge positive" : "badge negative"}>
          {usd.format(position.pnlUsd)}
        </span>
        <span className="mono">{position.pnlPct.toFixed(2)}%</span>
      </div>
    </div>
  );
}

function EventRow({ event }: { event: EventLog }) {
  return (
    <div className="list-row">
      <div>
        <strong>{event.level.toUpperCase()}</strong>
        <p>{event.message}</p>
      </div>
      <span className="mono">{formatTimestamp(event.createdAt)}</span>
    </div>
  );
}

function EmptyState({ message }: { message: string }) {
  return <p className="empty-state">{message}</p>;
}

function summarizeDetails(details: DiagnosticProbe["details"]): string {
  const entries = Object.entries(details);
  if (entries.length === 0) {
    return "";
  }

  return entries
    .slice(0, 6)
    .map(([key, value]) => `${key}=${formatDetailValue(value)}`)
    .join(" | ");
}

function formatDetailValue(value: unknown): string {
  if (Array.isArray(value)) {
    return value.join(",");
  }
  if (typeof value === "boolean") {
    return value ? "true" : "false";
  }
  if (value == null) {
    return "null";
  }
  return String(value);
}

function formatTimestamp(value: string): string {
  return value.replace("T", " ").slice(0, 19);
}

function formatNullableTimestamp(value: string | null | undefined): string {
  if (!value) {
    return "Not available";
  }
  return formatTimestamp(value);
}

function optionalUsd(value: number | null | undefined): string {
  if (value == null) {
    return "Not available";
  }
  return usd.format(value);
}

function buildSetupSteps(
  snapshot: DashboardSnapshot,
  diagnostics: DiagnosticsResponse,
  usingFallback: boolean
): Array<{ label: string; description: string; ready: boolean; optional?: boolean }> {
  return [
    {
      label: "Backend connected",
      description: usingFallback ? "Start the backend so the dashboard uses live bot data." : "The dashboard is talking to the bot backend.",
      ready: !usingFallback
    },
    {
      label: "MT5 account configured",
      description: isMt5Configured(diagnostics) ? "MT5_LOGIN and MT5_SERVER are set on the backend." : "Set MT5_LOGIN and MT5_SERVER on the backend to enable real account sync.",
      ready: isMt5Configured(diagnostics)
    },
    {
      label: "MT5 terminal connected",
      description: diagnostics.config.mt5Connected ? "The backend can prepare and submit orders." : "Start and log into the MT5 terminal before attempting live order flow.",
      ready: diagnostics.config.mt5Connected
    },
    {
      label: "Live market feed",
      description: diagnostics.config.useSimulatedFeed ? "The bot is still using a simulated feed for safety." : "The bot is connected to live MT5 market data.",
      ready: !diagnostics.config.useSimulatedFeed,
      optional: diagnostics.config.useSimulatedFeed
    },
    {
      label: "Trading armed",
      description: snapshot.bot.liveTradingEnabled ? "Live trading is enabled in configuration." : "Live trading is still disabled, which is safer while testing.",
      ready: snapshot.bot.liveTradingEnabled,
      optional: !snapshot.bot.liveTradingEnabled
    }
  ];
}

function buildAttentionItems(
  snapshot: DashboardSnapshot,
  diagnostics: DiagnosticsResponse,
  usingFallback: boolean
): Array<{ title: string; message: string }> {
  const items: Array<{ title: string; message: string }> = [];

  if (usingFallback) {
    items.push({
      title: "Backend is not connected",
      message: "The page is showing fallback data, so you are not looking at the real bot yet."
    });
  }

  if (snapshot.operator.paused) {
    items.push({
      title: "Bot is paused",
      message: "The bot is not scanning for new trades until you resume it."
    });
  }

  if (!isMt5Configured(diagnostics)) {
    items.push({
      title: "No MT5 account configured",
      message: "Set MT5_LOGIN and MT5_SERVER if you want account sync, previews based on real specs, and later live execution."
    });
  }

  diagnostics.probes
    .filter((probe) => probe.status === "degraded")
    .slice(0, 3)
    .forEach((probe) => {
      items.push({
        title: probe.label,
        message: probe.message
      });
    });

  return items;
}

function getNextAction({
  snapshot,
  diagnostics,
  usingFallback,
  previewableSignal
}: {
  snapshot: DashboardSnapshot;
  diagnostics: DiagnosticsResponse;
  usingFallback: boolean;
  previewableSignal: StrategySignal | null;
}): { title: string; description: string } {
  if (usingFallback) {
    return {
      title: "Connect the real backend",
      description: "Restart the backend service so this dashboard can show live bot data instead of demo fallback data."
    };
  }

  if (!isMt5Configured(diagnostics)) {
    return {
      title: "Configure your MT5 account",
      description: "Set MT5_LOGIN and MT5_SERVER first so the bot can sync balances, positions, and orders."
    };
  }

  if (snapshot.account.source !== "mt5" && snapshot.operator.canSyncAccount) {
    return {
      title: "Sync your MT5 account",
      description: "Pull in your real account state so the dashboard shows your live balances, positions, and orders."
    };
  }

  if (snapshot.operator.paused) {
    return {
      title: "Resume the bot",
      description: "The bot is paused, so it is not looking for new trades. Resume it when you are ready."
    };
  }

  if (previewableSignal) {
    return {
      title: `Review ${previewableSignal.symbol} before trading`,
      description: "Preview the strongest current setup to understand the entry, stop, take-profit, and generated order payload."
    };
  }

  return {
    title: "Let the bot keep watching",
    description: "The system looks healthy. Leave it running and wait for a high-quality setup to appear."
  };
}

function matchesQuery(query: string, ...parts: Array<string | number | null | undefined>): boolean {
  if (!query) {
    return true;
  }
  return parts.some((part) => String(part ?? "").toLowerCase().includes(query));
}
