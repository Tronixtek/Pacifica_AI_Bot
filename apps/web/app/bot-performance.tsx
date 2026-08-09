"use client";

import { useEffect, useState } from "react";

// Empty string means same-origin, which is what happens when FastAPI serves
// this page. Set NEXT_PUBLIC_TRADER_API_URL only when running `next dev`
// against a backend on a different port.
const API = process.env.NEXT_PUBLIC_TRADER_API_URL ?? "";

type Bot = {
  botId: string;
  label: string;
  magicNumber: number;
  trades: number;
  wins: number;
  losses: number;
  winRate: number;
  realisedUsd: number;
  unrealisedUsd: number;
  equityImpactUsd: number;
  averageUsd: number;
  openPositions: number;
  openVolume: number;
  bestUsd: number;
  worstUsd: number;
  symbols: string[];
  lastTradeAt: string | null;
  paused: boolean;
  canPause: boolean;
  archived?: boolean;
  archivedReason?: string | null;
};

type Observation = {
  symbol: string;
  timeframe: string;
  higherTimeframe: string | null;
  trend: string | null;
  higherTrend: string | null;
  price: number | null;
  atr: number | null;
  spreadFractionOfAtr: number | null;
  barClosedAt: string | null;
  observedAt: string | null;
  bid: number | null;
  ask: number | null;
  liveSpread: number | null;
  liveSpreadFractionOfAtr: number | null;
  status: string;
  reason: string | null;
  pattern: string | null;
  direction: string | null;
  riskAtr: number | null;
};

type Refusal = {
  at: string;
  symbol: string;
  direction: string;
  pattern: string | null;
  reason: string;
};

type Activity = {
  generatedAt: string;
  running: boolean;
  paused: boolean;
  signalsSeen: number;
  declined: number;
  topReasons: [string, number][];
  markets: Observation[];
  refusals: Refusal[];
  events: string[];
};

type Fleet = {
  generatedAt: string;
  accountBalanceUsd: number | null;
  accountEquityUsd: number | null;
  currency: string | null;
  openPositions: number;
  bots: Bot[];
};

const money = (v: number) =>
  `${v >= 0 ? "+" : "−"}$${Math.abs(v).toFixed(2)}`;

