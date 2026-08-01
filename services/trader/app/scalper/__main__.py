"""Run the scalper: python -m app.scalper"""
from __future__ import annotations

import asyncio
import logging

from app.config import Settings
from app.core.logging import configure_logging
from app.mt5.client import Mt5Client
from app.scalper.engine import ScalperEngine


async def main() -> None:
    settings = Settings()
    configure_logging(settings)
    logging.getLogger("app").setLevel(logging.WARNING)

    client = Mt5Client(settings)
    engine = ScalperEngine(settings, client)
    try:
        await engine.start()
    except KeyboardInterrupt:
        engine.running = False
    finally:
        await client.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nstopped")
