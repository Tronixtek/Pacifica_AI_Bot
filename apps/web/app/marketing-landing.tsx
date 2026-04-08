"use client";

import type {
  AccountConfigurationSource,
  AccountLinkResponse,
  DashboardSnapshot,
  DiagnosticsResponse,
  LivePerformanceSummary,
  OperatorActionResponse,
  PaperClosedTrade,
  PaperPerformanceSummary,
  SignalPreviewResponse,
  StrategySignal
} from "@pacifica-hackathon/shared";
import { startTransition, useDeferredValue, useEffect, useEffectEvent, useState } from "react";
import { useRouter } from "next/navigation";

import { detectBrowserWallets, getConnectedWalletAddress, shortenWalletAddress, type DetectedWallet } from "../lib/browser-wallet";
import {
  linkAccount,
  pauseStrategy,
  previewSignal,
  resetPaperAccount,
  resumeStrategy,
  submitSmokeTestOrder,
  syncAccount,
  topUpPaperAccount,
  unlinkAccount
} from "../lib/api";
import { LiveMarketWorkspace } from "./live-market-workspace";

const usd = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2
});

const FEATURE_COLUMNS = [
  {
    label: "START FAST",
    items: [
      ["Wallet-ready setup", "Connect your Pacifica account and see your sync status immediately."],
      ["Testnet-first launch", "Practice the full flow safely before you move anywhere near live capital."],
      ["Explainable signals", "Every setup tells you what the bot saw and why it wants to trade."]
    ]
  },
  {
    label: "TRADE SMART",
    items: [
      ["Breakout engine", "Capture range expansions with structured entries, stops, and targets."],
      ["Liquidity sweeps", "Spot failed moves and reclaim setups before the crowd catches on."],
      ["Risk guardrails", "Daily loss limits, capped sizing, and TP/SL protection are built in."]
    ]
  },
  {
    label: "STAY IN CONTROL",
    items: [
      ["Order preview", "Inspect the exact payload before the bot sends anything to Pacifica."],
      ["Live cockpit", "Monitor account sync, trade ideas, positions, and recent activity in one place."],
      ["Builder-ready", "Designed for Pacifica builder code attribution and agent-key workflows."]
    ]
  }
] as const;

const BENEFIT_CARDS = [
  {
    title: "Automate your trading",
    copy: "Let the bot watch Pacifica markets all day, detect setups, and line up the next trade without emotional hesitation."
  },
  {
    title: "Keep full visibility",
    copy: "See account sync, active opportunities, order previews, open positions, and recent bot activity in one guided workspace."
  },
  {
    title: "Go from demo to real",
    copy: "Start on paper or testnet, refine the strategy, and only enable live execution when the checks are green."
  }
] as const;

const NAV_ITEMS = [
  ["Get Started", "onboarding"],
  ["Why Pacifica Bot", "why"],
  ["Features", "features"],
  ["Live Bot", "live"],
  ["Advanced", "advanced"]
] as const;

const EXPERIENCE_OPTIONS = [
  ["beginner", "Beginner"],
  ["intermediate", "Intermediate"],
  ["advanced", "Advanced"]
] as const;

const LAUNCH_OPTIONS = [
  {
    id: "paper",
    label: "Paper",
    description: "Learn the flow safely with simulated capital while the bot explains every setup."
  },
  {
    id: "testnet",
    label: "Testnet",
    description: "Connect Pacifica testnet and validate syncing, previewing, and order flow with no real risk."
  },
  {
    id: "live",
    label: "Live Later",
    description: "Prepare for a real-money rollout only after account sync, agent keys, and safeguards are all green."
  }
] as const;

const ONBOARDING_STORAGE_KEY = "pacifica-bot-onboarding-v1";

type ExperienceLevel = "beginner" | "intermediate" | "advanced";
type LaunchPreference = "paper" | "testnet" | "live";

interface OnboardingState {
  fullName: string;
  email: string;
  experience: ExperienceLevel | null;
  profileCreated: boolean;
  launchMode: LaunchPreference | null;
  walletProvider: string | null;
  walletAddress: string | null;
  accountAddressInput: string;
  riskAccepted: boolean;
  builderAcknowledged: boolean;
}

interface OnboardingStatus {
  profileReady: boolean;
  experienceReady: boolean;
  launchModeReady: boolean;
  accessRequired: boolean;
  accessReady: boolean;
  executionReady: boolean;
  safeguardsReady: boolean;
  cockpitUnlocked: boolean;
  completedCount: number;
  totalCount: number;
}

const DEFAULT_ONBOARDING_STATE: OnboardingState = {
  fullName: "",
  email: "",
  experience: null,
  profileCreated: false,
  launchMode: null,
  walletProvider: null,
  walletAddress: null,
  accountAddressInput: "",
  riskAccepted: false,
  builderAcknowledged: false
};

