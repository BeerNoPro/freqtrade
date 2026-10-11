"""
Download research data for every Binance USDT-M crypto perpetual that ever traded (delisted
included): the point-in-time universe and the inputs of models trained on it.

    .venv/Scripts/python.exe scripts/research/download_pit_universe.py
    .venv/Scripts/python.exe scripts/research/download_pit_universe.py --symbols BTCUSDT LUNAUSDT

Daily candles: every candidate symbol (crypto underlying, see ``mce.universe.is_candidate``),
used to rank the universe. Funding, candles of the other ``--intervals`` and positioning metrics:
only symbols that are ever among the top ``--top + --spare`` (spares cover robustness runs that
drop a coin and re-rank), from ``--warmup-days`` before they first enter it. Rows before
``--start`` and files of other symbols are deleted, so the store only holds the current research
window plus warmup.
"""

import argparse
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import ccxt
import pandas as pd
import requests
from mce.archive import (
    list_um_usdt_symbols,
    load_wide,
    missing,
    prune,
    sync_from_archive,
    sync_funding,
    sync_klines,
    sync_metrics,
    underlying_types,
)
from mce.universe import is_candidate, point_in_time_members


logger = logging.getLogger(__name__)


def _sync_all(label: str, fn: Callable[[str, int], int], starts: dict[str, pd.Timestamp]) -> int:
    """Run ``fn(symbol, start_ms)`` for every symbol; network errors are logged and skipped."""
    added = 0
    for i, (symbol, start) in enumerate(starts.items(), 1):
        try:
            added += fn(symbol, int(start.timestamp() * 1000))
        except (ccxt.BaseError, requests.RequestException) as e:
            logger.warning("%s %s failed: %s", label, symbol, e)
        if i % 100 == 0:
            logger.info("%s: %d/%d symbols", label, i, len(starts))
    return added


def _fill_from_archive(outdir: Path, kind: str, starts: dict[str, pd.Timestamp]) -> list[str]:
    """Rebuild files the API could not serve from the monthly archive; return still missing."""
    for symbol in missing(outdir, kind, list(starts)):
        try:
            n = sync_from_archive(symbol, outdir, kind, starts[symbol])
            logger.info("%s %s: +%d rows from the monthly archive", kind, symbol, n)
        except requests.RequestException as e:
            logger.warning("%s %s archive fallback failed: %s", kind, symbol, e)
    return missing(outdir, kind, list(starts))


def _klines_fn(exchange: ccxt.Exchange, outdir: Path, interval: str) -> Callable[[str, int], int]:
    def fetch(symbol: str, start_ms: int) -> int:
        return sync_klines(exchange, symbol, outdir, start_ms, interval)

    return fetch


def first_member_dates(
    outdir: Path, symbols: list[str], n: int, min_history: int
) -> dict[str, pd.Timestamp]:
    """Symbol -> first day it is a member of the top ``n`` point-in-time universe."""
    close = load_wide(outdir, "klines_1d", "close", symbols)
    volume = load_wide(outdir, "klines_1d", "quote_volume", symbols)
    days = pd.date_range(close.index.min(), close.index.max(), freq="D")
    members = point_in_time_members(
        close.reindex(days), volume.reindex(days), n=n, min_history=min_history
    )
    members = members.loc[:, members.any()]
    return {s: members.index[members[s]].min() for s in sorted(members.columns)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--outdir", type=Path, default=Path("user_data/research/pit"))
    parser.add_argument("--start", default="2024-01-01", help="warmup start, older rows deleted")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--spare", type=int, default=5)
    parser.add_argument("--min-history", type=int, default=90, help="consecutive daily closes")
    parser.add_argument("--intervals", nargs="+", default=["1d", "4h"])
    parser.add_argument("--warmup-days", type=int, default=100)
    parser.add_argument("--symbols", nargs="+", help="raw symbols, default: whole archive")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    start = pd.Timestamp(args.start, tz="UTC")
    exchange = ccxt.binanceusdm({"enableRateLimit": True})
    underlying = underlying_types(exchange)
    archive = args.symbols or list_um_usdt_symbols()
    candidates = [s for s in archive if is_candidate(s, *underlying.get(s, [None, []]))]
    args.outdir.mkdir(parents=True, exist_ok=True)
    meta = {
        "listed_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "start": args.start,
        "archive": archive,
        "underlying": {s: underlying.get(s) for s in archive},
        "candidates": candidates,
    }
    (args.outdir / "symbols.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    logger.info("%d symbols in archive, %d crypto candidates", len(archive), len(candidates))

    removed = prune(args.outdir, "klines_1d", candidates, start)
    every = dict.fromkeys(candidates, start)
    added = _sync_all("klines_1d", lambda s, t: sync_klines(exchange, s, args.outdir, t), every)
    lost = _fill_from_archive(args.outdir, "klines_1d", every)
    print(f"klines_1d +{added} rows, {removed} stale files deleted; no data for {lost}")

    firsts = first_member_dates(args.outdir, candidates, args.top + args.spare, args.min_history)
    warm = pd.Timedelta(days=args.warmup_days)
    starts = {s: max(start, d - warm) for s, d in firsts.items()}
    removed = prune(args.outdir, "funding", list(starts), start)
    added = _sync_all("funding", lambda s, t: sync_funding(exchange, s, args.outdir, t), starts)
    lost = _fill_from_archive(args.outdir, "funding", starts)
    print(
        f"funding +{added} rows ({len(starts)} symbols), {removed} stale files; no data for {lost}"
    )

    for interval in (i for i in args.intervals if i != "1d"):
        kind = f"klines_{interval}"
        removed = prune(args.outdir, kind, list(starts), start)
        added = _sync_all(kind, _klines_fn(exchange, args.outdir, interval), starts)
        lost = _fill_from_archive(args.outdir, kind, starts)
        print(f"{kind} +{added} rows, {removed} stale files deleted; no data for {lost}")

    removed = prune(args.outdir, "metrics", list(starts), start)
    today = pd.Timestamp.now(tz="UTC")
    added = _sync_all(
        "metrics",
        lambda s, t: sync_metrics(s, args.outdir, pd.Timestamp(t, unit="ms"), today),
        starts,
    )
    print(f"metrics +{added} rows, {removed} stale files deleted")


if __name__ == "__main__":
    main()
