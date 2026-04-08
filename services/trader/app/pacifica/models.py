from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class MarketSpec:
    symbol: str
    tickSize: float
    lotSize: float
    minOrderSizeUsd: float
    maxOrderSizeUsd: float
    maxLeverage: int
    isolatedOnly: bool


@dataclass(slots=True)
class MarketQuote:
    symbol: str
    markPrice: float
    midPrice: float | None = None
    bidPrice: float | None = None
    askPrice: float | None = None
    updatedAt: datetime | None = None
    lastOrderId: int | None = None

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
    openPositions: int
    availableToWithdrawUsd: float | None = None
    pendingBalanceUsd: float | None = None
    totalMarginUsedUsd: float | None = None
    crossMaintenanceMarginUsd: float | None = None
    openOrders: int = 0
    stopOrders: int = 0
    feeLevel: int | None = None
    makerFeeRate: float | None = None
    takerFeeRate: float | None = None
    useLastTradedPriceForStops: bool | None = None
    updatedAt: datetime | None = None


@dataclass(slots=True)
class RemotePositionSnapshot:
    symbol: str
    side: str
    size: float
    entryPrice: float
    notionalUsd: float
    marginUsd: float | None = None
    fundingUsd: float | None = None
    isolated: bool = False
    openedAt: datetime | None = None
    updatedAt: datetime | None = None


@dataclass(slots=True)
class RemoteOpenOrderSnapshot:
    orderId: int
    clientOrderId: str | None
    symbol: str
    side: str
    orderType: str
    price: float
    stopPrice: float | None
    initialAmount: float
    filledAmount: float
    cancelledAmount: float
    remainingAmount: float
    notionalUsd: float
    reduceOnly: bool
    createdAt: datetime | None = None
    updatedAt: datetime | None = None


@dataclass(slots=True)
class RemoteTradingSnapshot:
    account: RemoteAccountSnapshot
    positions: list[RemotePositionSnapshot]
    openOrders: list[RemoteOpenOrderSnapshot]
    lastOrderId: int | None = None
    syncedAt: datetime | None = None


@dataclass(slots=True)
class ExecutionResult:
    accepted: bool
    message: str
    payload: dict[str, Any]
    response: dict[str, Any] | None = None
    orderId: int | None = None
