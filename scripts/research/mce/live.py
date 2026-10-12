"""
Live updates of the point-in-time store (``mce.pit``) for forward tests, and quotes at decision
time. Public Binance endpoints only; ``exchange`` is a ccxt ``binanceusdm`` (tests pass a fake).

Same sources as the research store: closed klines from ``/fapi/v1/klines``
(``mce.archive.sync_klines``) and open interest from ``/futures/data/openInterestHist``
(period 5m). That endpoint holds exactly the values of the archive's daily ``metrics`` files but
stamps each one 5 minutes later: the archive's 15:50 snapshot is the API's 15:55 row (20 coins
x 25 days, 2026-10-12: all equal after the shift, up to 3% apart without it). Rows are
re-stamped like the archive before use; the 15:55 row is published by about 15:58, before the
16:00 decision that uses it. The API keeps about 30 days and only answers ``startTime``
together with ``endTime``; ``metrics_api`` only bridges the days the archive has not published
yet (``mce.pit.open_interest`` prefers the archive).
"""

import logging
import time
from collections.abc import Callable
from pathlib import Path

import ccxt
import numpy as np
import pandas as pd

from mce.archive import _append
from mce.pit import open_interest


logger = logging.getLogger(__name__)

API_TO_ARCHIVE = pd.Timedelta(minutes=-5)  # archive date = API timestamp + this
SNAPSHOT_MINUTE = 50  # the hourly snapshot kept by mce.archive.parse_metrics_csv
OI_STEP = pd.Timedelta(minutes=5)
OI_PAGE = 500
OI_WINDOW = OI_STEP * (OI_PAGE - 4)  # one request, with a margin
OI_HISTORY = pd.Timedelta(days=29)  # the API keeps about 30 days
OI_KEEP = pd.Timedelta(days=35)  # older bridge rows are dropped: the archive has them by then
DEPTH_LEVELS = 100


def _ms(t: pd.Timestamp) -> int:
    return int(t.timestamp() * 1000)


def with_retry(fn: Callable, *args, tries: int = 3, pause: float = 2.0):
    """``fn(*args)``, retried after a network error or timeout, the pause doubling each time.
    Exchange-side rejections (bad symbol...) are not retried."""
    for attempt in range(tries):
        try:
            return fn(*args)
        except ccxt.NetworkError as e:  # includes RequestTimeout
            if attempt == tries - 1:
                raise
            logger.warning("retrying %s after %r", getattr(fn, "__name__", fn), e)
            time.sleep(pause * 2**attempt)
    raise AssertionError("unreachable")


def oi_api_to_snapshots(rows: list[dict]) -> pd.DataFrame:
    """openInterestHist rows -> hourly snapshots stamped like the archive (date, oi_value)."""
    if not rows:
        return pd.DataFrame(columns=["date", "oi_value"])
    raw = pd.DataFrame(rows)
    date = pd.to_datetime(raw["timestamp"].astype("int64"), unit="ms", utc=True) + API_TO_ARCHIVE
    value = pd.to_numeric(raw["sumOpenInterestValue"], errors="coerce")
    out = pd.DataFrame({"date": date, "oi_value": value})
    return out[out["date"].dt.minute == SNAPSHOT_MINUTE].reset_index(drop=True)


def sync_open_interest(exchange, symbol: str, pit: Path, now: pd.Timestamp) -> int:
    """Append the API snapshots newer than every stored one (archive or API) to
    ``<pit>/metrics_api/<symbol>.parquet``. Returns rows added."""
    path = pit / "metrics_api" / f"{symbol}.parquet"
    old = pd.read_parquet(path) if path.exists() else pd.DataFrame()
    stored = open_interest(pit, symbol, now)
    since = now - OI_HISTORY
    if stored is not None and len(stored):
        since = max(since, stored["date"].max() - API_TO_ARCHIVE + OI_STEP)
    rows: list[dict] = []
    while since < now:
        until = min(since + OI_WINDOW, now)
        params = {"symbol": symbol, "period": "5m", "limit": OI_PAGE}
        params |= {"startTime": _ms(since), "endTime": _ms(until)}
        rows += with_retry(exchange.fapiDataGetOpenInterestHist, params)
        since = until + pd.Timedelta(milliseconds=1)
    new = oi_api_to_snapshots(rows)
    if len(old):
        old = old[old["date"] >= now - OI_KEEP]
    return _append(path, old, new[new["oi_value"] > 0])


def impact_bps(levels: list, notional: float, mid: float, side: int) -> float:
    """Distance of the average fill of a market order worth ``notional`` (quote currency) from
    the mid price, in basis points: half the spread plus the book impact. ``levels`` is the side
    the order takes (asks for a buy), best first. NaN when the book is too thin."""
    left, paid, qty = notional, 0.0, 0.0
    for price, size in levels:
        p = float(price)
        take = min(left, p * float(size))
        paid, qty, left = paid + take, qty + take / p, left - take
        if left <= 1e-9:
            break
    if left > 1e-9 or qty == 0 or mid <= 0:
        return float("nan")
    return float(side * (paid / qty - mid) / mid * 1e4)


def quote(exchange, symbol: str, side: int, notional: float) -> dict[str, float]:
    """Best bid / ask and the entry impact of ``notional`` in ``side`` direction, now."""
    book = with_retry(exchange.fapiPublicGetTickerBookTicker, {"symbol": symbol})
    depth = with_retry(exchange.fapiPublicGetDepth, {"symbol": symbol, "limit": DEPTH_LEVELS})
    bid, ask = float(book["bidPrice"]), float(book["askPrice"])
    levels = depth["asks"] if side > 0 else depth["bids"]
    return {"bid": bid, "ask": ask, "slip_bps": impact_bps(levels, notional, (bid + ask) / 2, side)}


def round_trip_cost(fee_round_trip: float, slip_bps: float | None) -> float:
    """Cost of entering and leaving as a fraction of price: fees plus the entry impact counted
    twice (the exit is assumed to cost the same). Unknown impact counts as zero."""
    slip = 0.0 if slip_bps is None or not np.isfinite(slip_bps) else max(slip_bps, 0.0) / 1e4
    return fee_round_trip + 2 * slip
