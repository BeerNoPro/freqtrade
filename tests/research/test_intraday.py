"""Intraday windows around events from daily archive files (fake fetcher, no network)."""

import io
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from mce import intraday
from mce.intraday import (
    archive_day_key,
    day_key,
    event_days,
    fetch_archive_day,
    parse_agg_trades,
    parse_book_depth,
    sync_event_windows,
)


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


AGG_HEADER = (
    "agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker\n"
)
AGG_ROWS = "7,0.19939,633.0,10,11,1791763200009,false\n8,0.19938,136.9,12,12,1791763200251,true\n"


@pytest.mark.parametrize("text", [AGG_HEADER + AGG_ROWS, AGG_ROWS])
def test_parse_agg_trades_with_or_without_header(text: str) -> None:
    t = parse_agg_trades(text)
    assert list(t.columns) == ["date", "agg_trade_id", "price", "quantity", "is_buyer_maker"]
    assert t["date"].iloc[0] == pd.Timestamp(1791763200009, unit="ms", tz="UTC")
    assert list(t["agg_trade_id"]) == [7, 8] and list(t["is_buyer_maker"]) == [False, True]
    assert t["price"].iloc[1] == 0.19938 and t["quantity"].iloc[0] == 633.0


def test_parse_agg_trades_microseconds() -> None:
    t = parse_agg_trades("7,1.0,2.0,1,1,1791763200009123,True\n")
    assert t["date"].iloc[0] == pd.Timestamp(1791763200009, unit="ms", tz="UTC")


def test_parse_book_depth() -> None:
    text = (
        "timestamp,percentage,depth,notional\n"
        "2026-10-01 00:00:01,-0.2,699167.7,141657.8\n"
        "2026-10-01 00:00:01,0.2,544187.5,110540.2\n"
    )
    b = parse_book_depth(text)
    assert list(b.columns) == ["date", "percentage", "depth", "notional"]
    assert b["date"].iloc[0] == pd.Timestamp("2026-10-01 00:00:01", tz="UTC")
    assert list(b["percentage"]) == [-0.2, 0.2] and b["notional"].iloc[1] == 110540.2
    assert parse_book_depth(text.split("\n", 1)[1]).equals(b)  # header optional


class _Resp:
    def __init__(self, status: int, content: bytes = b"") -> None:
        self.status_code, self.content = status, content

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def test_fetch_archive_day_parses_the_zip_and_maps_404_to_none(monkeypatch) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("ARBUSDT-aggTrades-2026-10-12.csv", AGG_HEADER + AGG_ROWS)
    urls: list[str] = []

    def get(url: str, timeout: float) -> _Resp:
        urls.append(url)
        return _Resp(404) if "2026-10-13" in url else _Resp(200, buf.getvalue())

    monkeypatch.setattr(intraday.requests, "get", get)
    day = pd.Timestamp("2026-10-12", tz="UTC")
    t = fetch_archive_day("aggTrades", "ARBUSDT", day, parse_agg_trades)
    assert t is not None and len(t) == 2
    assert urls[0].endswith(archive_day_key("aggTrades", "ARBUSDT", day))
    assert archive_day_key("bookDepth", "ARBUSDT", day) == (
        "data/futures/um/daily/bookDepth/ARBUSDT/ARBUSDT-bookDepth-2026-10-12.zip"
    )
    nxt = day + pd.Timedelta(days=1)
    assert fetch_archive_day("aggTrades", "ARBUSDT", nxt, parse_agg_trades) is None


def test_fetch_archive_day_retries_a_dropped_connection(monkeypatch) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("x.csv", AGG_ROWS)
    calls: list[str] = []

    def get(url: str, timeout: float) -> _Resp:
        calls.append(url)
        if len(calls) < 3:
            raise intraday.requests.ConnectionError("Remote end closed connection")
        return _Resp(200, buf.getvalue())

    monkeypatch.setattr(intraday.requests, "get", get)
    monkeypatch.setattr(intraday.time, "sleep", lambda s: None)
    day = pd.Timestamp("2026-10-12", tz="UTC")
    t = fetch_archive_day("aggTrades", "ARBUSDT", day, parse_agg_trades)
    assert t is not None and len(t) == 2 and len(calls) == 3
    calls.clear()

    def down(url: str, timeout: float) -> _Resp:
        calls.append(url)
        raise intraday.requests.ConnectionError("down")

    monkeypatch.setattr(intraday.requests, "get", down)
    with pytest.raises(intraday.requests.ConnectionError):
        fetch_archive_day("aggTrades", "ARBUSDT", day, parse_agg_trades)
    assert len(calls) == intraday.RETRIES
