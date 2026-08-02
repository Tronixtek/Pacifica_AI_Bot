"""Per-bot attribution on a shared account, and CRT sweep detection.

Three bots trade one MT5 account, separated only by magic number. If
attribution is wrong the whole point of the exercise - comparing them - is
lost, and it fails silently because the totals still add up.
"""

from datetime import datetime, timezone

import pytest

from app.crt.detector import aggregate, detect
from app.mt5.models import Bar
from app.performance.attribution import attribute


REGISTRY = {
    990211: ("price_action", "Price Action"),
    990212: ("scalper", "Scalper"),
    990213: ("crt", "CRT"),
}
T0 = int(datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc).timestamp())


class Deal:
    def __init__(self, position_id, entry, magic=0, profit=0.0, swap=0.0,
                 commission=0.0, symbol="BTCUSDm", time=T0):
        self.position_id, self.entry, self.magic = position_id, entry, magic
        self.profit, self.swap, self.commission = profit, swap, commission
        self.symbol, self.time = symbol, time


class Position:
    def __init__(self, magic, volume=0.01, profit=0.0, swap=0.0, symbol="BTCUSDm"):
        self.magic, self.volume = magic, volume
        self.profit, self.swap, self.symbol = profit, swap, symbol


def _round_trip(pid, magic, profit, symbol="BTCUSDm"):
    return [
        Deal(pid, 0, magic=magic, symbol=symbol),               # entry
        Deal(pid, 1, magic=magic, profit=profit, symbol=symbol),  # exit
    ]


# --- attribution ----------------------------------------------------------


def test_each_bot_gets_only_its_own_trades():
    deals = (_round_trip(1, 990211, +2.0) + _round_trip(2, 990212, -1.0)
             + _round_trip(3, 990213, +0.5) + _round_trip(4, 990211, -0.5))
    by_id = {b.botId: b for b in attribute(deals, [], REGISTRY)}

    assert by_id["price_action"].trades == 2
    assert by_id["price_action"].realisedUsd == pytest.approx(1.5)
    assert by_id["scalper"].trades == 1
    assert by_id["scalper"].realisedUsd == pytest.approx(-1.0)
    assert by_id["crt"].trades == 1


def test_manual_close_still_credits_the_bot_that_opened_it():
    """Closing from the phone records magic 0 on the EXIT deal.

    Attributing by the exit would drop the trade entirely - and a manual close
    is exactly the one you want to see against the bot responsible.
    """
    deals = [
        Deal(9, 0, magic=990213),                      # CRT opened it
        Deal(9, 1, magic=0, profit=-3.0),              # closed by hand
    ]
    by_id = {b.botId: b for b in attribute(deals, [], REGISTRY)}
    assert by_id["crt"].trades == 1
    assert by_id["crt"].realisedUsd == pytest.approx(-3.0)


def test_swap_and_commission_are_included():
    deals = [Deal(1, 0, magic=990211),
             Deal(1, 1, magic=990211, profit=2.0, swap=-0.30, commission=-0.20)]
    assert attribute(deals, [], REGISTRY)[0].realisedUsd == pytest.approx(1.5)


def test_open_positions_are_split_by_magic():
    positions = [Position(990211, profit=1.0), Position(990212, profit=-0.5),
                 Position(990212, profit=-0.25)]
    by_id = {b.botId: b for b in attribute([], positions, REGISTRY)}
    assert by_id["price_action"].openPositions == 1
    assert by_id["scalper"].openPositions == 2
    assert by_id["scalper"].unrealisedUsd == pytest.approx(-0.75)


def test_unknown_magic_is_ignored_not_misattributed():
    """A manual trade placed by hand has magic 0 and belongs to no bot."""
    deals = _round_trip(1, 0, +5.0)
    assert all(b.trades == 0 for b in attribute(deals, [], REGISTRY))


