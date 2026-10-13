"""
Intraday candles around events (funding settlements and other scheduled times) from the daily kline
files of data.binance.vision. Only the days touched by an event are fetched, and only the minutes
within ``pad`` of an event are kept, in ``<store>/<kind>/<SYMBOL>.parquet`` (default kind
``klines_1m_events``, which the store downloads, ``prune`` and the runner never touch). The fetcher
is passed in, so the tests run without network.
"""

import io
import zipfile
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from mce.archive import ARCHIVE, parse_archive_klines


DAILY_KLINES = "data/futures/um/daily/klines/"
KIND = "klines_1m_events"
DayFetcher = Callable[[str, pd.Timestamp], pd.DataFrame | None]


def day_key(symbol: str, interval: str, day: pd.Timestamp) -> str:
    """Archive key of one day of candles."""
    return f"{DAILY_KLINES}{symbol}/{interval}/{symbol}-{interval}-{day:%Y-%m-%d}.zip"


def event_days(times: pd.Series, pad: pd.Timedelta) -> list[pd.Timestamp]:
    """Calendar days (UTC) touched by the windows [t - pad, t + pad]."""
    lo, hi = (times - pad).dt.floor("D"), (times + pad).dt.floor("D")
    return sorted(set(lo) | set(hi))


def fetch_day(symbol: str, day: pd.Timestamp, interval: str = "1m", timeout: float = 30):
    """One day of candles from the archive, None when the archive has no such file."""

    resp = requests.get(ARCHIVE + day_key(symbol, interval, day), timeout=timeout)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        return parse_archive_klines(z.read(z.namelist()[0]).decode())


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
