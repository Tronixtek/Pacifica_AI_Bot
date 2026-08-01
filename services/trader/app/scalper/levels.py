from __future__ import annotations

from dataclasses import dataclass

from app.mt5.models import MarketSpec


@dataclass(slots=True)
class ScalpLevels:
    """Price levels that realise a fixed cash profit and cash loss."""

    ok: bool
    entryPrice: float = 0.0
    takeProfit: float = 0.0
    stopLoss: float = 0.0
    lots: float = 0.0
    pointsToTarget: float = 0.0
    pointsToStop: float = 0.0
    spreadPoints: int = 0
    reason: str = ""


def value_per_point(spec: MarketSpec, lots: float) -> float:
    """Account-currency value of a one-point move at `lots`.

    `trade_tick_value` is quoted per LOT and already converted into the account
    currency by the terminal, so scaling by lots is all that is needed. This is
    the number that decides whether a fixed cash target is reachable at all: at
    the minimum lot on SOLUSDm it is about $0.00002, so $0.20 would need a
    20,000 point move.
    """
    if spec.tickValue <= 0 or lots <= 0:
        return 0.0
    return spec.tickValue * lots


def compute_levels(
    *,
    side: str,
    bid: float,
    ask: float,
    spec: MarketSpec,
    lots: float,
    target_usd: float,
    stop_usd: float,
) -> ScalpLevels:
    """Convert a cash target and cash stop into price levels.

    The entry fills at the ask when buying and the bid when selling, but the
    exit is measured against the OPPOSITE side: a long is closed at the bid.
    Placing the target `target_usd` away from the entry price therefore falls
    short by exactly one spread, and the trade closes for less than intended.
    The spread is added explicitly so the target is a NET figure.
    """
    if bid <= 0 or ask <= 0:
        return ScalpLevels(False, reason="No live quote.")
    if spec.tickSize <= 0:
        return ScalpLevels(False, reason=f"{spec.symbol} has no usable tick size.")

    per_point = value_per_point(spec, lots)
    if per_point <= 0:
        return ScalpLevels(
            False,
            reason=(
                f"{spec.symbol} has no tick value at {lots} lots, so a cash "
                "target cannot be converted into a price."
            ),
        )

    spread_points = round((ask - bid) / spec.tickSize)
    target_points = target_usd / per_point
    stop_points = stop_usd / per_point

    # Net of the round trip: the exit crosses back over the spread.
    gross_target_points = target_points + spread_points
    # The stop is reached sooner for the same reason, so it moves further out
    # to keep the realised loss at stop_usd rather than stop_usd + spread.
    gross_stop_points = stop_points - spread_points
    if gross_stop_points <= 0:
        return ScalpLevels(
            False,
            reason=(
                f"A {stop_usd:.2f} stop on {spec.symbol} is inside the spread "
                f"({spread_points} points, worth {spread_points * per_point:.2f})."
            ),
        )

    entry = ask if side == "buy" else bid
    step = spec.tickSize
    if side == "buy":
        take_profit = entry + gross_target_points * step
        stop_loss = entry - gross_stop_points * step
    else:
        take_profit = entry - gross_target_points * step
        stop_loss = entry + gross_stop_points * step

    digits = spec.digits
    levels = ScalpLevels(
        True,
        entryPrice=round(entry, digits),
        takeProfit=round(take_profit, digits),
        stopLoss=round(stop_loss, digits),
        lots=lots,
        pointsToTarget=gross_target_points,
        pointsToStop=gross_stop_points,
        spreadPoints=spread_points,
        reason=(
            f"{gross_target_points:.0f} points to net {target_usd:.2f}, "
            f"{gross_stop_points:.0f} points to lose {stop_usd:.2f} "
            f"(spread {spread_points})"
        ),
    )

    # A broker minimum stop distance would reject the order outright.
    if spec.stopsLevel > 0:
        if gross_target_points < spec.stopsLevel or gross_stop_points < spec.stopsLevel:
            return ScalpLevels(
                False,
                reason=(
                    f"{spec.symbol} requires {spec.stopsLevel} points minimum; this "
                    f"target is {gross_target_points:.0f} and stop {gross_stop_points:.0f}."
                ),
            )
    return levels


def spread_cost_ratio(
    spec: MarketSpec, lots: float, target_usd: float, spread_points: int
) -> float:
    """Spread cost as a fraction of the intended profit.

    The single most useful number for this strategy. A $0.20 target on a symbol
    whose spread costs $0.10 hands half of every win straight back, and no win
    rate overcomes that without a real directional edge. Returns infinity when
    the symbol cannot price the target at all.
    """
    per_point = value_per_point(spec, lots)
    if per_point <= 0 or target_usd <= 0:
        return float("inf")
    return (spread_points * per_point) / target_usd


def break_even_win_rate(target_usd: float, stop_usd: float, spread_cost_usd: float) -> float:
    """Win rate required merely to break even, once the spread is paid.

    Wins net (target - spread) while losses cost (stop + spread), so the
    threshold is worse than the raw reward-to-risk suggests. With a $0.20
    target, $4.00 stop and $0.10 spread it is about 97%.
    """
    win = target_usd - spread_cost_usd
    loss = stop_usd + spread_cost_usd
    if win <= 0:
        return 1.0
    return loss / (win + loss)
