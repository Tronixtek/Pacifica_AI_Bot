from __future__ import annotations

from dataclasses import dataclass

from app.edge.patterns import PatternHit, detect, structural_stop
from app.edge.trend import TrendView, aligned_direction, atr, read_trend
from app.mt5.models import Bar


@dataclass(slots=True, frozen=True)
class Signal:
    symbol: str
    direction: str            # "buy" | "sell"
    entry: float
    stop: float
    atr: float
    pattern: str
    reason: str

    @property
    def risk(self) -> float:
        return abs(self.entry - self.stop)


@dataclass(slots=True, frozen=True)
class Rejection:
    """Why no trade. Kept explicit so the dashboard can show idleness honestly.

    A bot that is silent because nothing qualified looks identical to one that
    is silent because it crashed, unless it says which.
    """

    symbol: str
    reason: str


def evaluate(
    symbol: str,
    bars: list[Bar],
    higher_bars: list[Bar] | None,
    *,
    require_anchor: bool = True,
    stop_buffer_atr: float = 0.10,
    atr_period: int = 14,
    min_stop_atr: float = 0.20,
    max_spread_fraction_of_risk: float = 0.25,
) -> Signal | Rejection:
    """Decide whether to trade `symbol`, given closed bars.

    The order of checks is deliberate: trend first, then the multi-timeframe
    veto, then the pattern, then cost. Cheap rejections come first, and the
    cost check comes last because it needs the stop distance that only the
    pattern can supply.

    `bars` and `higher_bars` must END with a CLOSED bar. A pattern read from a
    forming bar can un-form before the close, which is the classic way live
    behaviour drifts from a backtest.
    """
    a = atr(bars, atr_period)
    if a <= 0:
        return Rejection(symbol, "No usable ATR yet.")

    execution: TrendView = read_trend(bars, atr=a, require_anchor=require_anchor)
    if not execution.resolved:
        return Rejection(symbol, "No trend on the execution timeframe.")

    higher: TrendView | None = None
    if higher_bars is not None:
        higher = read_trend(
            higher_bars, atr=atr(higher_bars, atr_period), require_anchor=require_anchor
        )

    permitted = aligned_direction(execution, higher)
    if permitted is None:
        if higher is not None and higher.resolved:
            return Rejection(
                symbol,
                f"Higher timeframe is {higher.direction}, execution is "
                f"{execution.direction}; the veto blocks trading against it.",
            )
        return Rejection(symbol, "Timeframes do not agree on a direction.")

    hit: PatternHit | None = detect(bars, a)
    if hit is None:
        return Rejection(symbol, f"Trend is {execution.direction}, but no pattern fired.")
    if hit.direction != permitted:
        return Rejection(
            symbol,
            f"{hit.name} points {hit.direction} against a {execution.direction} "
            f"trend; counter-trend entries are not taken.",
        )

    entry = bars[-1].close
    stop = structural_stop(hit, a, stop_buffer_atr)
    risk = abs(entry - stop)

    # A structural level can sit almost on top of the close, which would demand
    # an enormous position to risk the intended cash and leave the spread
    # dwarfing the trade.
    if risk < min_stop_atr * a:
        return Rejection(
            symbol,
            f"{hit.name} gives a stop only {risk / a:.2f} ATR away; too tight to size.",
        )

    # The single check that decided every result in this project. Spread is a
    # fixed cash cost, so what matters is its size RELATIVE to the risk - the
    # same 8-point spread is trivial on a wide stop and fatal on a narrow one.
    spread = bars[-1].spreadPrice
    if spread > 0 and spread > max_spread_fraction_of_risk * risk:
        return Rejection(
            symbol,
            f"Spread is {spread / risk:.1%} of the risk, above the "
            f"{max_spread_fraction_of_risk:.0%} ceiling. Not worth trading.",
        )

    return Signal(
        symbol=symbol,
        direction=permitted,
        entry=entry,
        stop=stop,
        atr=a,
        pattern=hit.name,
        reason=(
            f"{hit.name} in a {execution.direction}trend"
            + (f", {higher.direction} on the higher timeframe" if higher and higher.resolved else "")
            + f"; stop {risk / a:.2f} ATR beyond the pattern."
        ),
    )
