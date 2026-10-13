"""
Intraday candles around events (funding settlements and other scheduled times) from the daily kline
files of data.binance.vision. Only the days touched by an event are fetched, and only the minutes
within ``pad`` of an event are kept, in ``<store>/<kind>/<SYMBOL>.parquet`` (default kind
``klines_1m_events``, which the store downloads, ``prune`` and the runner never touch). The fetcher
is passed in, so the tests run without network.

Other daily archive files (``aggTrades``: every taker order with its side; ``bookDepth``: notional
within ±0.2/1/2/3/4/5% of the price every 30 s) are read with ``fetch_archive_day`` and the
parsers below; ``mce.costs`` turns them into spread and book impact.
"""

import io
import time
import zipfile
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from mce.archive import ARCHIVE, parse_archive_klines


DAILY = "data/futures/um/daily/"
DAILY_KLINES = DAILY + "klines/"
KIND = "klines_1m_events"
RETRIES = 4
DayFetcher = Callable[[str, pd.Timestamp], pd.DataFrame | None]
AGG_TRADE_COLS = [
    "agg_trade_id",
    "price",
    "quantity",
    "first_trade_id",
    "last_trade_id",
    "transact_time",
    "is_buyer_maker",
]
BOOK_DEPTH_COLS = ["timestamp", "percentage", "depth", "notional"]


def day_key(symbol: str, interval: str, day: pd.Timestamp) -> str:
    """Archive key of one day of candles."""
    return f"{DAILY_KLINES}{symbol}/{interval}/{symbol}-{interval}-{day:%Y-%m-%d}.zip"


def archive_day_key(dataset: str, symbol: str, day: pd.Timestamp) -> str:
    """Archive key of one day of a per-symbol dataset without interval (aggTrades, bookDepth)."""
    return f"{DAILY}{dataset}/{symbol}/{symbol}-{dataset}-{day:%Y-%m-%d}.zip"


def event_days(times: pd.Series, pad: pd.Timedelta) -> list[pd.Timestamp]:
    """Calendar days (UTC) touched by the windows [t - pad, t + pad]."""
    lo, hi = (times - pad).dt.floor("D"), (times + pad).dt.floor("D")
    return sorted(set(lo) | set(hi))


def _zip_csv(key: str, timeout: float) -> str | None:
    """Text of the CSV inside one archive zip, None when the archive has no such file. A dropped
    connection or a timeout is retried ``RETRIES`` times in all, with a growing pause."""
    for attempt in range(1, RETRIES + 1):
        try:
            resp = requests.get(ARCHIVE + key, timeout=timeout)
            break
        except (requests.ConnectionError, requests.Timeout):
            if attempt == RETRIES:
                raise
            time.sleep(2.0 * attempt)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        return z.read(z.namelist()[0]).decode()


def fetch_day(symbol: str, day: pd.Timestamp, interval: str = "1m", timeout: float = 30):
    """One day of candles from the archive, None when the archive has no such file."""
    text = _zip_csv(day_key(symbol, interval, day), timeout)
    return None if text is None else parse_archive_klines(text)


def fetch_archive_day(
    dataset: str,
    symbol: str,
    day: pd.Timestamp,
    parse: Callable[[str], pd.DataFrame],
    timeout: float = 60,
) -> pd.DataFrame | None:
    """One day of ``dataset`` parsed by ``parse``, None when the archive has no such file."""
    text = _zip_csv(archive_day_key(dataset, symbol, day), timeout)
    return None if text is None else parse(text)


def parse_agg_trades(text: str) -> pd.DataFrame:
    """Daily aggTrades CSV (header optional, time in ms or us) -> date, agg_trade_id, price,
    quantity, is_buyer_maker (True: the seller was the taker). One row per taker order and price."""
    raw = pd.read_csv(io.StringIO(text), header=None, names=AGG_TRADE_COLS, dtype=str)
    raw = raw[raw["agg_trade_id"].str.isdigit()]
    t = raw["transact_time"].astype("int64")
    t = t.where(t < 10**14, t // 1000)
    return pd.DataFrame(
        {
            "date": pd.to_datetime(t, unit="ms", utc=True),
            "agg_trade_id": raw["agg_trade_id"].astype("int64"),
            "price": raw["price"].astype(float),
            "quantity": raw["quantity"].astype(float),
            "is_buyer_maker": raw["is_buyer_maker"].str.lower() == "true",
        }
    ).reset_index(drop=True)


def parse_book_depth(text: str) -> pd.DataFrame:
    """Daily bookDepth CSV (header optional) -> date (UTC), percentage (negative: bids),
    depth (base units) and notional (quote units), cumulative from the price out to percentage."""
    raw = pd.read_csv(io.StringIO(text), header=None, names=BOOK_DEPTH_COLS, dtype=str)
    raw = raw[raw["timestamp"] != "timestamp"]
    out = raw[["percentage", "depth", "notional"]].astype(float)
    out.insert(0, "date", pd.to_datetime(raw["timestamp"], utc=True))
    return out.reset_index(drop=True)


def _in_windows(dates: pd.Series, times: pd.Series, pad: pd.Timedelta) -> pd.Series:
    """Dates within ``pad`` of the nearest event time."""
    t = pd.DatetimeIndex(times.sort_values())
    d = pd.DatetimeIndex(dates)
    i = t.searchsorted(d, side="left")  # first event at or after each date
    after = t[i.clip(max=len(t) - 1)]
    before = t[(i - 1).clip(min=0)]
    gap = np.minimum(abs(after - d).to_numpy(), abs(d - before).to_numpy())
    return pd.Series(gap <= pad.to_timedelta64(), index=dates.index)


def sync_event_windows(
    symbol: str,
    times: pd.Series,
    outdir: Path,
    pad: pd.Timedelta,
    fetch: DayFetcher = fetch_day,
    kind: str = KIND,
) -> int:
    """Store the 1-minute candles within ``pad`` of each event time of ``symbol``. Events whose
    window is already complete in the store are skipped; a day missing from the archive is
    skipped. Returns rows added."""
    path = outdir / kind / f"{symbol}.parquet"
    old = pd.read_parquet(path) if path.exists() else pd.DataFrame(columns=["date"])
    have = set(pd.DatetimeIndex(old["date"]))
    minute = pd.Timedelta(minutes=1)
    todo = pd.Series(
        [t for t in times if not set(pd.date_range(t - pad, t + pad, freq=minute)) <= have],
        dtype="datetime64[ns, UTC]",
    )
    if todo.empty:
        return 0
    parts = [fetch(symbol, day) for day in event_days(todo, pad)]
    parts = [p for p in parts if p is not None and len(p)]
    if not parts:
        return 0
    new = pd.concat(parts, ignore_index=True)
    new = new[_in_windows(new["date"], todo, pad)]
    merged = pd.concat([old, new], ignore_index=True) if len(old) else new
    merged = merged.drop_duplicates("date", keep="last").sort_values("date")
    path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(path, index=False)
    return len(merged) - len(old)
