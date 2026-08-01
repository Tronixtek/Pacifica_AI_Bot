"""Trailing stop rules.

Replaces the fixed take-profit so a winner is not capped. Measured on Exness
M15 (breakout only): fixed 0.25R target gives 81.1% wins at -0.018R, while
arming at 0.25R and trailing 0.5 ATR gives 80.3% wins at +0.029R.

The invariant that matters most is that a stop never loosens. Widening a stop
to "give the trade room" converts a bounded loss into an unbounded one.
"""

import pytest

from app.config import Settings
from app.risk.trailing import TrailingStop
from app.runtime.engine import TradingEngine
from app.strategy.price_action import StrategyCandidate


ATR = 0.0010


def _trail(activate=0.25, mult=0.5):
    return TrailingStop(activate_r=activate, trail_atr_multiple=mult)


# --- arming ---------------------------------------------------------------


def test_trail_does_not_arm_before_the_threshold():
    """A trade barely in front is still noise; tightening there just exits early."""
    # Short from 1.1000, stop 1.1040 -> risk 0.0040. Needs 0.25R = 0.0010.
    d = _trail().evaluate(side="short", entry_price=1.1000, initial_stop=1.1040,
                          current_stop=1.1040, best_price=1.0995, atr=ATR)
    assert not d.shouldMove
    assert "not armed" in d.reason


def test_trail_arms_once_the_threshold_is_reached():
    d = _trail().evaluate(side="short", entry_price=1.1000, initial_stop=1.1040,
                          current_stop=1.1040, best_price=1.0990, atr=ATR)
    assert d.shouldMove
    # 0.5 ATR above the best price.
    assert d.stopLoss == pytest.approx(1.0990 + 0.5 * ATR)


def test_long_side_arms_and_trails_below_the_best_price():
    d = _trail().evaluate(side="long", entry_price=1.1000, initial_stop=1.0960,
                          current_stop=1.0960, best_price=1.1010, atr=ATR)
    assert d.shouldMove
    assert d.stopLoss == pytest.approx(1.1010 - 0.5 * ATR)
    assert d.stopLoss > 1.0960          # moved in the profitable direction


# --- ratcheting -----------------------------------------------------------


def test_stop_never_loosens_on_a_short():
    """The core safety invariant: a short's stop may fall, never rise."""
    # Price pulled back, so the trailed level is worse than the stop already set.
    d = _trail().evaluate(side="short", entry_price=1.1000, initial_stop=1.1040,
                          current_stop=1.0980, best_price=1.0990, atr=ATR)
    assert not d.shouldMove
    assert "would not improve" in d.reason


def test_stop_never_loosens_on_a_long():
    d = _trail().evaluate(side="long", entry_price=1.1000, initial_stop=1.0960,
                          current_stop=1.1020, best_price=1.1010, atr=ATR)
    assert not d.shouldMove


def test_repeated_advances_ratchet_the_stop_tighter():
    trail = _trail()
    stop = 1.1040
    for best in (1.0990, 1.0980, 1.0970):
        d = trail.evaluate(side="short", entry_price=1.1000, initial_stop=1.1040,
                           current_stop=stop, best_price=best, atr=ATR)
        assert d.shouldMove
        assert d.stopLoss < stop        # strictly tighter each time
        stop = d.stopLoss
    assert stop == pytest.approx(1.0970 + 0.5 * ATR)


def test_trail_can_lock_in_profit_past_breakeven():
    """Once price runs far enough the stop should sit beyond entry."""
    d = _trail().evaluate(side="short", entry_price=1.1000, initial_stop=1.1040,
                          current_stop=1.1040, best_price=1.0950, atr=ATR)
    assert d.shouldMove
    assert d.stopLoss < 1.1000          # better than breakeven for a short


# --- degenerate input -----------------------------------------------------


def test_zero_risk_is_rejected():
    d = _trail().evaluate(side="short", entry_price=1.1000, initial_stop=1.1000,
                          current_stop=1.1000, best_price=1.0900, atr=ATR)
    assert not d.shouldMove


def test_zero_atr_is_rejected():
    d = _trail().evaluate(side="short", entry_price=1.1000, initial_stop=1.1040,
                          current_stop=1.1040, best_price=1.0900, atr=0.0)
    assert not d.shouldMove


# --- interaction with the target -----------------------------------------


def _candidate():
    return StrategyCandidate(
        symbol="EURUSDm", setup="breakout", bias="long", confidence=0.8,
        entryPrice=1.1000, stopLoss=1.0960, takeProfit=1.1084,
        reason="t", atr=ATR, spreadPoints=8,
    )


def test_trailing_removes_the_capping_target():
    """With trailing on, the target must not cap the winner at 0.25R."""
    eng = TradingEngine(Settings(_env_file=None, trailingStopEnabled=True,
                                 contrarianTargetRiskMultiple=0.25))
    flipped, note = eng._apply_execution_policy(_candidate())
    risk = abs(flipped.entryPrice - flipped.stopLoss)
    reward = abs(flipped.takeProfit - flipped.entryPrice)
    assert reward / risk == pytest.approx(20.0)   # backstop, never binds
    assert "trailing stop" in note


def test_target_still_caps_when_trailing_is_off():
    eng = TradingEngine(Settings(_env_file=None, trailingStopEnabled=False,
                                 contrarianTargetRiskMultiple=0.25))
    flipped, _ = eng._apply_execution_policy(_candidate())
    risk = abs(flipped.entryPrice - flipped.stopLoss)
    reward = abs(flipped.takeProfit - flipped.entryPrice)
    assert reward / risk == pytest.approx(0.25)