def test_win_rate_and_average_are_derived_not_stored():
    deals = (_round_trip(1, 990211, +1.0) + _round_trip(2, 990211, +1.0)
             + _round_trip(3, 990211, -2.0))
    bot = attribute(deals, [], REGISTRY)[0]
    assert bot.trades == 3
    assert bot.winRate == pytest.approx(66.7, abs=0.1)
    assert bot.averageUsd == pytest.approx(0.0)


def test_equity_impact_combines_realised_and_open():
    deals = _round_trip(1, 990213, +4.0)
    bot = {b.botId: b for b in attribute(deals, [Position(990213, profit=-1.5)], REGISTRY)}["crt"]
    assert bot.equityImpactUsd == pytest.approx(2.5)


def test_symbols_traded_are_collected():
    deals = _round_trip(1, 990211, +1.0, "BTCUSDm") + _round_trip(2, 990211, +1.0, "GBPUSDm")
    assert set(attribute(deals, [], REGISTRY)[0].symbols) == {"BTCUSDm", "GBPUSDm"}


# --- CRT detection --------------------------------------------------------


def _bar(o, h, l, c):
    return Bar(time=datetime.now(timezone.utc), open=o, high=h, low=l, close=c)


def test_sweep_of_the_low_reads_bullish():
    c1 = _bar(1.1000, 1.1050, 1.0950, 1.1020)
    c2 = _bar(1.1020, 1.1030, 1.0930, 1.1010)      # wicked under, closed inside
    setup = detect(c1, c2)
    assert setup is not None and setup.bullish and setup.bias == "long"
    assert setup.sweptBy == pytest.approx(0.0020)


def test_sweep_of_the_high_reads_bearish():
    c1 = _bar(1.1000, 1.1050, 1.0950, 1.1020)
    c2 = _bar(1.1020, 1.1080, 1.1010, 1.1030)
    setup = detect(c1, c2)
    assert setup is not None and not setup.bullish and setup.bias == "short"


def test_close_outside_the_range_is_not_a_sweep():
    """Breaking out and staying out is continuation, not a liquidity grab."""
    c1 = _bar(1.1000, 1.1050, 1.0950, 1.1020)
    c2 = _bar(1.1020, 1.1030, 1.0900, 1.0940)      # closed below the range
    assert detect(c1, c2) is None


def test_outside_bar_sweeping_both_sides_is_rejected():
    """Liquidity taken above AND below gives no directional read."""
    c1 = _bar(1.1000, 1.1050, 1.0950, 1.1020)
    c2 = _bar(1.1020, 1.1090, 1.0910, 1.1000)
    assert detect(c1, c2) is None


def test_inside_bar_is_not_a_sweep():
    c1 = _bar(1.1000, 1.1050, 1.0950, 1.1020)
    c2 = _bar(1.1010, 1.1040, 1.0960, 1.1015)
    assert detect(c1, c2) is None


def test_one_tick_overshoot_is_filtered_by_the_atr_floor():
    c1 = _bar(1.1000, 1.1050, 1.0950, 1.1020)
    c2 = _bar(1.1020, 1.1030, 1.09499, 1.1010)     # barely under
    assert detect(c1, c2) is not None              # counts with no floor
    assert detect(c1, c2, min_sweep_atr=0.5, atr=0.0010) is None


def test_aggregate_builds_higher_timeframe_candles():
    bars = [_bar(1.0, 1.5, 0.5, 1.2), _bar(1.2, 1.8, 1.1, 1.6), _bar(1.6, 1.7, 0.9, 1.0)]
    merged = aggregate(bars, 3)
    assert len(merged) == 1
    assert merged[0].open == 1.0 and merged[0].close == 1.0
    assert merged[0].high == 1.8 and merged[0].low == 0.5


def test_aggregate_drops_an_incomplete_trailing_group():
    """A partial group is a forming candle and must not be treated as closed."""
    bars = [_bar(1, 2, 0, 1) for _ in range(7)]
    assert len(aggregate(bars, 3)) == 2      # 6 bars used, the 7th withheld
