"""
Collector for Binance USDT-M "futures data" endpoints that only keep ~30 days of history:
open interest history, global / top-trader long-short ratios and taker buy/sell volume.
Public endpoints (no API key). Appends idempotently to one parquet file per
(endpoint, pair, period); run it at least every few days so no history is lost.
"""

import logging
import time
from pathlib import Path

import ccxt
import pandas as pd


logger = logging.getLogger(__name__)

# endpoint key -> ccxt implicit method on binanceusdm
ENDPOINTS = {
    "open_interest": "fapiDataGetOpenInterestHist",
    "global_ls_account": "fapiDataGetGlobalLongShortAccountRatio",
    "top_ls_position": "fapiDataGetTopLongShortPositionRatio",
    "top_ls_account": "fapiDataGetTopLongShortAccountRatio",
    "taker_ls": "fapiDataGetTakerlongshortRatio",
}
PERIOD_MS = {"5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}
PAGE_LIMIT = 500
HISTORY_MS = 29 * 24 * 3_600_000  # stay inside the ~30 day window the API keeps


def _file(outdir: Path, key: str, pair: str, period: str) -> Path:
    return outdir / key / f"{pair.split(':')[0].replace('/', '_')}-{period}.parquet"


def _to_frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["timestamp"] = df["timestamp"].astype("int64")
    for col in df.columns:
        if col not in ("symbol", "timestamp"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["date"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df


def fetch(
    exchange: ccxt.Exchange, key: str, symbol: str, period: str, since_ms: int
) -> pd.DataFrame:
    method = getattr(exchange, ENDPOINTS[key])
    rows: list[dict] = []
    start = since_ms
    now = exchange.milliseconds()
    while start < now:
        end = min(start + PAGE_LIMIT * PERIOD_MS[period], now)
        page = method(
            {
                "symbol": symbol,
                "period": period,
                "limit": PAGE_LIMIT,
                "startTime": start,
                "endTime": end,
            }
        )
        rows.extend(page)
        start = end + 1
    return _to_frame(rows)


def collect(
    pairs: list[str], periods: list[str], outdir: Path, keys: list[str] | None = None
) -> dict[str, int]:
    """Fetch new rows for every pair/period/endpoint. Returns rows added per endpoint."""
    exchange = ccxt.binanceusdm({"enableRateLimit": True})
    exchange.load_markets()
    added: dict[str, int] = {}
    for key in keys or list(ENDPOINTS):
        for pair in pairs:
            symbol = exchange.market(pair)["id"]
            for period in periods:
                path = _file(outdir, key, pair, period)
                old = pd.read_parquet(path) if path.exists() else pd.DataFrame()
                floor = exchange.milliseconds() - HISTORY_MS
                since = int(old["timestamp"].max()) + 1 if len(old) else floor
                if since < floor:
                    logger.warning(
                        "%s %s %s: gap, history before %s lost", key, pair, period, floor
                    )
                try:
                    new = fetch(exchange, key, symbol, period, max(since, floor))
                except ccxt.BaseError as e:
                    logger.warning("%s %s %s failed: %s", key, pair, period, e)
                    continue
                if new.empty:
                    continue
                merged = pd.concat([old, new], ignore_index=True)
                merged = merged.drop_duplicates("timestamp").sort_values("timestamp")
                path.parent.mkdir(parents=True, exist_ok=True)
                merged.to_parquet(path, index=False)
                added[key] = added.get(key, 0) + len(merged) - len(old)
                time.sleep(0.05)
    return added
