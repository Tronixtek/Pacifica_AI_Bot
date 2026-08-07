from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


SERVICE_ROOT = Path(__file__).resolve().parents[1]


def env_alias(field_name: str) -> str:
    """camelCase field name -> UPPER_SNAKE environment variable name.

    Deliberately not pydantic's `to_snake`: that helper treats a digit as a
    word boundary, so `mt5Login` becomes `mt_5_login` and never matches the
    documented `MT5_LOGIN` variable. Every MT5_* setting silently fell back to
    its default because of it, which left the account sync disabled and the
    login/server credentials unused. Splitting only on capitals keeps digits
    attached to the token they belong to.
    """
    return re.sub(r"(?<!^)(?=[A-Z])", "_", field_name).lower()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        alias_generator=env_alias,
        populate_by_name=True,
        env_file=SERVICE_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    appEnv: str = "development"
    serviceName: str = "vtfx-mt5-trader"
    frontendOrigin: str = "http://127.0.0.1:3000"
    logLevel: str = "INFO"
    logFormat: Literal["json", "plain"] = "json"
    auditLogPath: Path = Path("logs/audit.jsonl")

    botMode: Literal["paper", "demo", "live"] = "paper"
    mt5Login: int | None = None
    mt5Password: str | None = None
    mt5Server: str | None = None
    mt5TerminalPath: str | None = None
    mt5MagicNumber: int = 990_211
    mt5DeviationPoints: int = 20
    mt5ConnectTimeoutMs: int = 10_000

    symbols: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["EURUSD", "XAUUSD", "BTCUSD"]
    )
    # Brokers append their own tag to instrument names (Exness uses "m",
    # others ".raw"/"_i"). Leave blank to auto-detect from the majors the
    # terminal exposes; set it explicitly to override detection.
    symbolSuffix: str | None = None
    useSimulatedFeed: bool = True
    enableLiveTrading: bool = False
    pollIntervalSec: float = 2.0
    accountSyncIntervalSec: float = 10.0
    marketDataStaleAfterSec: float = 15.0

    startingEquityUsd: float = 10_000.0
    maxRiskPerTradePct: float = 0.75
    minSignalConfidence: float = 0.72
    maxDailyLossPct: float = 3.0
    enforceDailyLossLimit: bool = False
    maxOpenPositions: int = 5
    defaultLeverage: float = 3.0
    contrarianExecutionEnabled: bool = True
    # Where to place the target once a signal has been flipped, as a multiple
    # of the flipped risk. Using the original stop (the previous behaviour)
    # puts the target roughly 0.43R away, which cuts winners off well before
    # the move is done: spread costs a fixed 0.09R regardless of target, so a
    # near target leaves nothing after costs. Measured across M5 and M15,
    # expectancy improves monotonically out to ~2R.
    # Set to 0 to restore the original "target = old stop" behaviour.
    contrarianTargetRiskMultiple: float = 2.0
    # Trailing exit. When enabled the fixed take-profit is dropped and the
    # trade exits on the trailed stop instead, so a move that keeps running
    # keeps paying. Measured on Exness M15 (breakout only): a fixed 0.25R
    # target gives 81.1% wins at -0.018R, while arming at 0.25R and trailing
    # 0.5 ATR gives 80.3% wins at +0.029R, stable across both halves.
    trailingStopEnabled: bool = True
    trailActivateR: float = 0.25
    trailAtrMultiple: float = 0.5
    signalCooldownSeconds: int = 45
    # The strategy evaluates closed bars on this timeframe. Sampling the tick
    # stream instead makes a "20-bar range" span 40 seconds, which is noise
    # rather than price action once broker spread is taken into account.
    strategyTimeframe: str = "5m"
    strategyBarCount: int = 400
    barRefreshIntervalSec: float = 15.0
    # A signal is only worth acting on while the bar that produced it is still
    # current. Beyond this fraction of one bar's duration past its close, price
    # has moved on but the stop and target have not, so the trade would be
    # taken on geometry that no longer matches the market.
    maxBarAgeFraction: float = 0.5
    # Reject an order when the live price has drifted this far from the price
    # the signal was built at, measured as a fraction of the intended risk.
    # Drift shrinks the reward and widens the risk without either being
    # repriced, which is how a 0.60 reward-to-risk setup became 0.05 in live
    # trading.
    maxEntryDriftRiskFraction: float = 0.25
    priceActionBreakoutWindow: int = 20
    priceActionSweepWindow: int = 12
    priceActionTrendFastWindow: int = 8
    priceActionTrendSlowWindow: int = 34
    priceActionMomentumWindow: int = 5
    # Thresholds are expressed in ATR rather than as a fraction of price, so a
    # single setting behaves the same on EURUSD, USDJPY and XAUUSD. The old
    # percentage form (0.0016 of price) demanded an ~18 pip break on EURUSD M5
    # where ATR is under 5 pips, which suppressed breakouts almost entirely.
    priceActionBreakoutAtrMultiple: float = 0.15
    priceActionMinTrendSeparationAtr: float = 0.25
    # Floor on stop distance. Without it the strategy emits stops as tight as
    # the spread itself, which are stopped out at the moment of entry.
    priceActionMinStopAtrMultiple: float = 0.6
    priceActionMinStopSpreadMultiple: float = 3.0
    priceActionRewardToRisk: float = 2.1
    # Which setups may produce signals. Measured on Exness M15 with the 2.0R
    # target: breakout is +0.077R and positive in both halves of the sample,
    # while liquidity_sweep is -0.027R and reverses out-of-sample (+0.017 to
    # -0.074). Inverting a sweep means betting a rejection fails, which is the
    # opposite of what the sweep is evidence for.
    enabledSetups: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["breakout"]
    )

    @field_validator("enabledSetups", mode="before")
    @classmethod
    def parse_enabled_setups(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, list):
            return [item.strip().lower() for item in value if item]
        if not value:
            return ["breakout"]
        return [item.strip().lower() for item in value.split(",") if item.strip()]
    mlEnabled: bool = True
    mlCandleInterval: str = "1m"
    mlTrainingLookbackCandles: int = 720
    mlPredictionHorizonBars: int = 5
    mlTargetMovePct: float = 0.18
    mlMinTradeProbability: float = 0.58
    mlProbabilityEdge: float = 0.04
    mlValidationSplitPct: float = 0.2
    mlMinValidationSamples: int = 24
    mlMinValidationDecisionCount: int = 8
    mlMinValidationDecisionPrecision: float = 0.53
    mlRetrainIntervalSec: float = 1_800.0
    mlPreferLocalDataset: bool = True
    mlDatasetRoot: Path = Path("data/training")
    mlLoadArtifactOnStartup: bool = True
    mlModelArtifactPath: Path = Path("models/ml_signal_model.json")
    # --- fixed-cash-target scalper (python -m app.scalper) ----------------
    # Separate from the main strategy. Takes a small fixed profit against a
    # much larger fixed loss, which produces a high win rate and a rare large
    # loss. Judge it on realised P/L, never on the win rate: with a 0.20
    # target, 4.00 stop and 0.10 of spread, break-even is about 97%.
    # Archived 2026-08-07: no entry signal (side merely alternated), so it
    # held opposing positions that could only net out to minus the spread.
    # Measured -$0.121/trade live. See tag archive/three-bot-fleet.
    scalperEnabled: bool = False
    # Symbols the scalper rotates through. Cost per unit of risk varies a lot:
    # roughly 0.016R on USDJPY against 0.060R on XAUUSD, so gold loses close to
    # four times faster for the same stop.
    scalperSymbols: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["BTCUSD"]
    )

    @field_validator("scalperSymbols", mode="before")
    @classmethod
    def parse_scalper_symbols(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, list):
            return [i.strip().upper() for i in value if i]
        if not value:
            return ["BTCUSD"]
        return [i.strip().upper() for i in value.split(",") if i.strip()]

    # Retained so an existing .env keeps working; scalperSymbols wins if set.
    scalperSymbol: str = "GBPUSD"
    scalperTargetUsd: float = 0.20
    scalperStopUsd: float = 4.00
    scalperLots: float = 0.0            # 0 = the broker minimum
    scalperSide: Literal["buy", "sell", "alternate"] = "alternate"
    scalperMaxOpenPositions: int = 5
    # Trail instead of taking the fixed target. Spread costs the same per
    # trade whatever the target, so capping every winner at scalperTargetUsd
    # hands a fixed cost a shrinking reward. Trailing arms at the old target
    # and then follows price, so a move that keeps going keeps paying.
    scalperTrailingEnabled: bool = True
    # Profit in ACCOUNT CURRENCY before the trail arms. Defaults to the old
    # fixed target, so the trade is at least as good as taking it.
    scalperTrailActivateUsd: float = 0.20
    scalperTrailAtrMultiple: float = 0.5

    scalperPollSec: float = 2.0
    # Distinct from mt5MagicNumber so the main bot's account view, which
    # filters by magic, never counts these positions as its own.
    scalperMagicNumber: int = 990_212
    # Hard limits. This payoff shape loses in rare large steps, so these are
    # the only thing between a bad hour and an empty account.
    scalperEquityFloorUsd: float = 0.0   # 0 = disabled
    scalperMaxDrawdownUsd: float = 25.0
    scalperMaxConsecutiveLosses: int = 5
    scalperMaxTrades: int = 0            # 0 = unlimited

    # --- CRT bot (magic 990213) -------------------------------------------
    # Candle Range Theory: a sweep of the previous range candle sets direction,
    # the stop sits beyond the swept wick, and the exit is a trailing stop.
    # Measured unprofitable in backtest at every configuration tried; it runs
    # here so its live results can be compared against the other bots.
    # Archived 2026-08-07: negative at every reward ratio tested,
    # -$0.451/trade live. See tag archive/three-bot-fleet.
    crtEnabled: bool = False
    crtMagicNumber: int = 990_213
    crtExecutionTimeframe: str = "5m"
    # Range candle = this many execution bars. 3 x 5m = 15m range candles.
    crtRangeFactor: int = 3
    crtMaxOpenPositions: int = 5
    crtRiskPerTradePct: float = 0.5
    crtPollSec: float = 10.0
    # Ignore one-tick overshoots: a sweep should be a real excursion.
    crtMinSweepAtr: float = 0.1
    crtStopBufferAtr: float = 0.25
    crtTrailActivateR: float = 0.25
    crtTrailAtrMultiple: float = 0.5

    # Refuse every operator/trading endpoint, leaving only reads. Intended for
    # when the API is reachable beyond loopback - a phone on a private network,
    # say - since none of these endpoints authenticate.
    apiReadOnly: bool = False

    persistRuntimeState: bool = True
    stateStorePath: Path = Path("data/state/runtime.sqlite3")
    stateCheckpointIntervalSec: float = 5.0

    # --- edge bot ---------------------------------------------------------
    # MA trend gate + candlestick entry + structural stop. The only design in
    # this project that beat a matched coin-flip control while staying positive
    # in both halves of the sample.
    #
    # A 280-cell sweep (7 instruments x 4 timeframes x 2 gates x 5 exits) put
    # every survivor worth having on gold. GBPUSD was included as a deliberate
    # negative control and produced none, which is what the cost study
    # predicts: spread there is 0.278R against 0.024R on gold, and no edge
    # measured anywhere in this project exceeded 0.05R.
    edgeEnabled: bool = False
    # Gold only by default. The second-tier candidates (USTEC 15m, US30 15m)
    # measured t<2.0 and are indistinguishable from the ~19 false positives
    # that 280 trials produce by chance, so they are opt-in rather than on.
    edgeSymbols: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["XAUUSD"]
    )

    @field_validator("edgeSymbols", mode="before")
    @classmethod
    def parse_edge_symbols(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, list):
            return [i.strip().upper() for i in value if i]
        if not value:
            return ["XAUUSD"]
        return [i.strip().upper() for i in value.split(",") if i.strip()]

    # 30m measured +0.135R at 4.2 trades/day against 1h's +0.151R at 2.1/day -
    # nearly double the daily total, and stable in both halves either way.
    edgeTimeframe: str = "30m"
    # The multi-timeframe veto. Set empty to disable and trade the execution
    # timeframe alone.
    edgeHigherTimeframe: str = "4h"
    # Stacked EMA20/50/200. The sweep's "full" gate beat the "fast" one on
    # gold at every exit tested.
    edgeRequireAnchor: bool = True
    edgeStopBufferAtr: float = 0.10
    # Spread ceiling as a fraction of the stop distance. This single ratio
    # decided every result measured here, so it is enforced per trade rather
    # than assumed from the instrument.
    edgeMaxSpreadFraction: float = 0.25

    # Exit: trail armed at 1.0R. Arming earlier protects a profit that would
    # have grown anyway 95% of the time and pays for it with all the upside.
    edgeTrailActivateR: float = 1.0
    edgeTrailAtrMultiple: float = 0.5
    # Not a target - a broker-side backstop far beyond where the trail will
    # ever exit, so the position stays bounded if this process dies.
    edgeBackstopR: float = 25.0

    # Risk. 0.5% is roughly one-seventh Kelly on the measured edge, which is
    # the right fraction while the 95% interval still includes zero. A
    # percentage rather than fixed cash, so it compounds down as well as up.
    edgeRiskPct: float = 0.50
    edgeMaxRiskPct: float = 0.80
    edgeMaxOpenPositions: int = 6
    edgeMaxHeatPct: float = 3.0
    # Set to catch malfunction, not variance: at a 31% win rate a 19-loss
    # streak is expected over 1000 trades, and halting on that stops a working
    # system at its worst moment.
    edgeDailyLossHaltPct: float = 6.0
    edgeHardStopDrawdownPct: float = 25.0

    edgePollSec: float = 20.0
    edgeMagicNumber: int = 990_214
    # Index CFDs gap over the weekend and a stop does not protect against a
    # gap - the fill happens at a price that never traded in between.
    edgeFlatBeforeWeekend: bool = True
    edgeWeekendFlatHours: float = 2.0

    @field_validator("symbols", mode="before")
    @classmethod
    def parse_symbols(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, list):
            return [item.upper() for item in value if item]
        if not value:
            return ["EURUSD", "XAUUSD", "BTCUSD"]
        return [item.strip().upper() for item in value.split(",") if item.strip()]


settings = Settings()
