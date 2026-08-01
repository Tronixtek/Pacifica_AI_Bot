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
    maxOpenPositions: int = 3
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
    persistRuntimeState: bool = True
    stateStorePath: Path = Path("data/state/runtime.sqlite3")
    stateCheckpointIntervalSec: float = 5.0

    @field_validator("symbols", mode="before")
    @classmethod
    def parse_symbols(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, list):
            return [item.upper() for item in value if item]
        if not value:
            return ["EURUSD", "XAUUSD", "BTCUSD"]
        return [item.strip().upper() for item in value.split(",") if item.strip()]


settings = Settings()