export function MarketingLanding({
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
  const [operatorState, setOperatorState] = useState(snapshot.operator);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [actionTone, setActionTone] = useState<"positive" | "negative">("positive");
  const [actionPending, setActionPending] = useState<string | null>(null);
  const [preview, setPreview] = useState<SignalPreviewResponse | null>(null);
  const [selectedChartSymbol, setSelectedChartSymbol] = useState(
    snapshot.marketCharts[0]?.symbol ?? snapshot.watchlist[0]?.symbol ?? "BTC"
  );
  const [onboarding, setOnboarding] = useState<OnboardingState>(DEFAULT_ONBOARDING_STATE);
  const [detectedWallets, setDetectedWallets] = useState<DetectedWallet[]>([]);
  const [autoLinkedAddress, setAutoLinkedAddress] = useState<string | null>(null);
  const [linkedAccountAddressState, setLinkedAccountAddressState] = useState<string | null>(
    diagnostics.config.effectiveAccountAddress
  );
  const [linkedAccountSourceState, setLinkedAccountSourceState] = useState<AccountConfigurationSource | null>(
    diagnostics.config.accountConfigurationSource
  );
  const [paperTopUpInput, setPaperTopUpInput] = useState("2500");
  const [hasHydrated, setHasHydrated] = useState(false);
  const deferredQuery = useDeferredValue(query.trim().toLowerCase());
  const visibleSignals = snapshot.signals.filter((signal) => signal.status !== "blocked");
  const filteredSignals = visibleSignals.filter((signal) =>
    matchesQuery(deferredQuery, signal.symbol, signal.setup, signal.bias, signal.reason)
  );
  const filteredEvents = snapshot.events.filter((event) =>
    matchesQuery(deferredQuery, event.level, event.message)
  );
  const degradedProbes = diagnostics.probes.filter((probe) => probe.status === "degraded");
  const previewableSignal = filteredSignals[0] ?? visibleSignals[0] ?? null;
  const linkedAccountAddress = linkedAccountAddressState;
  const linkedAccountSource = linkedAccountSourceState;
  const onboardingStatus = buildOnboardingStatus(onboarding, diagnostics, snapshot, usingFallback);
  const nextAction = getNextAction(snapshot, diagnostics, usingFallback, previewableSignal, onboardingStatus);
  const setupSteps = buildSetupSteps(snapshot, diagnostics, usingFallback);
  const attentionItems = buildAttentionItems(snapshot, diagnostics, usingFallback, onboardingStatus);
  const canManagePaperBalance = snapshot.bot.mode === "paper";
  const showingLivePerformance = isLivePerformanceMode(snapshot);
  const activePerformanceSummary = getPrimaryPerformanceSummary(snapshot);
  const activePerformanceTrades = getPrimaryPerformanceClosedTrades(snapshot);
  const performanceEyebrow =
    snapshot.bot.mode === "testnet"
      ? "Testnet performance"
      : snapshot.bot.mode === "mainnet"
        ? "Live performance"
        : "Paper performance";
  const performanceHeading = showingLivePerformance
    ? activePerformanceSummary.closedTrades > 0
      ? `${snapshot.bot.mode === "testnet" ? "Testnet" : "Live"} performance is being tracked`
      : `Waiting for the first closed ${snapshot.bot.mode === "testnet" ? "testnet" : "live"} trade`
    : snapshot.paperPerformance.currentModeSummary.closedTrades >= 12
      ? "Paper validation is building confidence"
      : "Keep collecting paper samples before testnet";
  const performanceDescription = showingLivePerformance
    ? snapshot.livePerformance?.trackingBasis ?? ""
    : snapshot.paperPerformance.comparisonMethod;

  useEffect(() => {
    setHasHydrated(true);
  }, []);

  useEffect(() => {
    setOperatorState(snapshot.operator);
  }, [snapshot.operator]);

  useEffect(() => {
    setLinkedAccountAddressState(diagnostics.config.effectiveAccountAddress);
    setLinkedAccountSourceState(diagnostics.config.accountConfigurationSource);
  }, [diagnostics.config.effectiveAccountAddress, diagnostics.config.accountConfigurationSource]);

  useEffect(() => {
    if (snapshot.marketCharts.some((chart) => chart.symbol === selectedChartSymbol)) {
      return;
    }
    const nextSymbol = snapshot.marketCharts[0]?.symbol ?? snapshot.watchlist[0]?.symbol;
    if (nextSymbol) {
      setSelectedChartSymbol(nextSymbol);
    }
  }, [selectedChartSymbol, snapshot.marketCharts, snapshot.watchlist]);

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(ONBOARDING_STORAGE_KEY);
      if (!raw) {
        return;
      }
      const stored = JSON.parse(raw) as Partial<OnboardingState>;
      setOnboarding((current) => ({
        ...current,
        ...stored,
        experience:
          stored.experience === "beginner" || stored.experience === "intermediate" || stored.experience === "advanced"
            ? stored.experience
            : current.experience,
        launchMode:
          stored.launchMode === "paper" || stored.launchMode === "testnet" || stored.launchMode === "live"
            ? stored.launchMode
            : current.launchMode
      }));
    } catch {
      // Ignore broken local onboarding drafts and continue with defaults.
    }
  }, []);

  useEffect(() => {
    window.localStorage.setItem(ONBOARDING_STORAGE_KEY, JSON.stringify(onboarding));
  }, [onboarding]);

  const refreshWallets = useEffectEvent(() => {
    const wallets = detectBrowserWallets();
    setDetectedWallets((current) => (sameWallets(current, wallets) ? current : wallets));

    const connected = wallets.find((wallet) => getConnectedWalletAddress(wallet.adapter));
    if (!connected) {
      setOnboarding((current) =>
        current.walletProvider === null && current.walletAddress === null
          ? current
          : {
              ...current,
              walletProvider: null,
              walletAddress: null
            }
      );
      return;
    }

    const connectedAddress = getConnectedWalletAddress(connected.adapter);
    if (!connectedAddress) {
      return;
    }

    setOnboarding((current) => {
      const nextAccountInput = current.accountAddressInput || connectedAddress;
      if (
        current.walletProvider === connected.label &&
        current.walletAddress === connectedAddress &&
        current.accountAddressInput === nextAccountInput
      ) {
        return current;
      }

      return {
        ...current,
        walletProvider: connected.label,
        walletAddress: connectedAddress,
        accountAddressInput: nextAccountInput
      };
    });
  });

  useEffect(() => {
    if (!linkedAccountAddress) {
      return;
    }
    setOnboarding((current) =>
      current.accountAddressInput
        ? current
        : {
            ...current,
            accountAddressInput: linkedAccountAddress
          }
    );
  }, [linkedAccountAddress]);

  useEffect(() => {
    refreshWallets();
    const handleFocus = () => refreshWallets();
    window.addEventListener("focus", handleFocus);
    return () => window.removeEventListener("focus", handleFocus);
  }, []);

  useEffect(() => {
    const accountAddress = onboarding.accountAddressInput.trim();
    if (
      usingFallback ||
      !onboarding.walletAddress ||
      !!linkedAccountAddress ||
      !looksLikeWalletAddress(accountAddress) ||
      autoLinkedAddress === accountAddress
    ) {
      return;
    }

    setAutoLinkedAddress(accountAddress);
    void attemptAutoLink(accountAddress);
  }, [
    onboarding.walletAddress,
    onboarding.accountAddressInput,
    linkedAccountAddress,
    usingFallback,
    autoLinkedAddress
  ]);

  const refreshSnapshot = useEffectEvent(() => {
    startTransition(() => {
      router.refresh();
    });
  });

  useEffect(() => {
    if (!autoRefresh) {
      return;
    }
    const timer = window.setInterval(() => refreshSnapshot(), 15000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, refreshSnapshot]);

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

  const applyAccountLinkResult = useEffectEvent((result: AccountLinkResponse) => {
    setOperatorState(result.operator);
    setLinkedAccountAddressState(result.linkedAccountAddress);
    setLinkedAccountSourceState(result.accountConfigurationSource);
    setActionMessage(result.message);
    setActionTone(result.ok ? "positive" : "negative");
  });

  const attemptAutoLink = useEffectEvent(async (accountAddress: string) => {
    try {
      const result = await linkAccount(accountAddress);
      applyAccountLinkResult(result);
      if (result.ok) {
        refreshSnapshot();
      }
    } catch {
      // Keep the manual link action available if the automatic session link fails.
    }
  });

  const runAction = useEffectEvent(async (key: string, work: () => Promise<OperatorActionResponse>) => {
    setActionPending(key);
    try {
      const result = await work();
      applyOperatorResult(result);
      refreshSnapshot();
    } catch (error) {
      setActionMessage(error instanceof Error ? error.message : "Action failed.");
      setActionTone("negative");
    } finally {
      setActionPending(null);
    }
  });

  const handlePauseResume = useEffectEvent(async () => {
    await runAction(operatorState.paused ? "resume" : "pause", operatorState.paused ? resumeStrategy : pauseStrategy);
  });

  const handleSync = useEffectEvent(async () => {
    await runAction("sync", syncAccount);
  });

  const handleSmokeTestOrder = useEffectEvent(async () => {
    await runAction("smoke-test", () => submitSmokeTestOrder(selectedChartSymbol));
  });

  const handlePreview = useEffectEvent(async (signal: StrategySignal) => {
    setActionPending(`preview:${signal.id}`);
    try {
      const result = await previewSignal(signal.id);
      setPreview(result);
      applyOperatorResult(result);
    } catch (error) {
      setActionMessage(error instanceof Error ? error.message : "Preview failed.");
      setActionTone("negative");
    } finally {
      setActionPending(null);
    }
  });

  const handleResetPaperBalance = useEffectEvent(async () => {
    await runAction("paper-reset", resetPaperAccount);
  });

  const handleTopUpPaperBalance = useEffectEvent(async () => {
    const amountUsd = Number(paperTopUpInput.trim());
    if (!Number.isFinite(amountUsd) || amountUsd <= 0) {
      setActionMessage("Enter a valid top-up amount greater than zero.");
      setActionTone("negative");
      return;
    }

    await runAction("paper-top-up", () => topUpPaperAccount(amountUsd));
  });

  const handleCreateProfile = useEffectEvent(() => {
    if (!isValidProfile(onboarding)) {
      setActionMessage("Add your full name, email, and experience level before creating the bot workspace.");
      setActionTone("negative");
      return;
    }

    setOnboarding((current) => ({
      ...current,
      profileCreated: true
    }));
    setActionMessage("Workspace created. You can keep customizing your launch flow below.");
    setActionTone("positive");
  });

  const handleConnectWallet = useEffectEvent(async (wallet: DetectedWallet) => {
    setActionPending(`connect:${wallet.id}`);
    try {
      const response = await wallet.adapter.connect();
      const responseAddress =
        typeof response === "object" &&
        response !== null &&
        "publicKey" in response &&
        response.publicKey
          ? response.publicKey.toString()
          : null;
      const address = responseAddress ?? getConnectedWalletAddress(wallet.adapter);

      if (!address) {
        throw new Error("Wallet connected, but no public address was returned.");
      }

      setOnboarding((current) => ({
        ...current,
        walletProvider: wallet.label,
        walletAddress: address,
        accountAddressInput: current.accountAddressInput || address
      }));
      let linkSucceeded = false;
      try {
        const linkResult = await linkAccount(address);
        applyAccountLinkResult(linkResult);
        linkSucceeded = linkResult.ok;
        if (linkResult.ok) {
          refreshSnapshot();
        }
      } catch {
        linkSucceeded = false;
      }

      setActionMessage(
        linkSucceeded
          ? `${wallet.label} connected and Pacifica account linked.`
          : `${wallet.label} connected. Link the Pacifica account to continue.`
      );
      setActionTone("positive");
      refreshWallets();
    } catch (error) {
      setActionMessage(error instanceof Error ? error.message : "Wallet connection failed.");
      setActionTone("negative");
    } finally {
      setActionPending(null);
    }
  });

  const handleDisconnectWallet = useEffectEvent(async () => {
    const activeWallet = detectedWallets.find((wallet) => wallet.label === onboarding.walletProvider);
    setActionPending("disconnect-wallet");
    try {
      if (activeWallet?.adapter.disconnect) {
        await activeWallet.adapter.disconnect();
      }
      setOnboarding((current) => ({
        ...current,
        walletProvider: null,
        walletAddress: null
      }));
      setActionMessage("Browser wallet disconnected.");
      setActionTone("positive");
      refreshWallets();
    } catch (error) {
      setActionMessage(error instanceof Error ? error.message : "Wallet disconnect failed.");
      setActionTone("negative");
    } finally {
      setActionPending(null);
    }
  });

  const handleLinkAccount = useEffectEvent(async () => {
    const accountAddress = onboarding.accountAddressInput.trim();
    if (!looksLikeWalletAddress(accountAddress)) {
      setActionMessage("Enter a valid Pacifica account address before linking it.");
      setActionTone("negative");
      return;
    }

    setActionPending("link-account");
    try {
      const result = await linkAccount(accountAddress);
      applyAccountLinkResult(result);
      refreshSnapshot();
    } catch (error) {
      setActionMessage(error instanceof Error ? error.message : "Account link failed.");
      setActionTone("negative");
    } finally {
      setActionPending(null);
    }
  });

  const handleUnlinkAccount = useEffectEvent(async () => {
    setActionPending("unlink-account");
    try {
      const result = await unlinkAccount();
      applyAccountLinkResult(result);
      refreshSnapshot();
    } catch (error) {
      setActionMessage(error instanceof Error ? error.message : "Account unlink failed.");
      setActionTone("negative");
    } finally {
      setActionPending(null);
    }
  });

  return (
    <main className="marketing-home">
      <header className="marketing-nav">
        <div className="page-shell nav-inner">
          <button className="brand-lockup" type="button" onClick={() => scrollToSection("top")}>
            PACIFICA<span>BOT</span>
          </button>

          <nav className="nav-links" aria-label="Primary">
            {NAV_ITEMS.map(([label, id]) => (
              <button key={id} className="nav-link" type="button" onClick={() => scrollToSection(id)}>
                {label}
              </button>
            ))}
          </nav>

          <div className="nav-actions">
            <button className="nav-ghost" type="button" onClick={() => scrollToSection("live")}>
              Live Bot
            </button>
            <button className="nav-cta" type="button" onClick={() => scrollToSection("onboarding")}>
              Create Account
            </button>
          </div>
        </div>
      </header>

      <section className="hero-band" id="top">
        <div className="page-shell hero-grid-v2">
          <div className="hero-copy-v2">
            <p className="eyebrow hero-eyebrow">Pacifica-Native AI Trading Bot</p>
            <h1>The breakout trading bot built to look premium and trade with discipline.</h1>
            <p className="hero-subcopy">
              A hackathon-ready Pacifica product that scans for breakouts and liquidity sweeps,
              previews every order, and helps users move from safe testnet validation to real
              execution with confidence.
            </p>

            <div className="hero-cta-row">
              <button className="nav-cta hero-button" type="button" onClick={() => scrollToSection("onboarding")}>
                Create your account
              </button>
              <button className="nav-ghost hero-secondary" type="button" onClick={() => scrollToSection("live")}>
                See live cockpit
              </button>
            </div>

            <div className="hero-status-row">
              <StatusPill label={snapshot.bot.status} tone={snapshot.bot.status === "healthy" ? "positive" : "negative"} />
              <StatusPill label={operatorState.paused ? "Bot Paused" : "Bot Running"} tone={operatorState.paused ? "negative" : "positive"} />
              <StatusPill label={snapshot.account.source === "pacifica" ? "Pacifica Synced" : "Paper Mode"} tone="neutral" />
              <StatusPill label={onboardingStatus.cockpitUnlocked ? "Setup Complete" : `${onboardingStatus.completedCount}/${onboardingStatus.totalCount} Ready`} tone="neutral" />
            </div>

            <div className="hero-ticker">
              <span className="ticker-accent" />
              <p>
                {snapshot.events[0]?.message ??
                  "The bot is live, listening for the next quality setup."}
              </p>
            </div>
          </div>

          <div className="hero-stage">
            <div className="stage-orb orb-btc">BTC</div>
            <div className="stage-orb orb-eth">ETH</div>
            <div className="stage-orb orb-sol">SOL</div>

            <div className="desktop-shell">
              <div className="device-topbar">
                <span />
                <span />
                <span />
              </div>
              <div className="desktop-header">
                <div>
                  <strong>{snapshot.watchlist[0]?.symbol ?? "BTC"} / USD</strong>
                  <p>{snapshot.bot.network} network</p>
                </div>
                <span className="desktop-price">
                  {usd.format(snapshot.watchlist[0]?.lastPrice ?? 0)}
                </span>
              </div>
              <div className="chart-faux">
                {Array.from({ length: 24 }).map((_, index) => (
                  <span
                    key={index}
                    className={`bar ${index % 3 === 0 ? "down" : "up"}`}
                    style={{ height: `${35 + ((index * 11) % 90)}px` }}
                  />
                ))}
              </div>
              <div className="desktop-footer">
                <MiniKpi label="Signals" value={String(visibleSignals.length)} />
                <MiniKpi label="Open positions" value={String(snapshot.account.openPositions)} />
                <MiniKpi label="PnL" value={usd.format(snapshot.account.pnlUsd)} />
              </div>
            </div>

            <div className="phone-shell">
              <p className="phone-label">PACIFICA BOT</p>
              <strong>{operatorState.paused ? "Paused" : "Active"}</strong>
              <div className="phone-stat">
                <span>Equity</span>
                <strong>{usd.format(snapshot.account.equityUsd)}</strong>
              </div>
              <div className="phone-stat">
                <span>Best setup</span>
                <strong>{previewableSignal ? `${previewableSignal.symbol} ${previewableSignal.bias}` : "Watching..."}</strong>
              </div>
              <div className="phone-stat">
                <span>Readiness</span>
                <strong>{`${diagnostics.probes.length - degradedProbes.length}/${diagnostics.probes.length}`}</strong>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="onboarding-band" id="onboarding">
        <div className="page-shell">
          <div className="onboarding-shell">
            <div className="onboarding-intro">
              <SectionCopy
                eyebrow="Get started"
                title="Create your bot workspace before the cockpit unlocks."
                body="This is the missing product layer: users create their workspace, choose how they want to launch, confirm Pacifica access, and accept the guardrails before they start using the bot."
              />

              <div className="journey-meter">
                <div className="journey-meter-top">
                  <strong>{onboardingStatus.cockpitUnlocked ? "Cockpit unlocked" : `${onboardingStatus.completedCount} of ${onboardingStatus.totalCount} setup steps complete`}</strong>
                  <span>{onboarding.launchMode ? onboarding.launchMode.toUpperCase() : "CHOOSE MODE"}</span>
                </div>
                <div className="journey-track" aria-hidden="true">
                  <span style={{ width: `${(onboardingStatus.completedCount / onboardingStatus.totalCount) * 100}%` }} />
                </div>
                <p>
                  {onboardingStatus.cockpitUnlocked
                    ? "The user journey is complete enough to enter the live bot cockpit."
                    : "Finish the setup cards below so the bot feels like a real product with a real onboarding flow."}
                </p>
              </div>
            </div>

            <div className="onboarding-grid">
              <article className="onboarding-card">
                <div className="onboarding-card-head">
                  <span className="step-chip">01</span>
                  <StatusPill label={onboardingStatus.profileReady ? "Ready" : "Required"} tone={onboardingStatus.profileReady ? "positive" : "negative"} />
                </div>
                <h3>Create your workspace</h3>
                <p>Users create their account first, then the bot can personalize the next steps for them.</p>
                <div className="form-grid">
                  <label className="form-field">
                    <span>Full name</span>
                    <input
                      value={onboarding.fullName}
                      onChange={(event) =>
                        setOnboarding((current) => ({
                          ...current,
                          fullName: event.target.value
                        }))
                      }
                      placeholder="Ada Lovelace"
                    />
                  </label>
                  <label className="form-field">
                    <span>Email</span>
                    <input
                      value={onboarding.email}
                      onChange={(event) =>
                        setOnboarding((current) => ({
                          ...current,
                          email: event.target.value
                        }))
                      }
                      placeholder="ada@pacificabot.app"
                    />
                  </label>
                </div>
                <div className="segment-row">
                  {EXPERIENCE_OPTIONS.map(([value, label]) => (
                    <button
                      key={value}
                      className={`segment-button ${onboarding.experience === value ? "active" : ""}`}
                      type="button"
                      onClick={() =>
                        setOnboarding((current) => ({
                          ...current,
                          experience: value
                        }))
                      }
                    >
                      {label}
                    </button>
                  ))}
                </div>
                <button className="nav-cta onboarding-action" type="button" onClick={() => handleCreateProfile()}>
                  {onboardingStatus.profileReady ? "Workspace saved" : "Create workspace"}
                </button>
              </article>

              <article className="onboarding-card">
                <div className="onboarding-card-head">
                  <span className="step-chip">02</span>
                  <StatusPill
                    label={
                      onboardingStatus.accessRequired
                        ? onboardingStatus.accessReady
                          ? onboardingStatus.executionReady
                            ? "Ready"
                            : "Connected"
                          : "Required"
                        : onboardingStatus.accessReady
                          ? "Connected"
                          : "Optional"
                    }
                    tone={onboardingStatus.accessReady ? "positive" : onboardingStatus.accessRequired ? "negative" : "neutral"}
                  />
                </div>
                <h3>Connect Pacifica access</h3>
                <p>
                  Paper mode can be explored first, but testnet and live flows should only unlock once Pacifica access is confirmed. Agent keys are still needed later for signed execution.
                </p>
                <div className="wallet-detect-row">
                  {detectedWallets.length === 0 ? (
                    <p className="wallet-help">
                      No supported browser wallet detected yet. Install Phantom, Solflare, or Backpack, or paste a Pacifica account address manually.
                    </p>
                  ) : (
                    detectedWallets.map((wallet) => (
                      <button
                        key={wallet.id}
                        className={`segment-button ${onboarding.walletProvider === wallet.label ? "active" : ""}`}
                        type="button"
                        onClick={() => void handleConnectWallet(wallet)}
                        disabled={actionPending === `connect:${wallet.id}`}
                      >
                        {actionPending === `connect:${wallet.id}` ? `Connecting ${wallet.label}...` : `Connect ${wallet.label}`}
                      </button>
                    ))
                  )}
                </div>
                <div className="wallet-summary">
                  <SimpleRow label="Browser wallet" value={onboarding.walletProvider ?? "Not connected"} />
                  <SimpleRow label="Wallet address" value={shortenWalletAddress(onboarding.walletAddress)} />
                </div>
                <label className="form-field">
                  <span>Pacifica account address</span>
                  <input
                    value={onboarding.accountAddressInput}
                    onChange={(event) =>
                      setOnboarding((current) => ({
                        ...current,
                        accountAddressInput: event.target.value
                      }))
                    }
                    placeholder="Paste a Pacifica account address or connect a wallet"
                  />
                </label>
                <div className="sync-status-list">
                  <SyncStatusRow label="Wallet connected" ready={Boolean(onboarding.walletAddress)} detail={shortenWalletAddress(onboarding.walletAddress)} />
                  <SyncStatusRow
                    label="Pacifica account linked"
                    ready={Boolean(linkedAccountAddress)}
                    detail={
                      linkedAccountAddress
                        ? `${shortenWalletAddress(linkedAccountAddress)}${linkedAccountSource ? ` via ${linkedAccountSource}` : ""}`
                        : "No linked account yet"
                    }
                  />
                  <SyncStatusRow
                    label="Agent key ready"
                    ready={diagnostics.config.agentKeyConfigured}
                    detail={
                      diagnostics.config.agentKeyConfigured
                        ? "Signed execution can be prepared from the backend."
                        : "Needed for real signed orders, not for entering the cockpit."
                    }
                  />
                </div>
                <div className="side-actions">
                  <button
                    className="nav-ghost side-button secondary"
                    type="button"
                    onClick={() => void handleLinkAccount()}
                    disabled={actionPending === "link-account"}
                  >
                    {actionPending === "link-account" ? "Linking..." : "Link Pacifica account"}
                  </button>
                  <button
                    className="nav-cta side-button"
                    type="button"
                    onClick={() => void handleSync()}
                    disabled={!operatorState.canSyncAccount || actionPending === "sync"}
                  >
                    {actionPending === "sync" ? "Syncing..." : "Sync Pacifica"}
                  </button>
                  <button
                    className="small-link"
                    type="button"
                    onClick={() => void handleUnlinkAccount()}
                    disabled={!linkedAccountAddress || actionPending === "unlink-account"}
                  >
                    {actionPending === "unlink-account" ? "Unlinking..." : "Unlink account"}
                  </button>
                  <button
                    className="small-link"
                    type="button"
                    onClick={() => void handleDisconnectWallet()}
                    disabled={!onboarding.walletAddress || actionPending === "disconnect-wallet"}
                  >
                    {actionPending === "disconnect-wallet" ? "Disconnecting..." : "Disconnect wallet"}
                  </button>
                </div>
              </article>

              <article className="onboarding-card">
                <div className="onboarding-card-head">
                  <span className="step-chip">03</span>
                  <StatusPill label={onboardingStatus.launchModeReady ? "Ready" : "Required"} tone={onboardingStatus.launchModeReady ? "positive" : "negative"} />
                </div>
                <h3>Choose a launch mode</h3>
                <p>Guide the user into the right environment instead of throwing them into a trading screen immediately.</p>
                <div className="launch-options">
                  {LAUNCH_OPTIONS.map((option) => (
                    <button
                      key={option.id}
                      className={`launch-card ${onboarding.launchMode === option.id ? "active" : ""}`}
                      type="button"
                      onClick={() =>
                        setOnboarding((current) => ({
                          ...current,
                          launchMode: option.id
                        }))
                      }
                    >
                      <strong>{option.label}</strong>
                      <p>{option.description}</p>
                    </button>
                  ))}
                </div>
              </article>

              <article className="onboarding-card">
                <div className="onboarding-card-head">
                  <span className="step-chip">04</span>
                  <StatusPill label={onboardingStatus.safeguardsReady ? "Ready" : "Required"} tone={onboardingStatus.safeguardsReady ? "positive" : "negative"} />
                </div>
                <h3>Accept the guardrails</h3>
                <p>The bot should only unlock after the user has acknowledged risk controls and builder-program behavior.</p>
                <div className="consent-stack">
                  <button
                    className={`consent-row ${onboarding.riskAccepted ? "active" : ""}`}
                    type="button"
                    onClick={() =>
                      setOnboarding((current) => ({
                        ...current,
                        riskAccepted: !current.riskAccepted
                      }))
                    }
                  >
                    <span className={`check-mark ${onboarding.riskAccepted ? "ready" : "missing"}`} />
                    <div>
                      <strong>I understand this bot enforces risk rules.</strong>
                      <p>Size caps, stop losses, and daily loss limits are part of the launch process.</p>
                    </div>
                  </button>
                  <button
                    className={`consent-row ${onboarding.builderAcknowledged ? "active" : ""}`}
                    type="button"
                    onClick={() =>
                      setOnboarding((current) => ({
                        ...current,
                        builderAcknowledged: !current.builderAcknowledged
                      }))
                    }
                  >
                    <span className={`check-mark ${onboarding.builderAcknowledged ? "ready" : "missing"}`} />
                    <div>
                      <strong>I understand builder code and agent-key permissions.</strong>
                      <p>The Pacifica flow should be explicit before the user lets automation touch any account.</p>
                    </div>
                  </button>
                </div>
              </article>
            </div>

            <div className="onboarding-footer">
              <div>
                <span className="eyebrow">Launch status</span>
                <h3>{onboardingStatus.cockpitUnlocked ? "Your trading cockpit is unlocked." : "Finish setup before trading."}</h3>
                <p>
                  {onboardingStatus.cockpitUnlocked
                    ? "The user journey is now coherent: account first, setup second, live cockpit third."
                    : onboardingStatus.accessRequired && !onboardingStatus.accessReady
                      ? "Pick testnet or live only after Pacifica access, agent keys, and sync are confirmed."
                      : "Complete the remaining setup cards so the bot experience feels trustworthy and guided."}
                </p>
              </div>
              <div className="side-actions">
                <button className="nav-ghost side-button secondary" type="button" onClick={() => scrollToSection("features")}>
                  Review features
                </button>
                <button
                  className="nav-cta side-button"
                  type="button"
                  onClick={() => scrollToSection("live")}
                  disabled={!onboardingStatus.cockpitUnlocked}
                >
                  Enter live cockpit
                </button>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="feature-band" id="why">
        <div className="page-shell">
          <SectionCopy
            eyebrow="Why this feels premium"
            title="A premium crypto product feel, tailored to Pacifica."
            body="We kept the polished crypto-SaaS energy you liked, but made it about your Pacifica trading bot, your workflow, and your users."
            light
          />

          <div className="feature-board-v2">
            {FEATURE_COLUMNS.map((column) => (
              <article key={column.label} className="feature-column-v2">
                <span className="feature-column-label">{column.label}</span>
                <div className="feature-stack">
                  {column.items.map(([title, copy]) => (
                    <div key={title} className="feature-item">
                      <div className="feature-icon" />
                      <div>
                        <strong>{title}</strong>
                        <p>{copy}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="white-section" id="features">
        <div className="page-shell split-showcase-v2">
          <div>
            <SectionCopy
              eyebrow="Automate your trading"
              title="Trade Pacifica with structure, not emotion."
              body="The bot watches the market, filters the noise, applies risk rules, and gives the user a clear action path before anything gets sent."
            />

            <div className="benefit-stack">
              {BENEFIT_CARDS.map((card) => (
                <article key={card.title} className="benefit-card">
                  <div className="benefit-icon" />
                  <div>
                    <strong>{card.title}</strong>
                    <p>{card.copy}</p>
                  </div>
                </article>
              ))}
            </div>
          </div>

          <div className="showcase-terminal">
            <div className="terminal-card floating-card">
              <span className="label">Signal confidence</span>
              <strong>{previewableSignal ? `${Math.round(previewableSignal.confidence * 100)}%` : "Waiting"}</strong>
              <p>{previewableSignal?.reason ?? "No high-conviction setup at the moment."}</p>
            </div>

            <div className="terminal-main">
              <div className="terminal-top">
                <strong>Live Bot Cockpit</strong>
                <span>{snapshot.bot.liveTradingEnabled ? "Live ready" : "Safe mode"}</span>
              </div>
              <div className="terminal-grid">
                <MiniKpi label="Watchlist" value={String(snapshot.watchlist.length)} />
                <MiniKpi label="Signals" value={String(visibleSignals.length)} />
                <MiniKpi label="Orders" value={String(snapshot.account.openOrders)} />
                <MiniKpi label="Positions" value={String(snapshot.account.openPositions)} />
              </div>
              <div className="terminal-wave">
                {Array.from({ length: 18 }).map((_, index) => (
                  <span key={index} style={{ height: `${20 + ((index * 13) % 70)}px` }} />
                ))}
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="soft-section-v2">
        <div className="page-shell">
          <SectionCopy
            eyebrow="Built for a real product"
            title="A landing page for users, not just developers."
            body="The site now reads like a real trading product: strong hero, clear value proposition, guided setup, polished visuals, and a live cockpit underneath."
            center
          />

          <div className="trust-grid">
            <TrustCard title="Start on testnet" copy="Validate the full flow without risking real money while the judges can still see real execution logic." />
            <TrustCard title="Explain every trade" copy="Show why the bot wants to trade, what it plans to do, and how risk controls shape the final order." />
            <TrustCard title="Stay Pacifica-native" copy="Builder-code support, Pacifica account sync, and agent-key signing keep the product aligned with the platform." />
          </div>
        </div>
      </section>

      <section className="cockpit-band" id="live">
        <div className="page-shell cockpit-grid-v2">
          <div className="cockpit-main-v2">
            <SectionCopy
              eyebrow="Live bot"
              title={onboardingStatus.cockpitUnlocked ? "See the bot think before it trades." : "The cockpit unlocks after account setup."}
              body={
                onboardingStatus.cockpitUnlocked
                  ? "This is where the marketing page becomes a real product. You can watch opportunities, preview orders, sync the account, and monitor activity."
                  : "The bot is visible below, but the real user flow should push people through account creation, setup, and safeguards before they start interacting with it."
              }
            />

            {actionMessage ? (
              <div className={`message-banner ${actionTone}`}>
                {actionMessage}
              </div>
            ) : null}

            {!onboardingStatus.cockpitUnlocked ? (
              <article className="launch-lock-card">
                <div>
                  <span className="eyebrow">Locked until setup is complete</span>
                  <h3>Finish onboarding to unlock previews and bot controls.</h3>
                  <p>
                    Users should not land directly in a trading surface. Create the workspace first, choose the launch mode, and confirm the Pacifica permissions.
                  </p>
                </div>
                <button className="nav-cta side-button" type="button" onClick={() => scrollToSection("onboarding")}>
                  Finish onboarding
                </button>
              </article>
            ) : null}

            <div className={onboardingStatus.cockpitUnlocked ? "cockpit-preview-shell" : "cockpit-preview-shell locked"}>
              <LiveMarketWorkspace
                marketCharts={snapshot.marketCharts}
                tradeActivity={snapshot.tradeActivity}
                selectedSymbol={selectedChartSymbol}
                onSelectSymbol={setSelectedChartSymbol}
              />

              <article className="live-panel">
                <div className="panel-topline">
                  <div>
                    <strong>Trade opportunities</strong>
                    <p>High-priority setups the bot wants you to review now.</p>
                  </div>
                  <input
                    className="search-input"
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="Search signals or recent activity..."
                  />
                </div>

                <div className="opportunity-stack">
                  {filteredSignals.length === 0 ? (
                    <EmptyState message="No active opportunities right now. The bot is still watching the market." />
                  ) : (
                    filteredSignals.map((signal) => (
                      <OpportunityCard
                        key={signal.id}
                        signal={signal}
                        previewing={actionPending === `preview:${signal.id}`}
                        onPreview={
                          !onboardingStatus.cockpitUnlocked || signal.status === "blocked"
                            ? undefined
                            : () => void handlePreview(signal)
                        }
                      />
                    ))
                  )}
                </div>
              </article>

              {preview ? (
                <article className="live-panel">
                  <div className="panel-topline">
                    <div>
                      <strong>Order preview</strong>
                      <p>{preview.message}</p>
                    </div>
                    <span className={`status-tag ${preview.ok ? "positive" : "negative"}`}>
                      {preview.marketSpecApplied ? "market spec loaded" : "fallback formatting"}
                    </span>
                  </div>
                  <pre className="payload-preview">{JSON.stringify(preview.payload, null, 2)}</pre>
                </article>
              ) : null}

              <article className="live-panel">
                <div className="panel-topline">
                  <div>
                    <strong>System notes</strong>
                    <p>Operational events, diagnostics, and recent strategy decisions.</p>
                  </div>
                  <button className="small-link" type="button" onClick={() => setAutoRefresh((value) => !value)}>
                    Auto refresh {autoRefresh ? "on" : "off"}
                  </button>
                </div>

                <div className="event-stack">
                  {filteredEvents.slice(0, 6).map((event) => (
                    <TimelineRow key={event.id} level={event.level} message={event.message} timestamp={event.createdAt} />
                  ))}
                </div>
              </article>
            </div>
          </div>

          <aside className="cockpit-side-v2">
            <article className="side-card">
              <span className="eyebrow">Next best step</span>
              <h3>{nextAction.title}</h3>
              <p>{nextAction.description}</p>
              <div className="side-actions">
                <button
                  className="nav-cta side-button"
                  type="button"
                  onClick={() => onboardingStatus.cockpitUnlocked ? void handlePauseResume() : scrollToSection("onboarding")}
                  disabled={onboardingStatus.cockpitUnlocked ? actionPending === "pause" || actionPending === "resume" : false}
                >
                  {onboardingStatus.cockpitUnlocked ? (operatorState.paused ? "Resume Bot" : "Pause Bot") : "Finish onboarding"}
                </button>
                <button
                  className="nav-ghost side-button secondary"
                  type="button"
                  onClick={() => void handleSync()}
                  disabled={!operatorState.canSyncAccount || actionPending === "sync"}
                >
                  {actionPending === "sync" ? "Syncing..." : "Sync Account"}
                </button>
                {snapshot.bot.mode === "testnet" ? (
                  <button
                    className="nav-ghost side-button secondary"
                    type="button"
                    onClick={() => void handleSmokeTestOrder()}
                    disabled={!operatorState.canSubmitOrders || actionPending === "smoke-test"}
                  >
                    {actionPending === "smoke-test"
                      ? `Sending ${selectedChartSymbol} test order...`
                      : `Send ${selectedChartSymbol} test order`}
                  </button>
                ) : null}
              </div>
            </article>

            <article className="side-card">
              <span className="eyebrow">Start here</span>
              <h3>Setup checklist</h3>
              <div className="checklist-stack">
                {setupSteps.map((step) => (
                  <ChecklistItem key={step.label} step={step} />
                ))}
              </div>
            </article>

            <article className="side-card">
              <span className="eyebrow">Account summary</span>
              <h3>{snapshot.account.source === "pacifica" ? "Pacifica account" : "Paper account"}</h3>
              <div className="simple-row-stack">
                <SimpleRow label="Equity" value={usd.format(snapshot.account.equityUsd)} />
                <SimpleRow label="Available" value={usd.format(snapshot.account.availableMarginUsd)} />
                <SimpleRow label="Open positions" value={String(snapshot.account.openPositions)} />
                <SimpleRow label="Open orders" value={String(snapshot.account.openOrders)} />
                <SimpleRow label="PnL" value={usd.format(snapshot.account.pnlUsd)} />
              </div>
              {canManagePaperBalance ? (
                <div className="paper-account-shell">
                  <div className="paper-account-header">
                    <strong>Paper testing balance</strong>
                    <p>
                      These controls affect the bot&apos;s simulated balance only, so you can keep testing after margin gets tight.
                    </p>
                  </div>
                  <div className="paper-account-grid">
                    <SimpleRow label="Starting balance" value={usd.format(snapshot.paperAccount.startingEquityUsd)} />
                    <SimpleRow label="Paper equity" value={usd.format(snapshot.paperAccount.equityUsd)} />
                    <SimpleRow label="Paper available" value={usd.format(snapshot.paperAccount.availableMarginUsd)} />
                    <SimpleRow label="Paper PnL" value={usd.format(snapshot.paperAccount.realizedPnlUsd + snapshot.paperAccount.unrealizedPnlUsd)} />
                  </div>
                  <div className="paper-account-form">
                    <input
                      className="paper-top-up-input"
                      type="number"
                      inputMode="decimal"
                      min="1"
                      step="100"
                      value={paperTopUpInput}
                      onChange={(event) => setPaperTopUpInput(event.target.value)}
                      placeholder="Top-up amount in USD"
                    />
                    <div className="side-actions paper-account-actions">
                      <button
                        className="nav-cta side-button"
                        type="button"
                        onClick={() => void handleTopUpPaperBalance()}
                        disabled={actionPending === "paper-top-up"}
                      >
                        {actionPending === "paper-top-up" ? "Adding..." : "Top up balance"}
                      </button>
                      <button
                        className="nav-ghost side-button secondary"
                        type="button"
                        onClick={() => void handleResetPaperBalance()}
                        disabled={actionPending === "paper-reset"}
                      >
                        {actionPending === "paper-reset" ? "Resetting..." : "Reset balance"}
                      </button>
                    </div>
                  </div>
                </div>
              ) : null}
            </article>

            <article className="side-card">
              <span className="eyebrow">{hasHydrated ? performanceEyebrow : "Performance"}</span>
              <h3>{hasHydrated ? performanceHeading : "Loading performance snapshot"}</h3>
              <p>
                {hasHydrated
                  ? performanceDescription
                  : "Refreshing the latest execution performance so this panel stays aligned after hydration."}
              </p>
              {hasHydrated ? (
                <>
                  <div className="paper-performance-grid">
                    <PaperModeCard
                      title={
                        showingLivePerformance
                          ? `${formatExecutionMode(activePerformanceSummary.executionMode)} execution`
                          : `${formatExecutionMode(snapshot.paperPerformance.currentMode)} book`
                      }
                      summary={activePerformanceSummary}
                      emphasized
                    />
                    {showingLivePerformance ? null : (
                      <PaperModeCard
                        title={`${formatExecutionMode(snapshot.paperPerformance.comparisonMode)} book`}
                        summary={snapshot.paperPerformance.comparisonModeSummary}
                      />
                    )}
                  </div>
                  {showingLivePerformance ? null : (
                    <div className="paper-performance-meta">
                      <SimpleRow
                        label="Paired closed signals"
                        value={String(snapshot.paperPerformance.pairedClosedSignals)}
                      />
                    </div>
                  )}
                  <div className="paper-trade-ledger">
                    {activePerformanceTrades.length === 0 ? (
                      <EmptyState
                        message={
                          showingLivePerformance
                            ? `Closed ${snapshot.bot.mode === "testnet" ? "testnet" : "live"} trades will appear here once the first full cycle finishes.`
                            : "Closed paper trades will appear here once the first full trade cycle finishes."
                        }
                      />
                    ) : (
                      activePerformanceTrades.slice(0, 6).map((trade) => (
                        <ClosedTradeRow key={trade.id} trade={trade} />
                      ))
                    )}
                  </div>
                </>
              ) : (
                <div className="paper-trade-ledger">
                  <EmptyState message="Performance metrics will appear here once the client snapshot is mounted." />
                </div>
              )}
            </article>

            <article className="side-card">
              <span className="eyebrow">ML quality</span>
              <h3>{snapshot.mlModel.ready ? "Validated model is active" : "Rules are still in control"}</h3>
              <div className="simple-row-stack">
                <SimpleRow label="Training source" value={snapshot.mlModel.trainingSource ?? "Not available"} />
                <SimpleRow label="Training samples" value={String(snapshot.mlModel.trainingSamples)} />
                <SimpleRow label="Holdout precision" value={formatRate(snapshot.mlModel.decisionPrecision)} />
                <SimpleRow label="Approved decisions" value={String(snapshot.mlModel.decisionSamples)} />
              </div>
              <p className="ml-summary-copy">{snapshot.mlModel.summary}</p>
            </article>

            <article className="side-card">
              <span className="eyebrow">Needs attention</span>
              <h3>Things to fix next</h3>
              <div className="attention-stack">
                {attentionItems.length === 0 ? (
                  <EmptyState message="Nothing urgent right now. The bot looks healthy from the current checks." />
                ) : (
                  attentionItems.map((item) => (
                    <AttentionCard key={item.title} title={item.title} message={item.message} />
                  ))
                )}
              </div>
            </article>
          </aside>
        </div>
      </section>

      <section className="proof-strip">
        <div className="page-shell proof-grid">
          <ProofMetric label="Setup steps ready" value={`${onboardingStatus.completedCount}/${onboardingStatus.totalCount}`} />
          <ProofMetric label="Readiness checks healthy" value={`${diagnostics.probes.length - degradedProbes.length}/${diagnostics.probes.length}`} />
          <ProofMetric label="Open positions" value={String(snapshot.account.openPositions)} />
          <ProofMetric label="Mode" value={snapshot.bot.mode.toUpperCase()} />
        </div>
      </section>

      <section className="page-shell advanced-shell" id="advanced">
        <details className="advanced-details">
          <summary>Advanced details</summary>
          <p className="advanced-copy">
            Technical diagnostics, network settings, and lower-level bot state for demos and debugging.
          </p>

          <div className="advanced-grid-v2">
            <article className="advanced-card">
              <h3>Diagnostics</h3>
              <div className="advanced-stack">
                {diagnostics.probes.map((probe) => (
                  <div key={probe.id} className="advanced-row">
                    <div>
                      <strong>{probe.label}</strong>
                      <p>{probe.message}</p>
                    </div>
                    <span className={`status-tag ${probe.status === "healthy" ? "positive" : probe.status === "degraded" ? "negative" : "neutral"}`}>
                      {probe.status}
                    </span>
                  </div>
                ))}
              </div>
            </article>

            <article className="advanced-card">
              <h3>Network</h3>
              <div className="advanced-stack">
                <SimpleRow label="REST URL" value={diagnostics.config.restUrl} />
                <SimpleRow label="WebSocket URL" value={diagnostics.config.websocketUrl} />
                <SimpleRow label="Feed mode" value={diagnostics.config.useSimulatedFeed ? "Simulated" : "Live Pacifica"} />
                <SimpleRow label="Live trading" value={diagnostics.config.liveTradingEnabled ? "Enabled" : "Disabled"} />
              </div>
            </article>

            <article className="advanced-card">
              <h3>Mirrored Pacifica positions</h3>
              <div className="advanced-stack">
                {snapshot.remotePositions.length === 0 ? (
                  <EmptyState message="No mirrored Pacifica positions yet." />
                ) : (
                  snapshot.remotePositions.map((position) => (
                    <SimpleRow
                      key={`${position.symbol}-${position.side}`}
                      label={`${position.symbol} ${position.side}`}
                      value={`${position.size} @ ${usd.format(position.entryPrice)}`}
                    />
                  ))
                )}
              </div>
            </article>

            <article className="advanced-card">
              <h3>Open orders</h3>
              <div className="advanced-stack">
                {snapshot.openOrders.length === 0 ? (
                  <EmptyState message="No open Pacifica orders right now." />
                ) : (
                  snapshot.openOrders.map((order) => (
                    <SimpleRow
                      key={order.orderId}
                      label={`${order.symbol} ${order.side}`}
                      value={`${order.remainingAmount} @ ${usd.format(order.price)}`}
                    />
                  ))
                )}
              </div>
            </article>
          </div>
        </details>
      </section>
    </main>
  );
}

function SectionCopy({
  eyebrow,
  title,
  body,
  center = false,
  light = false
}: {
  eyebrow: string;
  title: string;
  body: string;
  center?: boolean;
  light?: boolean;
}) {
  return (
    <header className={`section-copy ${center ? "center" : ""} ${light ? "light" : ""}`}>
      <p className="eyebrow">{eyebrow}</p>
      <h2>{title}</h2>
      <p>{body}</p>
    </header>
  );
}

function StatusPill({ label, tone }: { label: string; tone: "positive" | "negative" | "neutral" }) {
  return <span className={`status-tag ${tone}`}>{label}</span>;
}

function MiniKpi({ label, value }: { label: string; value: string }) {
  return (
    <div className="mini-kpi">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function TrustCard({ title, copy }: { title: string; copy: string }) {
  return (
    <article className="trust-card">
      <div className="benefit-icon" />
      <strong>{title}</strong>
      <p>{copy}</p>
    </article>
  );
}

function SyncStatusRow({ label, ready, detail }: { label: string; ready: boolean; detail?: string }) {
  return (
    <div className="sync-status-row">
      <div>
        <strong>{label}</strong>
        {detail ? <p>{detail}</p> : null}
      </div>
      <span className={`status-tag ${ready ? "positive" : "neutral"}`}>
        {ready ? "ready" : "pending"}
      </span>
    </div>
  );
}

function OpportunityCard({
  signal,
  previewing,
  onPreview
}: {
  signal: StrategySignal;
  previewing: boolean;
  onPreview?: () => void;
}) {
  return (
    <article className="opportunity-card">
      <div className="panel-topline">
        <div>
          <strong>{signal.symbol} {signal.bias.toUpperCase()}</strong>
          <p>{signal.setup.replace("_", " ")} with {(signal.confidence * 100).toFixed(0)}% confidence</p>
        </div>
        <span className={`status-tag ${signal.status === "executed" ? "positive" : signal.status === "blocked" ? "negative" : "neutral"}`}>
          {signal.status}
        </span>
      </div>
      <div className="opportunity-grid">
        <SimpleRow label="Entry" value={usd.format(signal.entryPrice)} />
        <SimpleRow label="Stop" value={usd.format(signal.stopLoss)} />
        <SimpleRow label="Target" value={usd.format(signal.takeProfit)} />
        <SimpleRow label="Notional" value={usd.format(signal.notionalUsd)} />
      </div>
      <p className="card-copy">{signal.reason}</p>
      {onPreview ? (
        <button className="small-link" type="button" onClick={onPreview} disabled={previewing}>
          {previewing ? "Preparing preview..." : "Preview order"}
        </button>
      ) : null}
    </article>
  );
}

function TimelineRow({
  level,
  message,
  timestamp
}: {
  level: string;
  message: string;
  timestamp: string;
}) {
  return (
    <div className="timeline-row">
      <span className={`timeline-dot ${level}`} />
      <div>
        <strong>{message}</strong>
        <p>{formatTimestamp(timestamp)}</p>
      </div>
    </div>
  );
}

function ChecklistItem({
  step
}: {
  step: { label: string; description: string; ready: boolean; optional?: boolean };
}) {
  return (
    <div className="checklist-item">
      <span className={`check-mark ${step.ready ? "ready" : step.optional ? "optional" : "missing"}`} />
      <div>
        <strong>{step.label}</strong>
        <p>{step.description}</p>
      </div>
    </div>
  );
}

function AttentionCard({ title, message }: { title: string; message: string }) {
  return (
    <div className="attention-card">
      <strong>{title}</strong>
      <p>{message}</p>
    </div>
  );
}

function ProofMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="proof-metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function SimpleRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="simple-row">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function PaperModeCard({
  title,
  summary,
  emphasized = false
}: {
  title: string;
  summary: PaperPerformanceSummary | LivePerformanceSummary;
  emphasized?: boolean;
}) {
  return (
    <div className={`paper-mode-card ${emphasized ? "emphasized" : ""}`}>
      <div className="paper-mode-card-top">
        <strong>{title}</strong>
        <span className={`status-tag ${summary.netPnlUsd >= 0 ? "positive" : "negative"}`}>
          {summary.closedTrades} closed
        </span>
      </div>
      <div className="simple-row-stack">
        <SimpleRow label="Net PnL" value={formatSignedUsd(summary.netPnlUsd)} />
        <SimpleRow label="Win rate" value={formatRate(summary.winRate)} />
        <SimpleRow label="Avg R" value={formatRMultiple(summary.averageRMultiple)} />
        <SimpleRow
          label="TP / SL"
          value={`${summary.takeProfitHits}/${summary.stopLossHits}`}
        />
        <SimpleRow
          label="Max drawdown"
          value={`${usd.format(summary.maxDrawdownUsd)} (${formatRate(summary.maxDrawdownPct)})`}
        />
      </div>
    </div>
  );
}

function ClosedTradeRow({ trade }: { trade: PaperClosedTrade }) {
  return (
    <div className="closed-trade-row">
      <div className="closed-trade-top">
        <strong>
          {trade.symbol} {trade.side.toUpperCase()} {trade.outcome.replaceAll("_", " ")}
        </strong>
        <span className={`status-tag ${trade.pnlUsd >= 0 ? "positive" : "negative"}`}>
          {formatSignedUsd(trade.pnlUsd)}
        </span>
      </div>
      <div className="closed-trade-meta">
        <span>{formatExecutionMode(trade.executionMode)}</span>
        <span>{trade.setup ?? "Setup n/a"}</span>
        <span>{formatRMultiple(trade.rMultiple)}</span>
        <span>{formatTimestamp(trade.closedAt)}</span>
      </div>
    </div>
  );
}

function EmptyState({ message }: { message: string }) {
  return <p className="empty-state">{message}</p>;
}

function isLivePerformanceMode(snapshot: DashboardSnapshot) {
  return snapshot.bot.mode !== "paper" && snapshot.livePerformance !== null;
}

function getPrimaryPerformanceSummary(snapshot: DashboardSnapshot) {
  return isLivePerformanceMode(snapshot)
    ? snapshot.livePerformance!.summary
    : snapshot.paperPerformance.currentModeSummary;
}

function getPrimaryPerformanceClosedTrades(snapshot: DashboardSnapshot) {
  return isLivePerformanceMode(snapshot)
    ? snapshot.livePerformance!.recentClosedTrades
    : snapshot.paperPerformance.recentClosedTrades;
}

function buildSetupSteps(snapshot: DashboardSnapshot, diagnostics: DiagnosticsResponse, usingFallback: boolean) {
  return [
    {
      label: "Backend connected",
      description: usingFallback ? "Restart the backend so the page uses live bot data." : "The frontend is connected to the live bot service.",
      ready: !usingFallback
    },
    {
      label: "Pacifica account linked",
      description: diagnostics.config.accountConfigured
        ? `Your Pacifica account is linked${diagnostics.config.accountConfigurationSource ? ` via ${diagnostics.config.accountConfigurationSource}` : ""}.`
        : "Connect a wallet or paste a Pacifica account address to unlock real account sync.",
      ready: diagnostics.config.accountConfigured
    },
    {
      label: "Agent key ready",
      description: diagnostics.config.agentKeyConfigured ? "The bot can prepare signed trading requests." : "Add an API agent key before live order flow.",
      ready: diagnostics.config.agentKeyConfigured
    },
    {
      label: "Bot scanning live markets",
      description: snapshot.operator.paused ? "Resume the bot so it can start looking for new setups." : "The bot is actively scanning for trading opportunities.",
      ready: !snapshot.operator.paused
    }
  ];
}

function buildAttentionItems(
  snapshot: DashboardSnapshot,
  diagnostics: DiagnosticsResponse,
  usingFallback: boolean,
  onboardingStatus: OnboardingStatus
) {
  const items: Array<{ title: string; message: string }> = [];
  const showingLivePerformance = isLivePerformanceMode(snapshot);
  const activePerformanceSummary = getPrimaryPerformanceSummary(snapshot);

  if (usingFallback) {
    items.push({
      title: "Fallback mode active",
      message: "You are seeing demo data until the backend connection is restored."
    });
  }

  if (!diagnostics.config.accountConfigured) {
    items.push({
      title: "No Pacifica account connected",
      message: "Connect a wallet or link a Pacifica account address so the product can show real balances, positions, and orders."
    });
  }

  if (!diagnostics.config.agentKeyConfigured) {
    items.push({
      title: "Agent key missing",
      message: "An API agent key is needed before the bot can move from preview mode toward signed live execution."
    });
  }

  if (!onboardingStatus.profileReady) {
    items.push({
      title: "Workspace profile incomplete",
      message: "Users should create their account profile before they are dropped into the bot experience."
    });
  }

  if (!onboardingStatus.launchModeReady) {
    items.push({
      title: "No launch mode selected",
      message: "Let the user choose paper, testnet, or a live-later path instead of guessing for them."
    });
  }

  if (onboardingStatus.accessRequired && !onboardingStatus.accessReady) {
    items.push({
      title: "Pacifica access not ready",
      message: "Testnet and live paths should stay locked until Pacifica sync and agent-key setup are complete."
    });
  }

  if (showingLivePerformance) {
    if (activePerformanceSummary.closedTrades === 0) {
      items.push({
        title: "No closed testnet trades yet",
        message: "The bot is live on testnet, but the performance card needs at least one completed trade cycle before the win rate and PnL ledger become meaningful."
      });
    }

    if (activePerformanceSummary.maxDrawdownPct > 0.05) {
      items.push({
        title: "Testnet drawdown is elevated",
        message: "Max drawdown has pushed past 5%, so keep the bot under observation before promoting this setup any further."
      });
    }
  } else {
    if (snapshot.paperPerformance.currentModeSummary.closedTrades < 12) {
      items.push({
        title: "Paper sample is still small",
        message: "Keep the bot running until the paper ledger has at least a dozen closed trades before you judge testnet readiness."
      });
    }

    if (
      snapshot.paperPerformance.currentModeSummary.netPnlUsd <
      snapshot.paperPerformance.comparisonModeSummary.netPnlUsd
    ) {
      items.push({
        title: "Current mode trails the comparison book",
        message: "The opposite execution policy is outperforming the live paper book, so compare more samples before promoting this setup."
      });
    }

    if (snapshot.paperPerformance.currentModeSummary.maxDrawdownPct > 0.05) {
      items.push({
        title: "Paper drawdown is still elevated",
        message: "Max drawdown has pushed past 5%, which is too aggressive for a clean move into testnet."
      });
    }
  }

  diagnostics.probes
    .filter((probe) => probe.status === "degraded")
    .slice(0, 2)
    .forEach((probe) => {
      items.push({
        title: probe.label,
        message: probe.message
      });
    });

  return items;
}

function getNextAction(
  snapshot: DashboardSnapshot,
  diagnostics: DiagnosticsResponse,
  usingFallback: boolean,
  previewableSignal: StrategySignal | null,
  onboardingStatus: OnboardingStatus
) {
  const showingLivePerformance = isLivePerformanceMode(snapshot);
  if (usingFallback) {
    return {
      title: "Reconnect the backend",
      description: "Start the backend service first so this landing page reflects the real bot instead of fallback data."
    };
  }

  if (!onboardingStatus.profileReady) {
    return {
      title: "Create the user account",
      description: "The product flow should begin with account creation so users know the bot is being set up for them."
    };
  }

  if (!onboardingStatus.launchModeReady) {
    return {
      title: "Choose how the user should launch",
      description: "Paper mode is the safest first experience, while testnet and live should be explicitly selected."
    };
  }

  if (!onboardingStatus.safeguardsReady) {
    return {
      title: "Accept the guardrails",
      description: "Risk rules and builder-program permissions should be acknowledged before the cockpit unlocks."
    };
  }

  if (onboardingStatus.accessRequired && !onboardingStatus.accessReady) {
    return {
      title: "Finish Pacifica access setup",
      description: "Testnet and live workflows should stay gated until a Pacifica account is linked."
    };
  }

  if (!onboardingStatus.executionReady) {
    return {
      title: "Add an agent key when you are ready",
      description: "The cockpit is available, but signed order submission should stay disabled until the backend has an API agent key."
    };
  }

  if (!diagnostics.config.accountConfigured) {
    return {
      title: "Link your Pacifica account",
      description: "That unlocks account sync, real balances, mirrored positions, and a more convincing product demo."
    };
  }

  if (snapshot.account.source !== "pacifica") {
    return {
      title: "Run your first account sync",
      description: "Pull your Pacifica account into the app so the live cockpit shows real data."
    };
  }

  if (snapshot.operator.paused) {
    return {
      title: "Resume the bot",
      description: "The bot is paused, so it will not search for fresh setups until you resume it."
    };
  }

  if (!showingLivePerformance && snapshot.paperPerformance.currentModeSummary.closedTrades < 12) {
    return {
      title: "Collect more paper trade samples",
      description: "Let the bot close more paper trades so the win rate, average R, and drawdown numbers become trustworthy before testnet."
    };
  }

  if (showingLivePerformance && snapshot.livePerformance?.summary.closedTrades === 0) {
    return {
      title: "Let the testnet trade finish a full cycle",
      description: "The bot is already trading on testnet. Once a position closes, the performance card will start showing real closed-trade stats."
    };
  }

  if (previewableSignal) {
    return {
      title: `Preview ${previewableSignal.symbol} now`,
      description: "The bot already found a trade candidate. Review the order preview before you move toward execution."
    };
  }

  return {
    title: "Let the bot keep watching",
    description: "The system looks healthy. Keep it running and wait for the next high-quality setup."
  };
}

function matchesQuery(query: string, ...parts: Array<string | number | null | undefined>) {
  if (!query) {
    return true;
  }
  return parts.some((part) => String(part ?? "").toLowerCase().includes(query));
}

function formatTimestamp(value: string) {
  return value.replace("T", " ").slice(0, 19);
}

function formatRate(value: number | null) {
  if (value == null) {
    return "Not available";
  }
  return `${(value * 100).toFixed(1)}%`;
}

function formatRMultiple(value: number | null) {
  if (value == null) {
    return "R n/a";
  }
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}R`;
}

function formatSignedUsd(value: number) {
  return `${value >= 0 ? "+" : "-"}${usd.format(Math.abs(value))}`;
}

function formatExecutionMode(value: "normal" | "contrarian") {
  return value === "contrarian" ? "Contrarian" : "Normal";
}

function scrollToSection(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function buildOnboardingStatus(
  onboarding: OnboardingState,
  diagnostics: DiagnosticsResponse,
  snapshot: DashboardSnapshot,
  usingFallback: boolean
): OnboardingStatus {
  const profileReady = onboarding.profileCreated && isValidProfile(onboarding);
  const experienceReady = onboarding.experience !== null;
  const launchModeReady = onboarding.launchMode !== null;
  const accessRequired = onboarding.launchMode === "testnet" || onboarding.launchMode === "live";
  const pacificaConnected = diagnostics.config.accountConfigured || snapshot.account.source === "pacifica";
  const accessReady = pacificaConnected && !usingFallback;
  const executionReady = diagnostics.config.agentKeyConfigured;
  const safeguardsReady = onboarding.riskAccepted && onboarding.builderAcknowledged;
  const accessStepReady = launchModeReady ? (accessRequired ? accessReady : true) : false;
  const totalCount = 4;
  const completedCount = [
    profileReady,
    launchModeReady,
    safeguardsReady,
    accessStepReady
  ].filter(Boolean).length;

  return {
    profileReady,
    experienceReady,
    launchModeReady,
    accessRequired,
    accessReady,
    executionReady,
    safeguardsReady,
    cockpitUnlocked: profileReady && experienceReady && launchModeReady && safeguardsReady && (accessRequired ? accessReady : true),
    completedCount,
    totalCount
  };
}

function isValidProfile(onboarding: OnboardingState) {
  return onboarding.fullName.trim().length >= 2 && /\S+@\S+\.\S+/.test(onboarding.email) && onboarding.experience !== null;
}

function looksLikeWalletAddress(value: string) {
  const trimmed = value.trim();
  if (trimmed.length < 32 || trimmed.length > 48) {
    return false;
  }
  return /^[1-9A-HJ-NP-Za-km-z]+$/.test(trimmed);
}

function sameWallets(current: DetectedWallet[], next: DetectedWallet[]) {
  if (current.length !== next.length) {
    return false;
  }

  return current.every((wallet, index) => {
    const candidate = next[index];
    return candidate !== undefined && wallet.id === candidate.id && wallet.label === candidate.label;
  });
}
