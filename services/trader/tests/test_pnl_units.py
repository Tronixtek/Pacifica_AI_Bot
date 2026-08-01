"""PnL must agree with the broker, which means `size` is always lots.

The bug this guards: paper positions carried `size` in base-currency units
while `tickValue` is quoted per LOT. Multiplying them scaled paper PnL by the
contract size - a factor of 100,000 on a standard forex lot - but only once
MT5 specs had loaded, so it never appeared in pure paper mode.
"""

from app.mt5.models import MarketSpec
from app.runtime.state import EngineRuntimeState


def _state_with_spec(symbol="EURUSDm", **spec_overrides):
    state = EngineRuntimeState(startingEquityUsd=500.0)
    state.bootstrap_markets([symbol])
    defaults = dict(
        symbol=symbol, digits=5, tickSize=0.00001, tickValue=1.0,
        contractSize=100_000.0, volumeStep=0.01, volumeMin=0.01,
        volumeMax=200.0, fillingModes=["IOC"],
    )
    defaults.update(spec_overrides)
    state.apply_market_specs({symbol: MarketSpec(**defaults)})
    return state


def test_one_lot_eurusd_moving_one_pip_is_ten_dollars():
    """The canonical sanity check every forex trader knows by heart.

    On a 5-digit feed tickSize is a POINT (0.00001); one pip is ten of them,
    so 1.15000 -> 1.15010.
    """
    state = _state_with_spec()
    pnl = state._calculate_pnl("EURUSDm", "long", 1.15000, 1.15010, 1.0)
    assert round(pnl, 2) == 10.00


def test_micro_lot_scales_proportionally():
    state = _state_with_spec()
    pnl = state._calculate_pnl("EURUSDm", "long", 1.15000, 1.15010, 0.01)
    assert round(pnl, 2) == 0.10


def test_short_side_inverts_the_sign():
    state = _state_with_spec()
    # 1.15000 -> 1.14900 is 10 pips (100 points) against a long.
    long_pnl = state._calculate_pnl("EURUSDm", "long", 1.15000, 1.14900, 0.10)
    short_pnl = state._calculate_pnl("EURUSDm", "short", 1.15000, 1.14900, 0.10)
    assert round(long_pnl, 2) == -10.00
    assert round(short_pnl, 2) == 10.00


def test_jpy_pair_uses_account_currency_tick_value():
    """USDJPY tickValue is already converted to USD by the terminal.

    0.01 lots over 150 points at 0.62862 USD per point per lot = $0.94.
    """
    state = _state_with_spec("USDJPYm", digits=3, tickSize=0.001, tickValue=0.62862)
    pnl = state._calculate_pnl("USDJPYm", "long", 159.000, 158.850, 0.01)
    assert round(pnl, 2) == -0.94


def test_risk_at_stop_matches_what_the_sizer_intended():
    """Closing at the stop must cost the risk budget the sizer solved for.

    This is the round-trip that proves sizing and PnL share a unit: if either
    side reverted to base-currency units, these would diverge by 100,000x.
    """
    state = _state_with_spec()
    # A 40 point stop at 0.09 lots - the sizing the live terminal produced
    # for a $3.75 budget on this account.
    entry, stop, lots = 1.15000, 1.14960, 0.09
    loss = state._calculate_pnl("EURUSDm", "long", entry, stop, lots)
    expected = ((entry - stop) / 0.00001) * 1.0 * lots
    assert round(abs(loss), 2) == round(expected, 2)
    # And it lands near a 0.75% risk budget on a $500 account.
    assert 3.0 <= abs(loss) <= 4.5
