from __future__ import annotations

import json
from pathlib import Path

from app.config import Settings


class LocalTrainingDatasetLoader:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.root = self._resolve_root(settings.mlDatasetRoot)

    def load_close_history(
        self,
        *,
        symbols: list[str],
        interval: str,
        max_candles: int,
    ) -> tuple[dict[str, list[float]], list[str]]:
        close_history: dict[str, list[float]] = {}
        notes: list[str] = []

        for symbol in symbols:
            path = self.root / "raw" / self.settings.pacificaNetwork / symbol / interval / "mark_candles.jsonl"
            if not path.exists():
                notes.append(f"{symbol}: local candle file not found")
                continue

            records = self._read_jsonl(path)
            if not records:
                notes.append(f"{symbol}: local candle file is empty")
                continue

            deduped: dict[str, float] = {}
            for record in records:
                open_time = str(record.get("openTime") or "")
                close_value = record.get("close")
                if not open_time or close_value is None:
                    continue
                deduped[open_time] = float(close_value)

            ordered_closes = [deduped[key] for key in sorted(deduped.keys())]
            if len(ordered_closes) < max_candles:
                notes.append(
                    f"{symbol}: only {len(ordered_closes)} candles available locally"
                )
            if ordered_closes:
                close_history[symbol] = ordered_closes[-max_candles:]

        return close_history, notes

    def _read_jsonl(self, path: Path) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                payload = line.strip()
                if not payload:
                    continue
                rows.append(json.loads(payload))
        return rows

    def _resolve_root(self, root: Path) -> Path:
        if root.is_absolute():
            return root
        return (Path(__file__).resolve().parents[2] / root).resolve()

