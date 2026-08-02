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

function toneOf(v: number) {
  if (v > 0) return "pos";
  if (v < 0) return "neg";
  return "flat";
}

export function BotPerformanceBoard() {
  const [fleet, setFleet] = useState<Fleet | null>(null);
  const [error, setError] = useState<string | null>(null);

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

      <div className="grid">
        {fleet?.bots.map((bot) => (
          <section key={bot.botId} className={`card ${toneOf(bot.equityImpactUsd)}`}>
            <div className="card-top">
              <h2>{bot.label}</h2>
              <span className="magic">#{bot.magicNumber}</span>
            </div>

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
              {bot.symbols.length > 0 ? bot.symbols.join(" · ") : "no trades yet"}
            </footer>
          </section>
        ))}
      </div>

      {fleet && fleet.bots.length > 0 && (
        <div className="total">
          <span>Combined</span>
          <strong className={toneOf(total)}>{money(total)}</strong>
        </div>
      )}

      <style>{`
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
                     padding-top: 12px; margin-top: auto; }
        .total { display: flex; justify-content: space-between; align-items: baseline;
                 margin-top: 24px; padding-top: 18px;
                 border-top: 1px solid rgba(128,128,128,0.22); font-size: 14px; }
        .total strong { font-size: 24px; font-variant-numeric: tabular-nums; }
        @media (max-width: 480px) { .stats { grid-template-columns: 1fr; } }
      `}</style>
    </main>
  );
}
