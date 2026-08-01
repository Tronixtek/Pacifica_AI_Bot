"""Guards that must hold before the bot is allowed to send real orders."""

from datetime import datetime, timezone

import pytest

from app.config import Settings
from app.contracts import StrategySignal
from app.mt5.execution import Mt5ExecutionService
from app.mt5.models import MarketSpec
from app.risk.manager import RiskManager
from app.strategy.price_action import StrategyCandidate


def _spec(**overrides):
    defaults = dict(
        symbol="EURUSDm", digits=5, tickSize=0.00001, tickValue=1.0,
        contractSize=100_000.0, volumeStep=0.01, volumeMin=0.01,
        volumeMax=200.0, fillingModes=["IOC"], stopsLevel=0,
    )
    defaults.update(overrides)
    return MarketSpec(**defaults)


def _signal(**overrides):
    defaults = dict(
        id="sig-1", symbol="EURUSDm", setup="breakout", bias="long",
        confidence=0.8, entryPrice=1.15000, stopLoss=1.14600,
        takeProfit=1.15800, size=0.05, notionalUsd=5750.0,
        status="approved", reason="test", createdAt=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return StrategySignal(**defaults)


class _Book:
    """Minimal stand-in for the risk book the engine passes in."""

    def __init__(self, equity=500.0, realized=0.0, margin=500.0):
        self.startingEquityUsd = equity
        self.realizedPnlUsd = realized
        self.currentEquityUsd = equity + realized
        self.availableMarginUsd = margin
        self.positions = {}


class _Client:
    def __init__(self, loss_per_lot=40.0, margin=2.0):
        self.loss_per_lot, self.margin = loss_per_lot, margin

    async def calc_profit(self, side, symbol, volume, po, pc):
        return -self.loss_per_lot * volume

    async def calc_margin(self, side, symbol, volume, price):
        return self.margin * volume


def _candidate(**overrides):
    defaults = dict(
        symbol="EURUSDm", setup="breakout", bias="long", confidence=0.8,
        entryPrice=1.15000, stopLoss=1.14600, takeProfit=1.15800,
        reason="test", atr=0.0005, spreadPoints=8,
    )
    defaults.update(overrides)
    return StrategyCandidate(**defaults)


# --- daily loss limit -----------------------------------------------------


@pytest.mark.asyncio
async def test_daily_loss_limit_blocks_when_enforced():
    settings = Settings(_env_file=None, enforceDailyLossLimit=True, maxDailyLossPct=3.0)
    manager = RiskManager(settings, _Client())
    # 3% of 500 is 15; a 20 loss is past the limit.
    book = _Book(equity=500.0, realized=-20.0)

    decision = await manager.review(_candidate(), book, _spec())

    assert not decision.approved
    assert "Daily loss limit" in decision.reason
    assert decision.riskState == "reduced"


@pytest.mark.asyncio
async def test_daily_loss_limit_allows_trading_below_the_threshold():
    settings = Settings(_env_file=None, enforceDailyLossLimit=True, maxDailyLossPct=3.0)
    manager = RiskManager(settings, _Client())
    decision = await manager.review(_candidate(), _Book(equity=500.0, realized=-5.0), _spec())
    assert decision.approved


@pytest.mark.asyncio
async def test_daily_loss_limit_is_inert_when_disabled():
    settings = Settings(_env_file=None, enforceDailyLossLimit=False, maxDailyLossPct=3.0)
    manager = RiskManager(settings, _Client())
    decision = await manager.review(_candidate(), _Book(equity=500.0, realized=-400.0), _spec())
    assert decision.approved


# --- margin ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_trade_is_blocked_when_margin_exceeds_available():
    settings = Settings(_env_file=None)
    # 10,000 per lot of margin against 500 available.
    manager = RiskManager(settings, _Client(margin=10_000.0))
    decision = await manager.review(_candidate(), _Book(margin=500.0), _spec())
    assert not decision.approved
    assert "Margin required" in decision.reason


# --- broker stop distance -------------------------------------------------


def test_stop_inside_broker_minimum_is_rejected():
    service = Mt5ExecutionService(Settings(_env_file=None), client=None)  # type: ignore[arg-type]
    spec = _spec(stopsLevel=100)  # 100 points minimum
    signal = _signal(stopLoss=1.14950)  # only 50 points away

    with pytest.raises(RuntimeError, match="inside the broker minimum"):
        service.build_order_request(signal, {signal.symbol: spec})


def test_take_profit_inside_broker_minimum_is_rejected():
    service = Mt5ExecutionService(Settings(_env_file=None), client=None)  # type: ignore[arg-type]
    spec = _spec(stopsLevel=100)
    signal = _signal(takeProfit=1.15050)  # only 50 points away

    with pytest.raises(RuntimeError, match="inside the broker minimum"):
        service.build_order_request(signal, {signal.symbol: spec})


def test_zero_stops_level_imposes_no_constraint():
    """Exness reports 0, which must not be treated as 'reject everything'."""
    service = Mt5ExecutionService(Settings(_env_file=None), client=None)  # type: ignore[arg-type]
    order = service.build_order_request(_signal(), {"EURUSDm": _spec(stopsLevel=0)})
    assert order["volume"] == 0.05


def test_orders_comfortably_outside_the_minimum_are_accepted():
    service = Mt5ExecutionService(Settings(_env_file=None), client=None)  # type: ignore[arg-type]
    order = service.build_order_request(_signal(), {"EURUSDm": _spec(stopsLevel=50)})
    assert order["sl"] == pytest.approx(1.14600)
    assert order["tp"] == pytest.approx(1.15800)
