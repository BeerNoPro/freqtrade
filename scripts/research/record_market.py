"""
Record Binance market data the public archive does not keep (see ``mce.recorder``):
liquidations, best bid/ask, predicted funding, order book snapshots, options IV / greeks /
open interest and daily exchange metadata. Runs until stopped, one instance at a time.

    .venv/Scripts/python.exe scripts/research/record_market.py
    .venv/Scripts/python.exe scripts/research/record_market.py --minutes 10   # bounded test run
    .venv/Scripts/python.exe scripts/research/record_market.py --compact-only

Windows: ``scripts/research/schedule_recorder.ps1`` (Task Scheduler). VPS:
``deploy/systemd/signal-recorder.service`` (docs/deploy-vps.md).
"""

import argparse
import asyncio
import contextlib
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import aiohttp
import pandas as pd
from filelock import FileLock, Timeout
from mce.recorder import Recorder, compact_all


REPO = Path(__file__).resolve().parents[2]
logger = logging.getLogger("recorder")


async def record(outdir: Path, minutes: float | None) -> None:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
        run = Recorder(outdir, session).run()
        if minutes is None:
            await run
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(run, minutes * 60)


def setup_logging(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        RotatingFileHandler(path, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    ]
    if sys.stderr is not None:  # pythonw (scheduled task) has no console
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--outdir", type=Path, default=REPO / "user_data" / "research" / "live")
    parser.add_argument("--log", type=Path, default=REPO / "user_data" / "logs" / "recorder.log")
    parser.add_argument("--minutes", type=float, help="stop after this many minutes (test runs)")
    parser.add_argument("--compact-only", action="store_true", help="compact past days and exit")
    args = parser.parse_args()
    setup_logging(args.log)

    args.outdir.mkdir(parents=True, exist_ok=True)
    lock = FileLock(args.outdir / ".recorder.lock")
    try:
        lock.acquire(timeout=0)
    except Timeout:
        logger.info("another recorder holds %s, exiting", lock.lock_file)
        return
    try:
        if args.compact_only:
            today = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d")
            logger.info("compacted %s", compact_all(args.outdir, today))
            return
        logger.info("recorder started, writing to %s", args.outdir)
        asyncio.run(record(args.outdir, args.minutes))
        logger.info("recorder stopped")
    finally:
        lock.release()


if __name__ == "__main__":
    main()
