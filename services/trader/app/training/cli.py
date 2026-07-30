from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.config import Settings
from app.mt5.client import Mt5Client
from app.strategy.ml_model import MlSignalModel
from app.training.collector import Mt5TrainingCollector


def main() -> None:
    parser = argparse.ArgumentParser(description="MT5 ML training dataset tools")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backfill_parser = subparsers.add_parser(
        "backfill",
        help="Backfill historical mark candles and recent trades into local JSONL files.",
    )
    backfill_parser.add_argument("--symbols", default="", help="Comma-separated symbols. Defaults to env symbols.")
    backfill_parser.add_argument(
        "--intervals",
        default="1m,5m,15m",
        help="Comma-separated candle intervals.",
    )
    backfill_parser.add_argument(
        "--lookback-days",
        type=int,
        default=30,
        help="Number of days to backfill.",
    )
    backfill_parser.add_argument(
        "--skip-recent-trades",
        action="store_true",
        help="Skip the recent trades snapshot step.",
    )
    backfill_parser.add_argument(
        "--output-dir",
        default="",
        help="Optional output directory. Defaults to services/trader/data/training.",
    )

    stream_parser = subparsers.add_parser(
        "stream",
        help="Poll live MT5 ticks and append them to local JSONL files.",
    )
    stream_parser.add_argument("--symbols", default="", help="Comma-separated symbols. Defaults to env symbols.")
    stream_parser.add_argument(
        "--output-dir",
        default="",
        help="Optional output directory. Defaults to services/trader/data/training.",
    )
    stream_parser.add_argument(
        "--poll-interval-sec",
        type=float,
        default=2.0,
        help="Seconds between tick polls.",
    )

    fit_parser = subparsers.add_parser(
        "fit",
        help="Train the ML signal model and persist the artifact to disk.",
    )
    fit_parser.add_argument("--symbols", default="", help="Comma-separated symbols. Defaults to env symbols.")
    fit_parser.add_argument(
        "--artifact-path",
        default="",
        help="Optional artifact output path. Defaults to settings ML_MODEL_ARTIFACT_PATH.",
    )
    fit_parser.add_argument(
        "--no-local-dataset",
        action="store_true",
        help="Skip local dataset files and train from MT5 candles instead.",
    )

    args = parser.parse_args()
    asyncio.run(run(args))


async def run(args: argparse.Namespace) -> None:
    settings = Settings()
    output_dir_value = getattr(args, "output_dir", "")
    output_dir = Path(output_dir_value) if output_dir_value else None
    symbols = _parse_symbols(args.symbols) or settings.symbols

    if getattr(args, "artifact_path", ""):
        settings.mlModelArtifactPath = Path(args.artifact_path)
    if getattr(args, "no_local_dataset", False):
        settings.mlPreferLocalDataset = False

    client = Mt5Client(settings)
    collector = Mt5TrainingCollector(
        settings,
        client,
        output_root=output_dir,
    )

    try:
        connected = await client.connect()
        if not connected:
            print(f"Warning: MT5 connect() failed: {client.lastError}")

        if args.command == "backfill":
            intervals = _parse_intervals(args.intervals)
            results = await collector.backfill(
                symbols=symbols,
                intervals=intervals,
                lookback_days=args.lookback_days,
                include_recent_ticks=not args.skip_recent_trades,
            )
            for result in results:
                print(
                    f"{result.symbol} {result.interval}: "
                    f"{result.candleCount} candles, {result.tickCount} recent ticks"
                )
            print(f"Saved dataset under {collector.outputRoot}")
            return

        if args.command == "stream":
            print(
                "Streaming live MT5 ticks to "
                f"{collector.outputRoot}. Press Ctrl+C to stop."
            )
            await collector.stream_live(
                symbols=symbols,
                poll_interval_sec=args.poll_interval_sec,
            )
            return

        if args.command == "fit":
            model = MlSignalModel(settings, client)
            trained = await model.refresh_if_due(symbols=symbols, force=True)
            print(
                {
                    "trained": trained,
                    "ready": model.ready,
                    "trainingSource": model.trainingSource,
                    "trainingSamples": model.trainingSamples,
                    "trainingSymbols": model.trainingSymbols,
                    "artifactPath": str(model.artifactPath),
                    "summary": model.lastSummary,
                }
            )
            return
    finally:
        await client.shutdown()


def _parse_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_symbols(value: str) -> list[str]:
    return [item.upper() for item in _parse_csv(value)]


def _parse_intervals(value: str) -> list[str]:
    return [item.lower() for item in _parse_csv(value)]


if __name__ == "__main__":
    main()
