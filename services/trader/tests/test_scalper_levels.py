"""Converting a cash target into price levels.

The subtle part is the spread. A long fills at the ask and closes at the bid,
so a target placed `target_usd` away from the entry price realises one spread
less than intended. At a $0.20 target with $0.10 of spread that is half the
profit, so the correction is not a rounding detail.
"""

import pytest

from app.mt5.models import MarketSpec
from app.scalper.levels import (
    break_even_win_rate,
    compute_levels,
    spread_cost_ratio,
    value_per_point,
)


def _spec(**overrides):
    # GBPUSDm: 5 digits, $1 per point per lot, 10 point spread in session.
    defaults = dict(
        symbol="GBPUSDm", digits=5, tickSize=0.00001, tickValue=1.0,
        contractSize=100_000.0, volumeStep=0.01, volumeMin=0.01,
        volumeMax=200.0, fillingModes=["IOC"], stopsLevel=0,
    )
    defaults.update(overrides)
    return MarketSpec(**defaults)


BID, ASK = 1.34800, 1.34810      # 10 point spread


# --- point value ----------------------------------------------------------


def test_value_per_point_scales_with_lots():
    spec = _spec()
    assert value_per_point(spec, 0.01) == pytest.approx(0.01)
    assert value_per_point(spec, 1.00) == pytest.approx(1.00)


def test_value_per_point_is_zero_without_a_tick_value():
    """A cold-start spec reports tickValue 0 and cannot price a cash target."""
    assert value_per_point(_spec(tickValue=0.0), 0.01) == 0.0


# --- level placement ------------------------------------------------------


def test_target_accounts_for_the_spread():
    """20 points earns $0.20, but the exit crosses back over 10 points."""
    levels = compute_levels(side="buy", bid=BID, ask=ASK, spec=_spec(), lots=0.01,
                            target_usd=0.20, stop_usd=4.00)
    assert levels.ok
    assert levels.pointsToTarget == pytest.approx(30.0)   # 20 + 10 spread
    assert levels.entryPrice == pytest.approx(ASK)        # a buy fills at the ask
    assert levels.takeProfit == pytest.approx(ASK + 30 * 0.00001)


def test_stop_is_pulled_in_so_the_loss_is_the_configured_amount():
    """The stop is reached one spread sooner, so it sits further out."""
    levels = compute_levels(side="buy", bid=BID, ask=ASK, spec=_spec(), lots=0.01,
                            target_usd=0.20, stop_usd=4.00)
    # 400 points would be $4.00, less the 10 the spread already costs.
    assert levels.pointsToStop == pytest.approx(390.0)
    assert levels.stopLoss == pytest.approx(ASK - 390 * 0.00001)


def test_sell_side_mirrors_and_fills_at_the_bid():
    levels = compute_levels(side="sell", bid=BID, ask=ASK, spec=_spec(), lots=0.01,
                            target_usd=0.20, stop_usd=4.00)
    assert levels.ok
    assert levels.entryPrice == pytest.approx(BID)
    assert levels.takeProfit < levels.entryPrice < levels.stopLoss


def test_target_and_stop_land_on_opposite_sides_of_entry():
    for side in ("buy", "sell"):
        lv = compute_levels(side=side, bid=BID, ask=ASK, spec=_spec(), lots=0.01,
                            target_usd=0.20, stop_usd=4.00)
        assert lv.ok
        if side == "buy":
            assert lv.stopLoss < lv.entryPrice < lv.takeProfit
        else:
            assert lv.takeProfit < lv.entryPrice < lv.stopLoss


# --- refusals -------------------------------------------------------------

def test_symbol_that_cannot_price_the_target_is_refused():
    """SOLUSDm at minimum lot is worth ~$0.00002 a point - $0.20 is unreachable."""
    levels = compute_levels(side="buy", bid=BID, ask=ASK,
                            spec=_spec(tickValue=0.0), lots=0.01,
                            target_usd=0.20, stop_usd=4.00)
    assert not levels.ok
    assert "cannot be converted" in levels.reason


def test_stop_inside_the_spread_is_refused():
    """A stop smaller than the spread would be hit at the moment of entry."""
    wide = _spec(tickValue=1.0)
    levels = compute_levels(side="buy", bid=1.34800, ask=1.35000,   # 200 points
                            spec=wide, lots=0.01,
                            target_usd=0.20, stop_usd=1.00)          # 100 points
    assert not levels.ok
    assert "inside the spread" in levels.reason


def test_broker_minimum_stop_distance_is_respected():
    levels = compute_levels(side="buy", bid=BID, ask=ASK,
                            spec=_spec(stopsLevel=100), lots=0.01,
                            target_usd=0.20, stop_usd=4.00)
    assert not levels.ok                       # 30 point target < 100 minimum
    assert "minimum" in levels.reason


def test_missing_quote_is_refused():
    assert not compute_levels(side="buy", bid=0.0, ask=0.0, spec=_spec(), lots=0.01,
                              target_usd=0.20, stop_usd=4.00).ok


# --- the economics --------------------------------------------------------


def test_spread_takes_half_of_a_twenty_cent_target():
    """The number that decides whether this strategy can work at all."""
    ratio = spread_cost_ratio(_spec(), lots=0.01, target_usd=0.20, spread_points=10)
    assert ratio == pytest.approx(0.5)


def test_a_bigger_target_dilutes_the_spread():
    ratio = spread_cost_ratio(_spec(), lots=0.01, target_usd=2.00, spread_points=10)
    assert ratio == pytest.approx(0.05)


def test_break_even_win_rate_for_the_default_settings():
    """0.20 target, 4.00 stop, 0.10 spread needs ~97.6% wins to break even."""
    rate = break_even_win_rate(target_usd=0.20, stop_usd=4.00, spread_cost_usd=0.10)
    assert rate == pytest.approx(0.976, abs=0.005)


def test_break_even_is_impossible_when_spread_exceeds_the_target():
    """No win rate saves a trade that cannot profit even when it wins."""
    assert break_even_win_rate(target_usd=0.10, stop_usd=4.00, spread_cost_usd=0.15) == 1.0
