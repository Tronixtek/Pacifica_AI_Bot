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

    def load(self) -> PersistedEngineState | None:
        if not self.enabled:
            return None

        try:
            self._ensure_schema()
            with sqlite3.connect(self.path) as connection:
                row = connection.execute(
                    """
                    SELECT payload, updated_at
                    FROM runtime_state
                    WHERE snapshot_key = ?
                    """,
                    ("engine",),
                ).fetchone()
            if row is None:
                self.lastError = None
                return None

            payload, updated_at = row
            snapshot = PersistedEngineState.model_validate_json(str(payload))
            self.lastRestoredAt = self._parse_iso_timestamp(updated_at)
            self.lastError = None
            return snapshot
        except Exception as exc:
            self.lastError = f"State restore failed: {exc}"
            return None

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
                        updated_at
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(snapshot_key) DO UPDATE SET
                        schema_version = excluded.schema_version,
                        payload = excluded.payload,
                        updated_at = excluded.updated_at
                    """,
                    (
                        "engine",
                        snapshot.schemaVersion,
                        payload,
                        updated_at.isoformat(),
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
            connection.commit()

    def _parse_iso_timestamp(self, value: str | None) -> datetime | None:
        if not value:
            return None
        return datetime.fromisoformat(value)
