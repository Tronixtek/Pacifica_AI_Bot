import pytest

from app.config import Settings, env_alias


@pytest.mark.parametrize(
    "field_name, expected",
    [
        # The regression this guards: a digit must not split the token.
        ("mt5Login", "mt5_login"),
        ("mt5Password", "mt5_password"),
        ("mt5Server", "mt5_server"),
        ("mt5MagicNumber", "mt5_magic_number"),
        ("mt5DeviationPoints", "mt5_deviation_points"),
        ("mt5ConnectTimeoutMs", "mt5_connect_timeout_ms"),
        # Ordinary camelCase fields keep working.
        ("botMode", "bot_mode"),
        ("useSimulatedFeed", "use_simulated_feed"),
        ("maxRiskPerTradePct", "max_risk_per_trade_pct"),
        ("symbols", "symbols"),
        ("symbolSuffix", "symbol_suffix"),
    ],
)
def test_env_alias_mapping(field_name, expected):
    assert env_alias(field_name) == expected


def test_mt5_env_vars_actually_bind(monkeypatch):
    """MT5_* env vars must reach the settings object.

    These previously resolved to `mt_5_*` aliases, so every one of them was
    ignored and `_mt5_configured()` stayed False, disabling account sync.
    """
    monkeypatch.setenv("MT5_LOGIN", "476096391")
    monkeypatch.setenv("MT5_SERVER", "Exness-MT5Trial9")
    monkeypatch.setenv("MT5_PASSWORD", "secret")
    monkeypatch.setenv("MT5_MAGIC_NUMBER", "12345")
    monkeypatch.setenv("MT5_DEVIATION_POINTS", "30")

    settings = Settings(_env_file=None)

    assert settings.mt5Login == 476096391
    assert settings.mt5Server == "Exness-MT5Trial9"
    assert settings.mt5Password == "secret"
    assert settings.mt5MagicNumber == 12345
    assert settings.mt5DeviationPoints == 30


def test_symbol_suffix_binds(monkeypatch):
    monkeypatch.setenv("SYMBOL_SUFFIX", ".raw")
    assert Settings(_env_file=None).symbolSuffix == ".raw"


def test_symbols_list_parses_from_csv(monkeypatch):
    monkeypatch.setenv("SYMBOLS", "eurusd, gbpusd ,usdjpy")
    assert Settings(_env_file=None).symbols == ["EURUSD", "GBPUSD", "USDJPY"]
