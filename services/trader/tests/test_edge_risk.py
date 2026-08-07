from __future__ import annotations

import pytest

from app.edge.risk import OpenRisk, RiskManager, RiskSettings, base_symbol
from app.mt5.models import MarketSpec


def spec(volumeMin=0.01, volumeStep=0.01, volumeMax=100.0):
    return MarketSpec(
        symbol="XAUUSDm", digits=3, tickSize=0.001, tickValue=0.01,
        contractSize=100.0, volumeStep=volumeStep, volumeMin=volumeMin,
        volumeMax=volumeMax,
    )


def mgr(**kw):
    return RiskManager(RiskSettings(**kw), suffix="m")


def evaluate(m, symbol="XAUUSDm", direction="buy", entry=2000.0, stop=1990.0,
             sp=None, vpp=1.0, equity=500.0, start=500.0, open_=None, today=0.0):
    return m.evaluate(
        symbol=symbol, direction=direction, entry=entry, stop=stop,
        spec=sp or spec(), valuePerPricePoint=vpp, equity=equity,
        startingEquity=start, openPositions=open_ or [], realisedToday=today,
    )


# --- sizing ---------------------------------------------------------------

def test_sizes_to_the_target_percentage_of_equity():
    # 0.5% of 500 = $2.50 risk; a 10.0 price stop at $1/point => 0.25 lots.
    d = evaluate(mgr(), entry=2000.0, stop=1990.0, vpp=1.0, equity=500.0)
    assert d.allowed
    assert d.lots == pytest.approx(0.25)
    assert d.riskCash == pytest.approx(2.50)


def test_sizing_always_rounds_down_never_up():
    """Rounding up would risk more than was authorised."""
    d = evaluate(mgr(), entry=2000.0, stop=1993.0, vpp=1.0,
                 sp=spec(volumeStep=0.10, volumeMin=0.10))
    # 2.50 / 7.0 = 0.357 lots -> must floor to 0.30, not 0.40
    assert d.lots == pytest.approx(0.30)
    assert d.riskCash <= 2.50


def test_risk_scales_with_equity():
    """Percentage sizing self-corrects; fixed cash does not.

    Each case uses its own starting equity, so this measures sizing alone
    rather than tripping the drawdown kill switch.
    """
    big = evaluate(mgr(), equity=1000.0, start=1000.0)
    small = evaluate(mgr(), equity=250.0, start=250.0)
    # Lot granularity means realised risk lands at or just BELOW target,
    # never above - 0.125 lots floors to 0.12.
    assert big.riskCash == pytest.approx(5.0)
    assert 1.20 <= small.riskCash <= 1.25
    assert small.riskCash < big.riskCash


def test_a_drawn_down_account_risks_less_than_its_starting_size():
    """Same account after losses: 10% down still trades, but smaller."""
    d = evaluate(mgr(), equity=450.0, start=500.0)
    assert d.allowed
    assert 2.15 <= d.riskCash <= 2.25
    assert d.riskPct <= 0.5


# --- minimum lot ----------------------------------------------------------

def test_minimum_lot_accepted_when_inside_the_ceiling():
    """JP225's 3.0 minimum forces more than target but under the ceiling."""
    d = evaluate(
        mgr(targetRiskPct=0.5, maxRiskPct=0.8),
        entry=40000.0, stop=39870.0, vpp=0.0067,
        sp=spec(volumeMin=3.0, volumeStep=0.01), equity=500.0,
    )
    assert d.allowed
    assert d.lots == pytest.approx(3.0)
    assert 0.5 < d.riskPct <= 0.8


def test_instrument_declined_when_minimum_lot_breaches_the_ceiling():
    d = evaluate(
        mgr(targetRiskPct=0.5, maxRiskPct=0.8),
        entry=40000.0, stop=39000.0, vpp=0.02,
        sp=spec(volumeMin=3.0), equity=500.0,
    )
    assert not d.allowed
    assert "too large for this account" in d.reason
    assert d.lots == 0.0


# --- correlation ----------------------------------------------------------

def test_second_position_in_the_same_bucket_is_refused():
    m = mgr()
    d = evaluate(m, symbol="USTECm",
                 open_=[OpenRisk("US30m", "buy", 2.5)])
    assert not d.allowed
    assert "move" in d.reason and "together" in d.reason


def test_different_buckets_are_allowed():
    m = mgr()
    d = evaluate(m, symbol="XAUUSDm",
                 open_=[OpenRisk("US30m", "buy", 2.5)])
    assert d.allowed


def test_opposing_positions_on_one_symbol_are_refused():
    """The exact defect found live: 3 buy / 2 sell on BTCUSDm."""
    m = mgr()
    d = evaluate(m, symbol="BTCUSDm", direction="sell",
                 open_=[OpenRisk("BTCUSDm", "buy", 2.5)])
    assert not d.allowed
    assert "minus the spread" in d.reason


def test_buckets_ignore_the_broker_suffix():
    m = mgr()
    assert m.bucket_for("US30m") == m.bucket_for("US30") == "us_indices"
    assert m.bucket_for("XAUUSDm") == "metals"


def test_unknown_symbols_get_their_own_bucket():
    m = mgr()
    assert m.bucket_for("WEIRDm") != m.bucket_for("OTHERm")


def test_base_symbol_strips_only_a_lowercase_suffix():
    assert base_symbol("US30m", "m") == "US30"
    assert base_symbol("XAUUSD") == "XAUUSD"
    assert base_symbol("US30m") == "US30"


# --- portfolio heat -------------------------------------------------------

def test_heat_cap_blocks_the_trade_rather_than_shrinking_it():
    m = mgr(maxPortfolioHeatPct=1.0)
    d = evaluate(m, open_=[OpenRisk("US30m", "buy", 4.0)])
    assert not d.allowed
    assert "Total open risk" in d.reason
    assert d.lots == 0.0


def test_position_cap_enforced():
    m = mgr(maxOpenPositions=2)
    d = evaluate(m, open_=[OpenRisk("US30m", "buy", 1.0),
                           OpenRisk("XAUUSDm", "buy", 1.0)])
    assert not d.allowed
    assert "position cap" in d.reason


# --- kill switches --------------------------------------------------------

def test_hard_stop_blocks_everything():
    m = mgr(hardStopDrawdownPct=25.0)
    d = evaluate(m, equity=370.0, start=500.0)
    assert not d.allowed
    assert "HARD STOP" in d.reason


def test_daily_halt_fires_only_on_a_fault_sized_loss():
    m = mgr(dailyLossHaltPct=6.0)
    # A normal bad run must NOT halt: this is the mistake that stopped the
    # scalper mid-drawdown.
    assert evaluate(m, equity=500.0, today=-20.0).allowed
    assert not evaluate(m, equity=500.0, today=-31.0).allowed


def test_hard_stop_takes_precedence_over_sizing():
    m = mgr()
    d = evaluate(m, equity=100.0, start=500.0)
    assert not d.allowed and d.lots == 0.0


def test_missing_equity_refuses_rather_than_guessing():
    assert not evaluate(mgr(), equity=0.0).allowed
