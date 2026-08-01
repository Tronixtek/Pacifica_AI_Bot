from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class MarketSpec:
    symbol: str
    digits: int
    tickSize: float
    tickValue: float
    contractSize: float
    volumeStep: float
    volumeMin: float
    volumeMax: float
    fillingModes: list[str] = field(default_factory=list)
    # Broker-enforced minimum distance, in points, between the market price and
    # a stop or target. Orders violating it are rejected with "Invalid stops".
    # Exness reports 0 here, but many brokers enforce 10-30.
    stopsLevel: int = 0
    freezeLevel: int = 0


@dataclass(slots=True)
class Bar:
    """One closed OHLC bar on the strategy timeframe.

    `spreadPoints` is the broker spread recorded on the bar, which lets the
    strategy reject setups formed during a spread blowout without needing a
    separate tick history.
    """

    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    spreadPoints: int = 0
    # The same spread expressed in price units. Carried alongside the point
    # count so the strategy can compare a stop distance against the spread
    # without needing the symbol spec.
    spreadPrice: float = 0.0

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def upperWick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lowerWick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def isBullish(self) -> bool:
        return self.close >= self.open


@dataclass(slots=True)
class MarketQuote:
    symbol: str
    markPrice: float
    midPrice: float | None = None
    bidPrice: float | None = None
    askPrice: float | None = None
    updatedAt: datetime | None = None

    @property
    def spreadBps(self) -> float:
        if self.bidPrice is None or self.askPrice is None:
            return 0.0
        midpoint = (self.bidPrice + self.askPrice) / 2
        if midpoint <= 0:
            return 0.0
        return abs(self.askPrice - self.bidPrice) / midpoint * 10_000


@dataclass(slots=True)
class RemoteAccountSnapshot:
    equityUsd: float
    availableMarginUsd: float
    balanceUsd: float
    totalMarginUsedUsd: float
    marginLevelPct: float | None
    currency: str
    leverage: int
    tradeAllowed: bool
    openPositions: int
    openOrders: int = 0
    updatedAt: datetime | None = None


@dataclass(slots=True)
class RemotePositionSnapshot:
    ticket: int
    symbol: str
    side: str
    size: float
    entryPrice: float
    stopLoss: float | None
    takeProfit: float | None
    notionalUsd: float
    swapUsd: float | None = None
    profitUsd: float | None = None
    openedAt: datetime | None = None
    updatedAt: datetime | None = None


@dataclass(slots=True)
class RemoteOpenOrderSnapshot:
    orderId: int
    symbol: str
    side: str
    orderType: str
    price: float
    stopPrice: float | None
    volume: float
    volumeRemaining: float
    notionalUsd: float
    createdAt: datetime | None = None
    updatedAt: datetime | None = None


@dataclass(slots=True)
class RemoteTradingSnapshot:
    account: RemoteAccountSnapshot
    positions: list[RemotePositionSnapshot]
    openOrders: list[RemoteOpenOrderSnapshot]
    syncedAt: datetime | None = None


@dataclass(slots=True)
class ExecutionResult:
    accepted: bool
    message: str
    payload: dict[str, Any]
    response: dict[str, Any] | None = None
    orderId: int | None = None
