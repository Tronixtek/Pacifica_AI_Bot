"""Restored state must belong to the configuration that is running now.

The observed failure: a paper run on EURUSD/XAUUSD/BTCUSD was restored into a
demo session on EURUSDm/GBPUSDm/USDJPYm, producing a watchlist containing both
sets and a $10,000 paper balance on a $500 account.
"""

from datetime import datetime, timezone

import pytest

from app.config import Settings
from app.runtime.persistence import RuntimeStateStore
from app.runtime.state import PersistedEngineState


def _settings(tmp_path, **overrides):
    base = dict(
        _env_file=None,
        persistRuntimeState=True,
        stateStorePath=tmp_path / "runtime.sqlite3",
        botMode="paper",
        symbols=["EURUSD", "XAUUSD"],
        startingEquityUsd=10_000.0,
    )
    base.update(overrides)
    return Settings(**base)


def _snapshot(**overrides):
    defaults = dict(
        startingEquityUsd=10_000.0,
        realizedPnlUsd=0.0,
        persistedAt=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return PersistedEngineState(**defaults)


def test_snapshot_round_trips_under_identical_config(tmp_path):
    store = RuntimeStateStore(_settings(tmp_path))
    store.save(_snapshot())

    reloaded = RuntimeStateStore(_settings(tmp_path)).load()
    assert reloaded is not None
    assert reloaded.startingEquityUsd == 10_000.0


def test_mode_change_discards_the_snapshot(tmp_path):
    RuntimeStateStore(_settings(tmp_path)).save(_snapshot())

    store = RuntimeStateStore(_settings(tmp_path, botMode="demo"))
    assert store.load() is None
    assert "different configuration" in store.discardReason


def test_symbol_change_discards_the_snapshot(tmp_path):
    RuntimeStateStore(_settings(tmp_path)).save(_snapshot())

    store = RuntimeStateStore(_settings(tmp_path, symbols=["EURUSD", "GBPUSD"]))
    assert store.load() is None
    assert "different configuration" in store.discardReason


def test_starting_equity_change_discards_the_snapshot(tmp_path):
    RuntimeStateStore(_settings(tmp_path)).save(_snapshot())

    store = RuntimeStateStore(_settings(tmp_path, startingEquityUsd=500.0))
    assert store.load() is None


def test_symbol_order_does_not_matter(tmp_path):
    """Reordering SYMBOLS in .env is not a configuration change."""
    RuntimeStateStore(_settings(tmp_path, symbols=["EURUSD", "XAUUSD"])).save(_snapshot())

    store = RuntimeStateStore(_settings(tmp_path, symbols=["XAUUSD", "EURUSD"]))
    assert store.load() is not None


def test_old_schema_version_is_discarded(tmp_path, monkeypatch):
    """A v5 snapshot holds `size` in units, not lots, and must not be restored."""
    import sqlite3

    settings = _settings(tmp_path)
    store = RuntimeStateStore(settings)
    store.save(_snapshot())

    with sqlite3.connect(settings.stateStorePath) as conn:
        conn.execute("UPDATE runtime_state SET schema_version = 5")
        conn.commit()

    fresh = RuntimeStateStore(_settings(tmp_path))
    assert fresh.load() is None
    assert "schema v5" in fresh.discardReason


def test_discarded_snapshot_is_not_reconsidered(tmp_path):
    """Once rejected the row is deleted, so a later boot starts clean."""
    RuntimeStateStore(_settings(tmp_path)).save(_snapshot())

    changed = _settings(tmp_path, botMode="demo")
    assert RuntimeStateStore(changed).load() is None
    # Reverting the config must NOT bring the stale snapshot back.
    assert RuntimeStateStore(_settings(tmp_path)).load() is None
