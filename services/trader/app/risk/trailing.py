from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class TrailDecision:
    """Whether a position's stop should move, and where to."""

    shouldMove: bool
    stopLoss: float = 0.0
    reason: str = ""


class TrailingStop:
    """Ratchets a stop behind the best price a position has reached.

    Replaces a fixed take-profit: the trade exits when the trailed stop is hit
    rather than at a preset level, so a move that keeps going keeps paying. A
    fixed 0.25R target caps every winner at 0.25R no matter how far price runs.

    Two rules are non-negotiable:

    - The stop only ever moves in the profitable direction. Widening a stop to
      "give the trade room" converts a bounded loss into an unbounded one.
    - Trailing arms only once price is `activate_r` in front. Trailing from
      entry tightens the stop while the trade is still noise, and the measured
      result is worse: arming at 0.25R scored +0.029R against +0.041R only for
      a very tight 0.5 ATR trail, and every later-arming variant beat arming
      immediately at wider trails.
    """

    def __init__(self, activate_r: float = 0.25, trail_atr_multiple: float = 0.5) -> None:
        self.activate_r = activate_r
        self.trail_atr_multiple = trail_atr_multiple

    def evaluate(
        self,
        *,
        side: str,
        entry_price: float,
        initial_stop: float,
        current_stop: float,
        best_price: float,
        atr: float,
    ) -> TrailDecision:
        """Where the stop should sit given the best price reached so far.

        `best_price` is the most favourable extreme since entry: the lowest low
        for a short, the highest high for a long.
        """
        risk = abs(entry_price - initial_stop)
        if risk <= 0 or atr <= 0:
            return TrailDecision(False, reason="No usable risk or volatility measure.")

        advance = (entry_price - best_price) if side == "short" else (best_price - entry_price)
        # Compare with a relative tolerance. Price differences are computed from
        # values like 1.1010 - 1.1000, which lands at 0.00099999... in binary
        # floating point, so a position sitting exactly on the arming threshold
        # would otherwise fail to arm depending on the price level involved.
        threshold = risk * self.activate_r
        if advance < threshold - abs(threshold) * 1e-9:
            return TrailDecision(
                False,
                reason=(
                    f"Trail not armed: {advance / risk:.2f}R in front, "
                    f"needs {self.activate_r:.2f}R."
                ),
            )

        offset = atr * self.trail_atr_multiple
        candidate = best_price + offset if side == "short" else best_price - offset

        # Ratchet only. For a short the stop may fall, never rise.
        improved = candidate < current_stop if side == "short" else candidate > current_stop
        if not improved:
            return TrailDecision(False, reason="Trailed stop would not improve on the current one.")

        return TrailDecision(
            True,
            stopLoss=candidate,
            reason=(
                f"Trailed to {self.trail_atr_multiple:.2f} ATR behind the best price "
                f"({advance / risk:.2f}R in front)."
            ),
        )
