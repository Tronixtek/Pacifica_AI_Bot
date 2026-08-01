from app.mt5.symbols import SymbolResolver


EXNESS = [
    "EURUSDm", "GBPUSDm", "USDJPYm", "AUDUSDm", "USDCHFm",
    "USDCADm", "NZDUSDm", "XAUUSDm", "BTCUSDm",
]

PLAIN = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCHF", "XAUUSD"]

DOTTED = ["EURUSD.raw", "GBPUSD.raw", "USDJPY.raw", "AUDUSD.raw", "USDCHF.raw"]


def test_detects_exness_m_suffix():
    resolution = SymbolResolver(EXNESS).resolve(["EURUSD", "XAUUSD"])
    assert resolution.suffix == "M"
    assert resolution.resolved == {"EURUSD": "EURUSDm", "XAUUSD": "XAUUSDm"}
    assert resolution.unresolved == []


def test_plain_broker_names_need_no_suffix():
    resolution = SymbolResolver(PLAIN).resolve(["EURUSD", "XAUUSD"])
    assert resolution.suffix == ""
    assert resolution.resolved == {"EURUSD": "EURUSD", "XAUUSD": "XAUUSD"}


def test_detects_dotted_suffix():
    resolution = SymbolResolver(DOTTED).resolve(["EURUSD", "USDJPY"])
    assert resolution.suffix == ".RAW"
    assert resolution.resolved == {"EURUSD": "EURUSD.raw", "USDJPY": "USDJPY.raw"}


def test_configured_suffix_overrides_detection():
    available = EXNESS + ["EURUSD.pro", "GBPUSD.pro"]
    resolution = SymbolResolver(available, configured_suffix=".pro").resolve(["EURUSD"])
    assert resolution.detectedFrom == "configured"
    assert resolution.resolved == {"EURUSD": "EURUSD.pro"}


def test_btcusd_does_not_match_btcusdt():
    # A naive startswith() match would bind BTCUSD to the USDT perpetual and
    # trade a completely different instrument.
    available = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCHF", "BTCUSDT"]
    resolution = SymbolResolver(available).resolve(["BTCUSD"])
    assert resolution.suffix == ""
    assert resolution.resolved.get("BTCUSD") != "BTCUSDT"
    assert "BTCUSD" in resolution.unresolved


def test_unmatched_symbols_are_reported_not_guessed():
    resolution = SymbolResolver(EXNESS).resolve(["EURUSD", "NOSUCHPAIR"])
    assert resolution.resolved == {"EURUSD": "EURUSDm"}
    assert resolution.unresolved == ["NOSUCHPAIR"]


def test_broker_exact_name_passes_through():
    resolution = SymbolResolver(EXNESS).resolve(["EURUSDm"])
    assert resolution.resolved == {"EURUSDm": "EURUSDm"}


def test_empty_symbol_list_from_terminal_resolves_nothing():
    resolution = SymbolResolver([]).resolve(["EURUSD"])
    assert resolution.resolved == {}
    assert resolution.unresolved == ["EURUSD"]


class _FakeMarketData:
    def __init__(self, tradable, resolved):
        self.tradingSymbols = tradable
        self.resolution = type("R", (), {"resolved": resolved})()


class _EngineShim:
    """Exercises the symbol-resolution helper without booting the engine."""

    from app.runtime.engine import TradingEngine

    _resolve_requested_symbol = TradingEngine._resolve_requested_symbol

    def __init__(self, tradable, resolved):
        self.marketData = _FakeMarketData(tradable, resolved)

    @property
    def engineSymbols(self):
        return self.marketData.tradingSymbols


def _shim():
    return _EngineShim(
        ["EURUSDm", "GBPUSDm", "USDJPYm"],
        {"EURUSD": "EURUSDm", "GBPUSD": "GBPUSDm", "USDJPY": "USDJPYm"},
    )


def test_operator_can_use_the_broker_name_verbatim():
    assert _shim()._resolve_requested_symbol("EURUSDm") == "EURUSDm"


def test_uppercased_broker_name_still_resolves():
    # The regression: .upper() produced EURUSDM, which matched nothing and made
    # the smoke-test order unreachable on any broker with a lowercase suffix.
    assert _shim()._resolve_requested_symbol("EURUSDM") == "EURUSDm"


def test_configured_name_resolves_to_the_broker_name():
    assert _shim()._resolve_requested_symbol("EURUSD") == "EURUSDm"


def test_lowercase_input_resolves():
    assert _shim()._resolve_requested_symbol("  eurusd  ") == "EURUSDm"


def test_unknown_symbol_is_returned_for_the_caller_to_reject():
    assert _shim()._resolve_requested_symbol("NOSUCH") == "NOSUCH"
