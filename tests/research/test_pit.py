"""Point-in-time frames: snapshot timing, universe membership and causality."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from mce.pit import (
    bar_membership,
    coin_frame,
    daily_members,
    load_frames,
    snapshot_before,
    synthetic_store,
)


UTC = "UTC"


def test_snapshot_before_skips_same_instant_and_stale_values() -> None:
    snaps = pd.DataFrame(
        {
            "date": pd.to_datetime(["2025-01-01 00:50", "2025-01-01 03:50"], utc=True),
            "oi_value": [10.0, 20.0],
        }
    )
    times = pd.Series(
        pd.to_datetime(["2025-01-01 03:50", "2025-01-01 04:00", "2025-01-01 09:00"], utc=True)
    )
    got = snapshot_before(times, snaps, "oi_value", pd.Timedelta(hours=4))
    # 03:50 itself is not visible at 03:50; at 09:00 the last snapshot is 5h10m old (> 4h)
    assert got[0] == 10.0 and got[1] == 20.0 and np.isnan(got[2])


def test_bar_membership_uses_the_last_closed_daily_ranking() -> None:
    days = pd.date_range("2025-01-01", periods=3, freq="D", tz=UTC)
    members = pd.DataFrame({"AUSDT": [False, True, False]}, index=days)
    decision = pd.Series(
        pd.to_datetime(["2025-01-02 04:00", "2025-01-03 00:00", "2025-01-03 04:00"], utc=True)
    )
    # 01-02 04:00 sees the ranking of 01-01; 01-03 00:00 sees 01-02 (closed at that instant)
    assert bar_membership(members, "AUSDT", decision).tolist() == [False, True, True]
    assert bar_membership(members, "BUSDT", decision).tolist() == [False, False, False]


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> Path:
    pit = tmp_path_factory.mktemp("pit")
    synthetic_store(pit, n_coins=24, start="2024-06-01", end="2025-06-01", seed=3)
    return pit


def test_coin_frame_is_causal_and_ignores_zero_open_interest(store: Path) -> None:
    end = pd.Timestamp("2025-06-01", tz=UTC)
    members = daily_members(store, end)
    symbol = members.columns[members.any()][0]
    metrics = store / "metrics" / f"{symbol}.parquet"
    m = pd.read_parquet(metrics)
    m.loc[3000, "oi_value"] = 0.0  # the archive has a few zero prints
    m.to_parquet(metrics, index=False)
    full = coin_frame(store, symbol, "4h", end, members)
    zero_time = m.loc[3000, "date"]
    after = full[full["time"] > zero_time].iloc[0]
    assert np.isfinite(after["oi_now"]) and after["oi_now"] > 0
    cols = ["close", "atr", "shock", "oi_now", "oi_prev", "oi_z", "regime", "active"]
    for cut in ("2024-11-03 08:00", "2025-02-17", "2025-04-30 20:00"):
        t = pd.Timestamp(cut, tz=UTC)
        part = coin_frame(store, symbol, "4h", t, daily_members(store, t))
        head = full[full["date"] < t]
        assert len(part) == len(head)
        pd.testing.assert_frame_equal(part[cols], head[cols].reset_index(drop=True))


def test_load_frames_keeps_coins_active_in_the_window(store: Path) -> None:
    start, end = pd.Timestamp("2025-01-01", tz=UTC), pd.Timestamp("2025-06-01", tz=UTC)
    frames = load_frames(store, "4h", start, end)
    members = daily_members(store, end)
    assert 20 <= len(frames) <= 24
    for symbol, f in frames.items():
        assert (f["active"] & (f["date"] >= start)).any()
        day = f["time"].dt.floor("D") - pd.Timedelta(days=1)
        expect = members[symbol].reindex(day).fillna(False).to_numpy(dtype=bool)
        assert (f["active"].to_numpy() == expect).all()
    per_bar = pd.concat([f.set_index("date")["active"] for f in frames.values()], axis=1)
    assert per_bar.loc[start:].sum(axis=1).max() <= 20
