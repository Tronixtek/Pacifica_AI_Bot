"""The flipped target is placed against the flipped risk, not the old stop.

Reusing the original stop as the target gave roughly 0.43R. Since the stop is
unchanged, spread costs the same fraction of R whatever the target, so a near
target pays the same cost for far less reward. Measured over M5 and M15,
expectancy improved monotonically out to ~2R.
"""

import pytest

from app.config import Settings
from app.runtime.engine import TradingEngine
from app.strategy.price_action import StrategyCandidate


def _engine(**overrides):
    # Trailing replaces the fixed target entirely, so these tests pin it off.
    overrides.setdefault("trailingStopEnabled", False)
    return TradingEngine(Settings(_env_file=None, **overrides))


def _candidate(bias="long", entry=1.1000, stop=1.0960, target=1.1084):
    """Default is a 40 pip stop and 2.1R target, matching the strategy."""
    return StrategyCandidate(
        symbol="EURUSDm", setup="breakout", bias=bias, confidence=0.8,
        entryPrice=entry, stopLoss=stop, takeProfit=target,
        reason="test", atr=0.0006, spreadPoints=8,
    )


def test_long_signal_flips_to_short_with_extended_target():
    eng = _engine(contrarianExecutionEnabled=True, contrarianTargetRiskMultiple=2.0)
    flipped, note = eng._apply_execution_policy(_candidate())

    assert flipped.bias == "short"
    # Stop becomes the original target, above entry for a short.
    assert flipped.stopLoss == pytest.approx(1.1084)
    risk = flipped.stopLoss - flipped.entryPrice
    assert flipped.takeProfit == pytest.approx(1.1000 - risk * 2.0)
    assert flipped.takeProfit < flipped.entryPrice   # target below for a short
    assert "2.00R" in note


def test_short_signal_flips_to_long_with_extended_target():
    eng = _engine(contrarianExecutionEnabled=True, contrarianTargetRiskMultiple=2.0)
    flipped, _ = eng._apply_execution_policy(
        _candidate(bias="short", entry=1.1000, stop=1.1040, target=1.0916)
    )

    assert flipped.bias == "long"
    assert flipped.stopLoss == pytest.approx(1.0916)   # below entry for a long
    risk = flipped.entryPrice - flipped.stopLoss
    assert flipped.takeProfit == pytest.approx(1.1000 + risk * 2.0)
    assert flipped.takeProfit > flipped.entryPrice


def test_reward_to_risk_matches_the_configured_multiple():
    for multiple in (0.75, 1.5, 2.0, 3.0):
        eng = _engine(contrarianExecutionEnabled=True, contrarianTargetRiskMultiple=multiple)
        flipped, _ = eng._apply_execution_policy(_candidate())
        risk = abs(flipped.entryPrice - flipped.stopLoss)
        reward = abs(flipped.takeProfit - flipped.entryPrice)
        assert reward / risk == pytest.approx(multiple)


def test_zero_multiple_restores_the_original_behaviour():
    """An escape hatch back to target = old stop."""
    eng = _engine(contrarianExecutionEnabled=True, contrarianTargetRiskMultiple=0.0)
    flipped, note = eng._apply_execution_policy(_candidate())
    assert flipped.takeProfit == pytest.approx(1.0960)   # the original stop
    assert "Original stop is now take profit" in note


def test_old_behaviour_really_was_a_near_target():
    """Documents the ~0.43R the previous default produced."""
    eng = _engine(contrarianExecutionEnabled=True, contrarianTargetRiskMultiple=0.0)
    flipped, _ = eng._apply_execution_policy(_candidate())
    risk = abs(flipped.entryPrice - flipped.stopLoss)
    reward = abs(flipped.takeProfit - flipped.entryPrice)
    assert reward / risk == pytest.approx(0.476, abs=0.01)


def test_disabled_contrarian_passes_the_candidate_through():
    eng = _engine(contrarianExecutionEnabled=False)
    candidate = _candidate()
    passed, note = eng._apply_execution_policy(candidate)
    assert passed is candidate
    assert note is None


def test_volatility_context_survives_the_flip():
    """ATR and spread must not be dropped - guards downstream depend on them."""
    eng = _engine(contrarianExecutionEnabled=True, contrarianTargetRiskMultiple=2.0)
    flipped, _ = eng._apply_execution_policy(_candidate())
    assert flipped.atr == pytest.approx(0.0006)
    assert flipped.spreadPoints == 8


def test_comparison_book_geometry_stays_valid_after_extension():
    """The shadow book inverts the executed trade; its stop/target must not cross."""
    eng = _engine(contrarianExecutionEnabled=True, contrarianTargetRiskMultiple=2.0)
    flipped, _ = eng._apply_execution_policy(_candidate())
    shadow = eng._build_comparison_candidate(flipped)

    assert shadow.bias == "long"
    assert shadow.stopLoss < shadow.entryPrice < shadow.takeProfit
