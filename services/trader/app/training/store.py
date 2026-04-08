from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


class DatasetStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def write_jsonl(
        self,
        relative_path: str | Path,
        rows: Iterable[dict[str, Any]],
        *,
        append: bool,
    ) -> int:
        target = self.root / Path(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        count = 0
        with target.open(mode, encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, separators=(",", ":"), default=str))
                handle.write("\n")
                count += 1
        return count

    def append_jsonl(self, relative_path: str | Path, row: dict[str, Any]) -> None:
        self.write_jsonl(relative_path, [row], append=True)

    def update_manifest(self, key: str, payload: dict[str, Any]) -> None:
        target = self.root / "manifest.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        manifest: dict[str, Any] = {}
        if target.exists():
            manifest = json.loads(target.read_text(encoding="utf-8"))

        manifest[key] = {
            **manifest.get(key, {}),
            **payload,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }
        target.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

