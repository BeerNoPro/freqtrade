"""Intraday windows around events from daily archive files (fake fetcher, no network)."""

from pathlib import Path

import numpy as np
import pandas as pd
from mce.intraday import day_key, event_days, sync_event_windows


UTC = "UTC"
PAD = pd.Timedelta(minutes=10)


def _day(symbol: str, day: pd.Timestamp) -> pd.DataFrame | None:
    """1m candles of one day, close = minutes since 2026-06-01 (to read the time back)."""
    if day == pd.Timestamp("2026-06-05", tz=UTC):
        return None  # a day the archive does not have
    dates = pd.date_range(day, periods=1440, freq="1min")
    minutes = (dates - pd.Timestamp("2026-06-01", tz=UTC)) / pd.Timedelta(minutes=1)
    c = minutes.to_numpy(dtype=float)
    return pd.DataFrame(
        {"date": dates, "open": c, "high": c, "low": c, "close": c, "volume": 1.0}
    ).assign(quote_volume=c)


def test_day_key_and_event_days() -> None:
    day = pd.Timestamp("2026-06-02", tz=UTC)
    assert day_key("AUSDT", "1m", day) == (
        "data/futures/um/daily/klines/AUSDT/1m/AUSDT-1m-2026-06-02.zip"
    )
    times = pd.Series(pd.to_datetime(["2026-06-02 00:05", "2026-06-02 12:00"], utc=True))
    # 00:05 - 10 min reaches back into 06-01
    assert event_days(times, PAD) == [pd.Timestamp("2026-06-01", tz=UTC), day]


def test_sync_event_windows_keeps_only_the_minutes_around_events(tmp_path: Path) -> None:
    calls: list[pd.Timestamp] = []

    def fetch(symbol: str, day: pd.Timestamp) -> pd.DataFrame | None:
        calls.append(day)
        return _day(symbol, day)

    times = pd.Series(pd.to_datetime(["2026-06-02 00:05", "2026-06-03 08:00"], utc=True))
    added = sync_event_windows("AUSDT", times, tmp_path, PAD, fetch)
    stored = pd.read_parquet(tmp_path / "klines_1m_events" / "AUSDT.parquet")
    assert added == len(stored) == 2 * 21  # 21 minutes around each event
    assert sorted(set(calls)) == [
        pd.Timestamp(d, tz=UTC) for d in ("2026-06-01", "2026-06-02", "2026-06-03")
    ]
    first = stored[stored["date"] < pd.Timestamp("2026-06-03", tz=UTC)]
    assert first["date"].min() == pd.Timestamp("2026-06-01 23:55", tz=UTC)  # across midnight
    assert first["date"].max() == pd.Timestamp("2026-06-02 00:15", tz=UTC)
    minutes = (stored["date"] - pd.Timestamp("2026-06-01", tz=UTC)) / pd.Timedelta(minutes=1)
    assert np.allclose(stored["close"], minutes)
    # a second run fetches nothing new; a missing archive day is skipped without error
    calls.clear()
    assert sync_event_windows("AUSDT", times, tmp_path, PAD, fetch) == 0 and calls == []
    late = pd.Series(pd.to_datetime(["2026-06-05 12:00"], utc=True))
    assert sync_event_windows("AUSDT", late, tmp_path, PAD, fetch) == 0
