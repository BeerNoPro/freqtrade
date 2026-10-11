"""
Download daily candles and funding for every Binance USDT-M perpetual that ever traded
(delisted included) to build the point-in-time research universe.

    .venv/Scripts/python.exe scripts/research/download_pit_universe.py
    .venv/Scripts/python.exe scripts/research/download_pit_universe.py --symbols BTCUSDT LUNAUSDT

Candles: every candidate symbol. Funding: symbols that are ever among the top ``--top +
--spare`` (spares cover robustness runs that drop a coin and re-rank) plus ``DEFAULT_PAIRS``.
"""

import argparse
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import ccxt
import pandas as pd
import requests
from mce.archive import (
    list_um_usdt_symbols,
    load_wide,
    missing,
    sync_from_archive,
    sync_funding,
    sync_klines,
)
from mce.universe import DEFAULT_PAIRS, is_candidate, pair_to_symbol, point_in_time_members


logger = logging.getLogger(__name__)


def _sync_all(fn, exchange: ccxt.Exchange, symbols: list[str], outdir: Path, start: int) -> int:
    added = 0
    for i, symbol in enumerate(symbols, 1):
        try:
            added += fn(exchange, symbol, outdir, start)
        except ccxt.BaseError as e:
            logger.warning("%s %s failed: %s", fn.__name__, symbol, e)
        if i % 50 == 0:
            logger.info("%s: %d/%d symbols", fn.__name__, i, len(symbols))
    return added


def _fill_from_archive(outdir: Path, kind: str, symbols: list[str]) -> list[str]:
    """Rebuild files the API could not serve from the monthly archive; return still missing."""
    for symbol in missing(outdir, kind, symbols):
        try:
            n = sync_from_archive(symbol, outdir, kind)
            logger.info("%s %s: +%d rows from the monthly archive", kind, symbol, n)
        except requests.RequestException as e:
            logger.warning("%s %s archive fallback failed: %s", kind, symbol, e)
    return missing(outdir, kind, symbols)


def ever_members(outdir: Path, symbols: list[str], n: int) -> list[str]:
    close = load_wide(outdir, "klines_1d", "close", symbols)
    volume = load_wide(outdir, "klines_1d", "quote_volume", symbols)
    days = pd.date_range(close.index.min(), close.index.max(), freq="D")
    members = point_in_time_members(close.reindex(days), volume.reindex(days), n=n)
    return sorted(members.columns[members.any()])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--outdir", type=Path, default=Path("user_data/research/pit"))
    parser.add_argument("--start", default="2019-09-01")
    parser.add_argument("--top", type=int, default=15)
    parser.add_argument("--spare", type=int, default=5)
    parser.add_argument("--symbols", nargs="+", help="raw symbols, default: whole archive")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    start = int(pd.Timestamp(args.start, tz="UTC").timestamp() * 1000)
    archive = args.symbols or list_um_usdt_symbols()
    candidates = [s for s in archive if is_candidate(s)]
    args.outdir.mkdir(parents=True, exist_ok=True)
    meta = {
        "listed_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "archive": archive,
        "candidates": candidates,
    }
    (args.outdir / "symbols.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    logger.info("%d symbols in archive, %d candidates", len(archive), len(candidates))

    exchange = ccxt.binanceusdm({"enableRateLimit": True})
    added = _sync_all(sync_klines, exchange, candidates, args.outdir, start)
    lost = _fill_from_archive(args.outdir, "klines_1d", candidates)
    print(f"klines_1d +{added} rows; no data for {lost}")

    members = ever_members(args.outdir, candidates, args.top + args.spare)
    funded = sorted(set(members) | {pair_to_symbol(p) for p in DEFAULT_PAIRS})
    logger.info("funding for %d symbols", len(funded))
    added = _sync_all(sync_funding, exchange, funded, args.outdir, start)
    lost = _fill_from_archive(args.outdir, "funding", funded)
    print(f"funding +{added} rows ({len(funded)} symbols); no data for {lost}")


if __name__ == "__main__":
    main()