function agoOf(iso: string | null) {
  if (!iso) return null;
  const secs = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`;
  return `${Math.round(secs / 3600)}h ago`;
}

function toneOf(v: number) {
  if (v > 0) return "pos";
  if (v < 0) return "neg";
  return "flat";
}

export function BotPerformanceBoard() {
  const [fleet, setFleet] = useState<Fleet | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [activity, setActivity] = useState<Activity | null>(null);

  const toggle = async (bot: Bot) => {
    setBusy(bot.botId);
    try {
      const action = bot.paused ? "resume" : "pause";
      const res = await fetch(`${API}/api/bots/${bot.botId}/${action}`, {
        method: "POST",
      });
      const data = await res.json();
      setNotice(data.message ?? null);
      // Reflect the new state immediately rather than waiting for the poll,
      // so the button does not appear to do nothing for up to five seconds.
      setFleet((f) =>
        f
          ? {
              ...f,
              bots: f.bots.map((b) =>
                b.botId === bot.botId ? { ...b, paused: data.paused } : b
              ),
            }
          : f
      );
    } catch (e) {
      setNotice(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const res = await fetch(`${API}/api/bots`, { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data: Fleet = await res.json();
        if (alive) {
          setFleet(data);
          setError(null);
        }
        // Fetched separately so a failure here cannot blank the performance
        // numbers, which are the thing that must always render.
        try {
          const ar = await fetch(`${API}/api/bots/edge/activity`, { cache: "no-store" });
          if (ar.ok && alive) setActivity(await ar.json());
        } catch {
          /* diagnostic only - losing it is not worth an error banner */
        }
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      }
    };
    load();
    const timer = setInterval(load, 5000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  const total = fleet?.bots.reduce((a, b) => a + b.equityImpactUsd, 0) ?? 0;

  return (
    <main className="board">
      <header className="head">
        <div>
          <h1>Bot Performance</h1>
          <p className="sub">
            Three strategies, one demo account, separated by magic number.
          </p>
        </div>
        <div className="acct">
          {fleet?.accountEquityUsd != null && (
            <>
              <span className="acct-eq">
                ${fleet.accountEquityUsd.toFixed(2)}
              </span>
              <span className="acct-lbl">
                equity · {fleet.openPositions} open
              </span>
            </>
          )}
        </div>
      </header>

      {error && (
        <div className="err">
          Cannot reach the trader service{API ? ` at ${API}` : ""} — {error}
        </div>
      )}

      {!fleet && !error && <div className="err">Loading…</div>}

      {notice && (
        <div className="notice" onClick={() => setNotice(null)}>
          {notice}
        </div>
      )}

      <div className="grid">
        {fleet?.bots.map((bot) => (
          <section key={bot.botId} className={`card ${toneOf(bot.equityImpactUsd)}`}>
            <div className="card-top">
              <h2>{bot.label}</h2>
              <span className="magic">#{bot.magicNumber}</span>
            </div>

            {bot.archived ? (
              <div className="archived-flag">
                <strong>ARCHIVED</strong> &mdash; retired, not running.
                {bot.archivedReason ? ` ${bot.archivedReason}` : ""}
                {" "}Figures below are history.
              </div>
            ) : bot.paused ? (
              <div className="paused-flag">
                Paused &mdash; no new trades. Open positions still managed.
              </div>
            ) : null}

            <div className={`headline ${toneOf(bot.equityImpactUsd)}`}>
              {money(bot.equityImpactUsd)}
            </div>
            <div className="headline-sub">
              {money(bot.realisedUsd)} realised
              {bot.unrealisedUsd !== 0 && <> · {money(bot.unrealisedUsd)} open</>}
            </div>

            <dl className="stats">
              <div>
                <dt>Trades</dt>
                <dd>{bot.trades}</dd>
              </div>
              <div>
                <dt>Win rate</dt>
                {/* Shown next to average, never alone: a high win rate with a
                    negative average is the failure mode these bots exhibit. */}
                <dd>{bot.trades ? `${bot.winRate}%` : "—"}</dd>
              </div>
              <div>
                <dt>Avg / trade</dt>
                <dd className={toneOf(bot.averageUsd)}>
                  {bot.trades ? money(bot.averageUsd) : "—"}
                </dd>
              </div>
              <div>
                <dt>Open</dt>
                <dd>
                  {bot.openPositions}
                  {bot.openVolume > 0 && (
                    <span className="dim"> · {bot.openVolume} lots</span>
                  )}
                </dd>
              </div>
              <div>
                <dt>Best</dt>
                <dd className="pos">{bot.trades ? money(bot.bestUsd) : "—"}</dd>
              </div>
              <div>
                <dt>Worst</dt>
                <dd className="neg">{bot.trades ? money(bot.worstUsd) : "—"}</dd>
              </div>
            </dl>

            <footer className="card-foot">
              <span>
                {bot.symbols.length > 0 ? bot.symbols.join(" · ") : "no trades yet"}
              </span>
              {bot.canPause && !bot.archived && (
                <button
                  className={bot.paused ? "btn resume" : "btn pause"}
                  onClick={() => toggle(bot)}
                  disabled={busy === bot.botId}
                >
                  {busy === bot.botId
                    ? "…"
                    : bot.paused
                    ? "Resume"
                    : "Stop"}
                </button>
              )}
            </footer>
          </section>
        ))}
      </div>


      {activity && (
        <section className="live">
          <div className="live-head">
            <h2>What the bot is seeing</h2>
            <span className="live-meta">
              {activity.signalsSeen} signal{activity.signalsSeen === 1 ? "" : "s"}
              {" / "}
              {activity.declined} declined
              {activity.paused ? " / PAUSED" : ""}
              {" / updated "}
              {agoOf(activity.generatedAt) ?? "-"}
            </span>
          </div>

          {activity.markets.length === 0 && (
            <p className="reason">
              {activity.running
                ? "Starting up - the first observation lands within a poll."
                : "The edge bot is not running."}
            </p>
          )}

          <div className="obs">
            {activity.markets.map((m) => (
              <div key={m.symbol} className={`ob ${m.status === "signal" ? "hot" : ""}`}>
                <div className="ob-top">
                  <strong>{m.symbol}</strong>
                  <span className="tf">
                    {m.timeframe}
                    {m.higherTimeframe ? ` under ${m.higherTimeframe}` : ""}
                  </span>
                </div>

                <div className="trends">
                  <span className={`pill ${m.trend ?? "none"}`}>
                    {m.timeframe} {m.trend ?? "no trend"}
                  </span>
                  {m.higherTimeframe && (
                    <span className={`pill ${m.higherTrend ?? "none"}`}>
                      {m.higherTimeframe} {m.higherTrend ?? "no trend"}
                    </span>
                  )}
                  {m.trend && m.higherTrend && m.trend !== m.higherTrend && (
                    <span className="pill blocked">vetoed</span>
                  )}
                </div>

                <p className="reason">{m.reason}</p>

                <div className="ob-facts">
                  {m.bid != null && m.ask != null ? (
                    <span className="quote">
                      {m.bid} / {m.ask}
                    </span>
                  ) : (
                    m.price != null && <span>price {m.price}</span>
                  )}
                  {m.atr != null && <span>ATR {m.atr.toFixed(2)}</span>}
                  {m.spreadFractionOfAtr != null && (
                    <span
                      className={m.spreadFractionOfAtr > 0.25 ? "warn" : ""}
                      title="Spread as a fraction of ATR. This ratio decided every result measured."
                    >
                      spread {(m.spreadFractionOfAtr * 100).toFixed(1)}% of ATR
                    </span>
                  )}
                  {m.barClosedAt && (
                    <span>bar {new Date(m.barClosedAt).toISOString().slice(11, 16)} UTC</span>
                  )}
                  {m.observedAt && (
                    <span
                      className={
                        Date.now() - new Date(m.observedAt).getTime() > 120000 ? "warn" : "fresh"
                      }
                      title="How long ago the bot last looked at this market. If this stops moving, the loop has stopped."
                    >
                      seen {agoOf(m.observedAt)}
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>

          {activity.topReasons.length > 0 && (
            <div className="reasons">
              <span className="reasons-label">Why it is waiting</span>
              {activity.topReasons.map(([reason, n]) => (
                <div key={reason} className="reason-row">
                  <span className="count">{n}</span>
                  <span>{reason}</span>
                </div>
              ))}
            </div>
          )}

          {activity.refusals.length > 0 && (
            <div className="reasons">
              <span className="reasons-label">Qualified but declined</span>
              {activity.refusals.slice(0, 6).map((r, i) => (
                <div key={i} className="reason-row">
                  <span className="count">
                    {new Date(r.at).toISOString().slice(11, 16)}
                  </span>
                  <span>
                    <strong>{r.symbol} {r.direction}</strong>
                    {r.pattern ? ` (${r.pattern})` : ""} - {r.reason}
                  </span>
                </div>
              ))}
            </div>
          )}

          {activity.events.length > 0 && (
            <details className="log">
              <summary>Activity log ({activity.events.length})</summary>
              <pre>{activity.events.join("\n")}</pre>
            </details>
          )}
        </section>
      )}

      {fleet && fleet.bots.length > 0 && (
        <div className="total">
          <span>Combined</span>
          <strong className={toneOf(total)}>{money(total)}</strong>
        </div>
      )}

      <style>{`

        .live { margin-top: 32px; border: 1px solid rgba(148,163,184,0.22);
          border-radius: 12px; padding: 18px 20px 20px; }
        .live-head { display: flex; justify-content: space-between; align-items: baseline;
          gap: 16px; flex-wrap: wrap; margin-bottom: 14px; }
        .live h2 { font-size: 15px; font-weight: 600; margin: 0; letter-spacing: -0.01em; }
        .live-meta { font-size: 12px; opacity: 0.6; font-variant-numeric: tabular-nums; }
        .obs { display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }
        .ob { border: 1px solid rgba(148,163,184,0.18); border-radius: 9px; padding: 12px 14px; }
        .ob.hot { border-color: rgba(52,211,153,0.55); background: rgba(52,211,153,0.07); }
        .ob-top { display: flex; justify-content: space-between; align-items: baseline; gap: 10px; }
        .ob-top strong { font-size: 14px; }
        .tf { font-size: 11px; opacity: 0.55; }
        .trends { display: flex; gap: 6px; flex-wrap: wrap; margin: 9px 0; }
        .pill { font-size: 10px; letter-spacing: 0.04em; text-transform: uppercase;
          padding: 3px 7px; border-radius: 5px; border: 1px solid transparent; }
        .pill.up { color: #6ee7b7; border-color: rgba(110,231,183,0.35); background: rgba(110,231,183,0.10); }
        .pill.down { color: #fca5a5; border-color: rgba(252,165,165,0.35); background: rgba(252,165,165,0.10); }
        .pill.none { color: #94a3b8; border-color: rgba(148,163,184,0.3); }
        .pill.blocked { color: #fcd34d; border-color: rgba(252,211,77,0.4); background: rgba(252,211,77,0.10); }
        .reason { font-size: 12px; line-height: 1.5; margin: 0 0 9px; opacity: 0.78; }
        .ob-facts { display: flex; gap: 12px; flex-wrap: wrap; font-size: 11px;
          opacity: 0.5; font-variant-numeric: tabular-nums; }
        .ob-facts .warn { color: #fcd34d; opacity: 1; }
        .ob-facts .fresh { color: #6ee7b7; opacity: 0.85; }
        .ob-facts .quote { font-weight: 600; opacity: 0.85; }
        .reasons { margin-top: 16px; }
        .reasons-label { display: block; font-size: 11px; text-transform: uppercase;
          letter-spacing: 0.07em; opacity: 0.45; margin-bottom: 7px; }
        .reason-row { display: flex; gap: 10px; font-size: 12px; padding: 3px 0;
          line-height: 1.5; opacity: 0.8; }
        .reason-row .count { min-width: 46px; text-align: right; opacity: 0.55;
          font-variant-numeric: tabular-nums; }
        .log { margin-top: 16px; font-size: 12px; }
        .log summary { cursor: pointer; opacity: 0.55; }
        .log pre { margin: 9px 0 0; padding: 11px; border-radius: 7px; overflow-x: auto;
          background: rgba(148,163,184,0.08); font-size: 11px; line-height: 1.65; }
        .board { max-width: 1100px; margin: 0 auto; padding: 40px 24px 64px; }
        .head { display: flex; justify-content: space-between; align-items: flex-start;
                gap: 24px; flex-wrap: wrap; margin-bottom: 28px; }
        h1 { font-size: 26px; font-weight: 600; letter-spacing: -0.02em; margin: 0; }
        .sub { margin: 6px 0 0; font-size: 14px; opacity: 0.6; }
        .acct { text-align: right; }
        .acct-eq { display: block; font-size: 26px; font-weight: 600;
                   font-variant-numeric: tabular-nums; }
        .acct-lbl { font-size: 12px; opacity: 0.55; }
        .err { border: 1px solid rgba(220,60,60,0.35); background: rgba(220,60,60,0.07);
               padding: 14px 16px; border-radius: 10px; font-size: 14px; margin-bottom: 20px; }
        .grid { display: grid; gap: 16px;
                grid-template-columns: repeat(auto-fit, minmax(290px, 1fr)); }
        .card { border: 1px solid rgba(128,128,128,0.22); border-radius: 14px;
                padding: 20px; display: flex; flex-direction: column; gap: 4px; }
        .card.pos { border-color: rgba(38,166,110,0.45); }
        .card.neg { border-color: rgba(220,70,70,0.40); }
        .card:has(.paused-flag) { opacity: 0.72; }
        .card:has(.archived-flag) { opacity: 0.55; filter: grayscale(0.8); }
        .card-top { display: flex; justify-content: space-between; align-items: baseline;
                    gap: 12px; margin-bottom: 10px; }
        h2 { font-size: 15px; font-weight: 600; margin: 0; }
        .magic { font-size: 11px; opacity: 0.45; font-variant-numeric: tabular-nums; }
        .headline { font-size: 32px; font-weight: 650; letter-spacing: -0.02em;
                    font-variant-numeric: tabular-nums; }
        .headline-sub { font-size: 12px; opacity: 0.6; margin-bottom: 16px;
                        font-variant-numeric: tabular-nums; }
        .pos { color: #26a66e; }
        .neg { color: #dc4646; }
        .flat { opacity: 0.75; }
        .stats { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 16px;
                 margin: 0 0 16px; }
        .stats div { display: flex; flex-direction: column; gap: 2px; }
        dt { font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em;
             opacity: 0.5; }
        dd { margin: 0; font-size: 15px; font-weight: 550;
             font-variant-numeric: tabular-nums; }
        .dim { font-weight: 400; opacity: 0.5; font-size: 12px; }
        .card-foot { font-size: 11px; opacity: 0.45; border-top: 1px solid rgba(128,128,128,0.15);
                     padding-top: 12px; margin-top: auto; display: flex;
                     align-items: center; justify-content: space-between; gap: 12px; }
        .btn { font: inherit; font-size: 12px; font-weight: 550; padding: 7px 16px;
               border-radius: 8px; cursor: pointer; border: 1px solid transparent;
               background: transparent; opacity: 1; }
        /* Comfortably past the 44px touch target once padding is counted -
           this is used one-handed on a phone. */
        .btn { min-height: 34px; min-width: 78px; }
        .btn.pause { border-color: rgba(220,70,70,0.5); color: #dc4646; }
        .btn.pause:hover { background: rgba(220,70,70,0.1); }
        .btn.resume { border-color: rgba(38,166,110,0.55); color: #26a66e; }
        .btn.resume:hover { background: rgba(38,166,110,0.12); }
        .btn:disabled { opacity: 0.45; cursor: default; }
        .archived-flag { font-size: 11px; padding: 8px 10px; margin-bottom: 12px;
          border-radius: 6px; line-height: 1.5;
          border: 1px solid rgba(148,163,184,0.45);
          background: rgba(100,116,139,0.16); color: #94a3b8; }
        .archived-flag strong { letter-spacing: 0.08em; color: #cbd5e1; }
        .paused-flag { font-size: 11px; padding: 6px 10px; margin-bottom: 12px;
                       border-radius: 7px; background: rgba(220,160,40,0.13);
                       border: 1px solid rgba(220,160,40,0.35); }
        .notice { border: 1px solid rgba(128,128,128,0.3); border-radius: 10px;
                  padding: 12px 16px; font-size: 13px; margin-bottom: 18px;
                  cursor: pointer; }
        .total { display: flex; justify-content: space-between; align-items: baseline;
                 margin-top: 24px; padding-top: 18px;
                 border-top: 1px solid rgba(128,128,128,0.22); font-size: 14px; }
        .total strong { font-size: 24px; font-variant-numeric: tabular-nums; }
        @media (max-width: 480px) { .stats { grid-template-columns: 1fr; } }
      `}</style>
    </main>
  );
}
