from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import Settings


class AuditLogger:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.path = self._resolve_path(settings.auditLogPath)

    def write(
        self,
        *,
        event_type: str,
        action: str,
        status: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "service": self.settings.serviceName,
            "eventType": event_type,
            "action": action,
            "status": status,
            "details": details or {},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, separators=(",", ":"), default=str))
            handle.write("\n")

    def _resolve_path(self, path: Path) -> Path:
        if path.is_absolute():
            return path
        return (Path(__file__).resolve().parents[2] / path).resolve()

