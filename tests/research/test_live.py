"""Live store updates: API open interest re-stamped like the archive, quotes, no network."""

from pathlib import Path

import ccxt
import mce.live
import numpy as np
import pandas as pd
import pytest
from mce.live import (
    impact_bps,
    oi_api_to_snapshots,
    quote,
    round_trip_cost,
    sync_open_interest,
    with_retry,
)
from mce.pit import coin_frame, daily_members, open_interest, synthetic_store


UTC = "UTC"


def _value(ts_ms: int) -> float:
    return 1e7 + ts_ms / 1e6


class FakeExchange:
    """openInterestHist over [startTime, endTime] every 5 minutes, at most ``limit`` rows."""

    def __init__(self, now: pd.Timestamp) -> None:
        self.now = now
        self.calls: list[dict] = []

    def fapiDataGetOpenInterestHist(self, params: dict) -> list[dict]:
        self.calls.append(params)
        start = pd.Timestamp(params["startTime"], unit="ms", tz=UTC).ceil("5min")
        end = min(pd.Timestamp(params["endTime"], unit="ms", tz=UTC), self.now)
        stamps = pd.date_range(start, end, freq="5min")[: params["limit"]]
        return [
            {
                "timestamp": int(t.timestamp() * 1000),
                "sumOpenInterestValue": str(_value(int(t.timestamp() * 1000))),
            }
            for t in stamps
        ]

    def fapiPublicGetTickerBookTicker(self, params: dict) -> dict:
        return {"symbol": params["symbol"], "bidPrice": "99.9", "askPrice": "100.1"}

    def fapiPublicGetDepth(self, params: dict) -> dict:
        return {
            "asks": [["100.1", "10"], ["100.3", "100"]],
            "bids": [["99.9", "10"], ["99.5", "100"]],
        }


def test_oi_rows_are_restamped_like_the_archive() -> None:
    stamps = pd.date_range("2026-10-10 12:40", periods=4, freq="5min", tz=UTC)
    rows = [
        {"timestamp": int(t.timestamp() * 1000), "sumOpenInterestValue": f"{i}"}
        for i, t in enumerate(stamps)
    ]
    out = oi_api_to_snapshots(rows)
    # API 12:55 is the archive's 12:50 snapshot; the other minutes are dropped
    assert out["date"].tolist() == [pd.Timestamp("2026-10-10 12:50", tz=UTC)]
    assert out["oi_value"].tolist() == [3.0]


def test_sync_open_interest_pages_and_is_incremental(tmp_path: Path) -> None:
    now = pd.Timestamp("2026-10-12 16:01:30", tz=UTC)
    ex = FakeExchange(now)
    added = sync_open_interest(ex, "AUSDT", tmp_path, now)
    stored = pd.read_parquet(tmp_path / "metrics_api" / "AUSDT.parquet")
    assert added == len(stored) > 24 * 28 and len(ex.calls) > 10  # ~29 days, several pages
    assert (stored["date"].dt.minute == 50).all() and stored["date"].is_unique
    api_time = stored["date"] + pd.Timedelta(minutes=5)
    assert np.allclose(stored["oi_value"], [_value(int(t.timestamp() * 1000)) for t in api_time])
    assert stored["date"].max() == pd.Timestamp("2026-10-12 15:50", tz=UTC)  # API 15:55 row
    ex.calls.clear()
    assert sync_open_interest(ex, "AUSDT", tmp_path, now) == 0
    assert len(ex.calls) == 1  # only the last few minutes are asked again
    later = now + pd.Timedelta(hours=4)
    ex.now = later
    assert sync_open_interest(ex, "AUSDT", tmp_path, later) == 4


def test_open_interest_prefers_the_archive(tmp_path: Path) -> None:
    for kind, dates, values in (
        ("metrics", ["2026-01-01 00:50", "2026-01-01 01:50"], [1.0, 2.0]),
        ("metrics_api", ["2026-01-01 01:50", "2026-01-01 02:50"], [9.0, 3.0]),
    ):
        (tmp_path / kind).mkdir()
        pd.DataFrame({"date": pd.to_datetime(dates, utc=True), "oi_value": values}).to_parquet(
            tmp_path / kind / "AUSDT.parquet", index=False
        )
    got = open_interest(tmp_path, "AUSDT", pd.Timestamp("2026-01-02", tz=UTC))
    assert got["oi_value"].tolist() == [1.0, 2.0, 3.0]
    assert open_interest(tmp_path, "BUSDT", pd.Timestamp("2026-01-02", tz=UTC)) is None


def test_bridge_rows_give_the_same_frame_as_the_archive(tmp_path: Path) -> None:
    synthetic_store(tmp_path, n_coins=22, start="2024-06-01", end="2025-03-01", seed=5)
    end = pd.Timestamp("2025-03-01", tz=UTC)
    members = daily_members(tmp_path, end)
    symbol = members.columns[members.any()][0]
    before = coin_frame(tmp_path, symbol, "4h", end, members)
    path = tmp_path / "metrics" / f"{symbol}.parquet"
    m = pd.read_parquet(path)
    cut = end - pd.Timedelta(days=3)
    (tmp_path / "metrics_api").mkdir()
    m[m["date"] >= cut - pd.Timedelta(days=1)][["date", "oi_value"]].to_parquet(
        tmp_path / "metrics_api" / f"{symbol}.parquet", index=False
    )
    m[m["date"] < cut].to_parquet(path, index=False)  # archive not yet published for 3 days
    after = coin_frame(tmp_path, symbol, "4h", end, members)
    cols = ["oi_now", "oi_prev", "oi_z", "shock", "active"]
    pd.testing.assert_frame_equal(before[cols], after[cols])


def test_with_retry_retries_network_errors_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mce.live.time, "sleep", lambda s: None)
    calls = []

    def flaky(x: int) -> int:
        calls.append(x)
        if len(calls) < 3:
            raise ccxt.RequestTimeout("slow")
        return x * 2

    assert with_retry(flaky, 21) == 42 and len(calls) == 3
    calls.clear()
    with pytest.raises(ccxt.RequestTimeout):
        with_retry(flaky, 1, tries=2)

    def rejected(x: int) -> int:
        calls.append(x)
        raise ccxt.BadSymbol("no such symbol")

    calls.clear()
    with pytest.raises(ccxt.BadSymbol):
        with_retry(rejected, 1)
    assert len(calls) == 1


def test_impact_and_quote() -> None:
    asks = [["100.1", "10"], ["100.3", "100"]]
    # 1001 USDT fills on the best ask only: 10 bps above a 100.0 mid
    assert impact_bps(asks, 1001.0, 100.0, 1) == pytest.approx(10.0)
    deeper = impact_bps(asks, 3000.0, 100.0, 1)
    assert 10.0 < deeper < 30.0
    assert np.isnan(impact_bps(asks, 1e9, 100.0, 1))  # thinner than the order
    q = quote(FakeExchange(pd.Timestamp("2026-01-01", tz=UTC)), "AUSDT", -1, 999.0)
    assert q["bid"] == 99.9 and q["ask"] == 100.1 and q["slip_bps"] == pytest.approx(10.0)
    assert round_trip_cost(0.0012, 10.0) == pytest.approx(0.0032)
    assert round_trip_cost(0.0012, None) == round_trip_cost(0.0012, float("nan")) == 0.0012
