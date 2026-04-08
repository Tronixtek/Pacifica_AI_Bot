from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic.alias_generators import to_snake
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


SERVICE_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        alias_generator=to_snake,
        populate_by_name=True,
        env_file=SERVICE_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    appEnv: str = "development"
    serviceName: str = "pacifica-trader"
    frontendOrigin: str = "http://127.0.0.1:3000"
    logLevel: str = "INFO"
    logFormat: Literal["json", "plain"] = "json"
    auditLogPath: Path = Path("logs/audit.jsonl")

    botMode: Literal["paper", "testnet", "mainnet"] = "paper"
    pacificaNetwork: Literal["testnet", "mainnet"] = "testnet"
    pacificaRestUrl: str = "https://test-api.pacifica.fi/api/v1"
    pacificaWsUrl: str = "wss://test-ws.pacifica.fi/ws"
    pacificaAccountAddress: str | None = None
    pacificaAgentPrivateKey: str | None = None
    pacificaApiConfigKey: str | None = None
    pacificaBuilderCode: str | None = None

    symbols: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["BTC", "ETH", "SOL"]
    )
    useSimulatedFeed: bool = True
    preferWebsocketFeed: bool = True
    enableLiveTrading: bool = False
    pollIntervalSec: float = 2.0
    accountSyncIntervalSec: float = 10.0
    marketDataStaleAfterSec: float = 15.0
    wsHeartbeatSec: float = 20.0

    startingEquityUsd: float = 10_000.0
    maxRiskPerTradePct: float = 0.75
    minSignalConfidence: float = 0.72
    maxDailyLossPct: float = 3.0
    enforceDailyLossLimit: bool = False
    maxOpenPositions: int = 3
    defaultLeverage: float = 3.0
    contrarianExecutionEnabled: bool = True
    signalCooldownSeconds: int = 45
    priceActionBreakoutWindow: int = 20
    priceActionSweepWindow: int = 12
    priceActionTrendFastWindow: int = 8
    priceActionTrendSlowWindow: int = 34
    priceActionMomentumWindow: int = 5
    priceActionBreakoutBuffer: float = 0.0016
    priceActionRewardToRisk: float = 2.1
    signatureExpiryWindowMs: int = 5_000
    slippagePercent: float = 0.35
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
            return ["BTC", "ETH", "SOL"]
        return [item.strip().upper() for item in value.split(",") if item.strip()]


settings = Settings()
