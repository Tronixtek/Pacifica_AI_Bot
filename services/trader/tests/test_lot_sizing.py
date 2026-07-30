from __future__ import annotations

from app.config import Settings
from app.contracts import StrategySignal
from app.mt5.execution import Mt5ExecutionService
from app.mt5.models import MarketSpec
from datetime import datetime, timezone


def _make_execution_service(**settings_overrides) -> Mt5ExecutionService:
    settings = Settings(**settings_overrides)
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


def test_compute_volume_matches_risk_capital_formula():
    service = _make_execution_service()
    signal = _make_signal(notionalUsd=1000.0, entryPrice=1.1000)
    spec = _make_market_spec()

    volume = service._compute_volume(signal, spec)

    # implied_units = 1000 / 1.1 = 909.0909...; raw_volume = implied_units * (0.0001/1.0)
    # = 0.090909...; floored to the 0.01 volume step => 0.09
    assert volume == 0.09


def test_compute_volume_rejects_when_below_broker_minimum():
    service = _make_execution_service()
    # A tiny notional produces a raw volume well under the 0.01 minimum lot.
    signal = _make_signal(notionalUsd=1.0, entryPrice=1.1000)
    spec = _make_market_spec()

    volume = service._compute_volume(signal, spec)

    assert volume == 0.0


def test_compute_volume_clamps_to_broker_maximum():
    service = _make_execution_service()
    signal = _make_signal(notionalUsd=50_000_000.0, entryPrice=1.1000)
    spec = _make_market_spec(volumeMax=50.0)

    volume = service._compute_volume(signal, spec)

    assert volume == 50.0


def test_manual_test_signal_uses_requested_size_directly():
    service = _make_execution_service()
    spec = _make_market_spec()
    # Smoke-test signals carry an exact target lot size in `size` rather than a
    # risk-derived notional; build_order_request should honor it verbatim.
    signal = _make_signal(setup="manual_test", size=spec.volumeMin, notionalUsd=1234.0)

    order = service.build_order_request(signal, {signal.symbol: spec})

    assert order["volume"] == spec.volumeMin


def test_build_order_request_raises_when_volume_rounds_to_zero():
    service = _make_execution_service()
    signal = _make_signal(notionalUsd=1.0, entryPrice=1.1000)
    spec = _make_market_spec()

    try:
        service.build_order_request(signal, {signal.symbol: spec})
    except RuntimeError as exc:
        assert "zero lots" in str(exc)
    else:
        raise AssertionError("Expected build_order_request to reject a zero-lot order.")
