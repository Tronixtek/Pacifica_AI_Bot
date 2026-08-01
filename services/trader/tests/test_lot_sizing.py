from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.config import Settings
from app.contracts import StrategySignal
from app.mt5.execution import Mt5ExecutionService
from app.mt5.models import MarketSpec
from app.risk.sizing import PositionSizer


def _make_execution_service(**settings_overrides) -> Mt5ExecutionService:
    settings = Settings(_env_file=None, **settings_overrides)
    return Mt5ExecutionService(settings, client=None)  # type: ignore[arg-type]


def _make_signal(**overrides) -> StrategySignal:
    defaults = dict(
        id="sig-1",
        symbol="EURUSD",
        setup="breakout",
        bias="long",
        confidence=0.8,
        entryPrice=1.1000,
        stopLoss=1.0980,
        takeProfit=1.1040,
        size=0.0,
        notionalUsd=1000.0,
        status="approved",
        reason="test",
        createdAt=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return StrategySignal(**defaults)


def _make_market_spec(**overrides) -> MarketSpec:
    defaults = dict(
        symbol="EURUSD",
        digits=5,
        tickSize=0.0001,
        tickValue=1.0,
        contractSize=100_000.0,
        volumeStep=0.01,
        volumeMin=0.01,
        volumeMax=100.0,
        fillingModes=["IOC"],
    )
    defaults.update(overrides)
    return MarketSpec(**defaults)


class FakeClient:
    """Stands in for the terminal's own profit/margin calculation."""

    def __init__(self, loss_per_lot: float, margin: float = 0.0):
        self.loss_per_lot = loss_per_lot
        self.margin = margin

    async def calc_profit(self, side, symbol, volume, price_open, price_close):
        # Real order_calc_profit reports a loss as negative and scales linearly.
        return -abs(self.loss_per_lot) * volume

    async def calc_margin(self, side, symbol, volume, price):
        return self.margin * volume


# --- sizing ---------------------------------------------------------------


async def _size(client, risk, spec=None, entry=1.1000, stop=1.0980):
    return await PositionSizer(client).size_for_risk(
        symbol="EURUSD", side="long", entry_price=entry,
        stop_loss=stop, risk_amount=risk, spec=spec or _make_market_spec(),
    )


@pytest.mark.asyncio
async def test_lots_are_floored_to_the_broker_step():
    # $3.75 risk at $40 loss per lot = 0.09375 lots, which must floor to 0.09.
    # Rounding up would exceed the configured risk per trade.
    result = await _size(FakeClient(loss_per_lot=40.0), risk=3.75)
    assert result.ok
    assert result.lots == 0.09
    assert result.riskAmountUsd == pytest.approx(3.60)


@pytest.mark.asyncio
async def test_rejects_when_risk_budget_is_below_the_minimum_lot():
    """Never inflate to volumeMin - that would silently exceed the risk cap."""
    result = await _size(FakeClient(loss_per_lot=5000.0), risk=3.75)
    assert not result.ok
    assert result.lots == 0.0
    assert "below the broker minimum lot" in result.reason
    # The operator is told what the minimum would actually have risked.
    assert "50.00" in result.reason


@pytest.mark.asyncio
async def test_clamps_to_the_broker_maximum():
    spec = _make_market_spec(volumeMax=50.0)
    result = await _size(FakeClient(loss_per_lot=0.01), risk=100_000.0, spec=spec)
    assert result.ok
    assert result.lots == 50.0


@pytest.mark.asyncio
async def test_both_sizing_paths_agree_on_a_cross_currency_pair():
    """Cross-check the fallback against the terminal on USDJPY.

    USDJPY quotes profit in JPY, so a raw price distance is NOT a loss in
    account currency. The spec path survives this only because MT5 reports
    `trade_tick_value` already converted into the account currency (0.62862
    USD per point per lot, not 100 JPY). Both paths must therefore agree -
    if they ever diverge, `tickValue` has gone stale or zero, which is the
    cold-start failure `wait_for_tick` exists to prevent.
    """
    jpy_spec = _make_market_spec(
        symbol="USDJPY", digits=3, tickSize=0.001, tickValue=0.62862
    )
    entry, stop = 159.000, 158.850  # 150 points

    from_terminal = await PositionSizer(FakeClient(loss_per_lot=94.32)).size_for_risk(
        symbol="USDJPY", side="long", entry_price=entry, stop_loss=stop,
        risk_amount=3.75, spec=jpy_spec,
    )
    from_spec = await PositionSizer(None).size_for_risk(
        symbol="USDJPY", side="long", entry_price=entry, stop_loss=stop,
        risk_amount=3.75, spec=jpy_spec,
    )

    assert from_terminal.source == "order_calc_profit"
    assert from_spec.source == "spec_tick_value"
    assert from_terminal.lots == from_spec.lots
    assert from_terminal.lossPerLot == pytest.approx(from_spec.lossPerLot, rel=1e-3)


@pytest.mark.asyncio
async def test_terminal_wins_when_tick_value_is_stale():
    """A zero tick value must not silently produce a zero-lot rejection.

    This is the observed cold-start state: specs read before the first tick
    report tickValue 0 on any pair needing conversion. With a terminal
    available the trade still sizes correctly.
    """
    broken_spec = _make_market_spec(symbol="USDJPY", tickSize=0.001, tickValue=0.0)

    with_terminal = await PositionSizer(FakeClient(loss_per_lot=94.32)).size_for_risk(
        symbol="USDJPY", side="long", entry_price=159.0, stop_loss=158.85,
        risk_amount=3.75, spec=broken_spec,
    )
    without = await PositionSizer(None).size_for_risk(
        symbol="USDJPY", side="long", entry_price=159.0, stop_loss=158.85,
        risk_amount=3.75, spec=broken_spec,
    )

    assert with_terminal.ok and with_terminal.lots == 0.03
    assert not without.ok


@pytest.mark.asyncio
async def test_spec_fallback_is_used_when_no_terminal_is_available():
    """Paper mode has no terminal, so sizing must still work off the spec."""
    result = await _size(None, risk=3.75)
    assert result.ok
    assert result.source == "spec_tick_value"


@pytest.mark.asyncio
async def test_zero_stop_distance_is_rejected():
    result = await _size(FakeClient(loss_per_lot=40.0), risk=3.75, stop=1.1000)
    assert not result.ok
    assert "Stop distance is zero" in result.reason


@pytest.mark.asyncio
async def test_missing_spec_is_rejected_not_guessed():
    result = await PositionSizer(None).size_for_risk(
        symbol="EURUSD", side="long", entry_price=1.10, stop_loss=1.09,
        risk_amount=3.75, spec=None,
    )
    assert not result.ok
    assert "No market spec" in result.reason


# --- order construction ---------------------------------------------------


def test_build_order_request_uses_signal_size_as_lots_directly():
    """`size` is lots from the risk layer and must pass through untouched.

    Re-deriving volume from notionalUsd here is exactly what reintroduced the
    currency-conversion error on non-USD-quoted pairs.
    """
    service = _make_execution_service()
    spec = _make_market_spec()
    signal = _make_signal(size=0.09, notionalUsd=999_999.0)

    order = service.build_order_request(signal, {signal.symbol: spec})

    assert order["volume"] == 0.09


def test_manual_test_signal_uses_requested_size_directly():
    service = _make_execution_service()
    spec = _make_market_spec()
    signal = _make_signal(setup="manual_test", size=spec.volumeMin, notionalUsd=1234.0)

    order = service.build_order_request(signal, {signal.symbol: spec})

    assert order["volume"] == spec.volumeMin


def test_build_order_request_raises_when_volume_rounds_to_zero():
    service = _make_execution_service()
    spec = _make_market_spec()
    signal = _make_signal(size=0.0001)  # under the 0.01 minimum lot

    with pytest.raises(RuntimeError, match="zero lots"):
        service.build_order_request(signal, {signal.symbol: spec})
