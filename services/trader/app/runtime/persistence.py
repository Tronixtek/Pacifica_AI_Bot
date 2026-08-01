from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.config import Settings
from app.contracts import ServiceHealth
from app.runtime.state import PersistedEngineState


class RuntimeStateStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.path = settings.stateStorePath
        self.enabled = settings.persistRuntimeState
        self.lastError: str | None = None
        self.lastPersistedAt: datetime | None = None
        self.lastRestoredAt: datetime | None = None
        self.discardReason: str | None = None

    @property
    def fingerprint(self) -> str:
        """Identifies the configuration a snapshot belongs to.

        A snapshot only describes the account it was taken against. Restoring a
        paper-mode book onto a demo account, or a EURUSD book after the
        watchlist changed, resurrects positions and balances that never existed
        in the new configuration.
        """
        symbols = ",".join(sorted(self.settings.symbols))
        return (
            f"mode={self.settings.botMode}"
            f"|symbols={symbols}"
            f"|equity={self.settings.startingEquityUsd}"
            f"|timeframe={self.settings.strategyTimeframe}"
        )

    def load(self) -> PersistedEngineState | None:
        if not self.enabled:
            return None

        self.discardReason = None
        try:
            self._ensure_schema()
            with sqlite3.connect(self.path) as connection:
                row = connection.execute(
                    """
                    SELECT payload, updated_at, schema_version, config_fingerprint
                    FROM runtime_state
                    WHERE snapshot_key = ?
                    """,
                    ("engine",),
                ).fetchone()
            if row is None:
                self.lastError = None
                return None

            payload, updated_at, schema_version, fingerprint = row

            expected_version = PersistedEngineState.model_fields["schemaVersion"].default
            if schema_version != expected_version:
                self.discardReason = (
                    f"Discarded a state snapshot written by schema v{schema_version}; "
                    f"this build expects v{expected_version}."
                )
                self._discard()
                return None

            if fingerprint != self.fingerprint:
                self.discardReason = (
                    "Discarded a state snapshot taken under a different configuration "
                    f"({fingerprint or 'unknown'})."
                )
                self._discard()
                return None

            snapshot = PersistedEngineState.model_validate_json(str(payload))
            self.lastRestoredAt = self._parse_iso_timestamp(updated_at)
            self.lastError = None
            return snapshot
        except Exception as exc:
            self.lastError = f"State restore failed: {exc}"
            return None

    def _discard(self) -> None:
        """Drop the stored snapshot so it cannot be reconsidered next boot."""
        try:
            with sqlite3.connect(self.path) as connection:
                connection.execute(
                    "DELETE FROM runtime_state WHERE snapshot_key = ?", ("engine",)
                )
                connection.commit()
        except Exception as exc:
            self.lastError = f"Failed to clear stale state: {exc}"

    def save(self, snapshot: PersistedEngineState) -> None:
        if not self.enabled:
            return

        updated_at = datetime.now(timezone.utc)
        try:
            self._ensure_schema()
            payload = snapshot.model_dump_json()
            with sqlite3.connect(self.path) as connection:
                connection.execute("PRAGMA journal_mode=WAL;")
                connection.execute("PRAGMA synchronous=NORMAL;")
                connection.execute(
                    """
                    INSERT INTO runtime_state (
                        snapshot_key,
                        schema_version,
                        payload,
                        updated_at,
                        config_fingerprint
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(snapshot_key) DO UPDATE SET
                        schema_version = excluded.schema_version,
                        payload = excluded.payload,
                        updated_at = excluded.updated_at,
                        config_fingerprint = excluded.config_fingerprint
                    """,
                    (
                        "engine",
                        snapshot.schemaVersion,
                        payload,
                        updated_at.isoformat(),
                        self.fingerprint,
                    ),
                )
                connection.commit()
            self.lastPersistedAt = updated_at
            self.lastError = None
        except Exception as exc:
            self.lastError = f"State checkpoint failed: {exc}"

    def health(self) -> ServiceHealth:
        if not self.enabled:
            return ServiceHealth(
                id="state_store",
                label="State Store",
                status="healthy",
                message="Durable runtime persistence is disabled by configuration.",
            )
        if self.lastError:
            return ServiceHealth(
                id="state_store",
                label="State Store",
                status="degraded",
                message=self.lastError,
            )
        if self.lastPersistedAt:
            return ServiceHealth(
                id="state_store",
                label="State Store",
                status="healthy",
                message=f"Durable runtime state checkpointed at {self.lastPersistedAt.isoformat()}.",
            )
        if self.lastRestoredAt:
            return ServiceHealth(
                id="state_store",
                label="State Store",
                status="healthy",
                message=f"Durable runtime state restored from {self.lastRestoredAt.isoformat()}.",
            )
        return ServiceHealth(
            id="state_store",
            label="State Store",
            status="healthy",
            message="Durable runtime state store is ready.",
        )

    def _ensure_schema(self) -> None:
        if not self.enabled:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute("PRAGMA journal_mode=WAL;")
            connection.execute("PRAGMA synchronous=NORMAL;")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_state (
                    snapshot_key TEXT PRIMARY KEY,
                    schema_version INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            # Added after the table shipped, so migrate rather than assume.
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(runtime_state)")
            }
            if "config_fingerprint" not in columns:
                connection.execute(
                    "ALTER TABLE runtime_state ADD COLUMN config_fingerprint TEXT"
                )
            connection.commit()

    def _parse_iso_timestamp(self, value: str | None) -> datetime | None:
        if not value:
            return None
        return datetime.fromisoformat(value)
