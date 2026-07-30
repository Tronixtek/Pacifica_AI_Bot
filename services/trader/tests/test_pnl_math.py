from __future__ import annotations

from app.runtime.state import EngineRuntimeState, SymbolState


def _state_with_symbol(**symbol_overrides) -> EngineRuntimeState:
    state = EngineRuntimeState(startingEquityUsd=10_000.0)
    defaults = dict(
        symbol="EURUSD",
        baselinePrice=1.1000,
        lastPrice=1.1000,
        spreadBps=1.0,
    )
    defaults.update(symbol_overrides)
    state.markets[defaults["symbol"]] = SymbolState(**defaults)
    return state


def test_calculate_pnl_uses_tick_value_when_market_spec_known():
    state = _state_with_symbol(tickSize=0.0001, tickValue=1.0, contractSize=100_000.0)

    # Long 0.09 lots, entry 1.1000 -> exit 1.1040 is a 40-pip (400-tick... 0.0040/0.0001=40
    # ticks) move at $1/tick/lot => 40 * 1.0 * 0.09 = 3.6
    pnl = state._calculate_pnl("EURUSD", "long", 1.1000, 1.1040, 0.09)

    assert round(pnl, 4) == 3.6


def test_calculate_pnl_short_direction_is_inverted():
    state = _state_with_symbol(tickSize=0.0001, tickValue=1.0, contractSize=100_000.0)

    long_pnl = state._calculate_pnl("EURUSD", "long", 1.1000, 1.0980, 0.10)
    short_pnl = state._calculate_pnl("EURUSD", "short", 1.1000, 1.0980, 0.10)

    assert long_pnl < 0
    assert short_pnl > 0
    assert round(long_pnl, 6) == round(-short_pnl, 6)


def test_calculate_pnl_falls_back_to_contract_size_when_no_tick_value_known():
    # No tickSize/tickValue set (e.g. paper mode before any real MT5 spec has synced).
    state = _state_with_symbol(contractSize=1.0)

    pnl = state._calculate_pnl("EURUSD", "long", 1.1000, 1.1100, 2.0)

    assert round(pnl, 4) == round((1.1100 - 1.1000) * 2.0 * 1.0, 4)


def test_position_value_usd_scales_by_contract_size():
    state = _state_with_symbol(contractSize=100_000.0)

    notional = state._position_value_usd("EURUSD", price=1.1000, size=0.5)

    assert notional == round(0.5 * 1.1000 * 100_000.0, 2)


def test_position_value_usd_defaults_contract_size_to_one_for_unknown_symbol():
    state = EngineRuntimeState(startingEquityUsd=10_000.0)

    notional = state._position_value_usd("UNKNOWN", price=50.0, size=3.0)

    assert notional == 150.0
