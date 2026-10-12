"""Point-in-time frames: snapshot timing, universe membership and causality."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from mce.event_study import StudyConfig, pair_events
from mce.hooks import Hook
from mce.pit import (
    bar_membership,
    coin_frame,
    daily_members,
    load_frames,
    read_store,
    snapshot_before,
    synthetic_store,
)


UTC = "UTC"
BAR4 = pd.Timedelta(hours=4)


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


# ------------------------------------------------------------------ dead and halted candles

DELIST_LAST = pd.Timestamp("2025-03-12 08:00", tz=UTC)  # last traded 4h candle of the dead coin
HALT = (pd.Timestamp("2025-02-10", tz=UTC), pd.Timestamp("2025-02-13", tz=UTC))  # 3 days
END = pd.Timestamp("2025-06-01", tz=UTC)


def _kill(pit: Path, symbol: str, first: pd.Timestamp, last: pd.Timestamp | None) -> None:
    """Turn the 4h candles in [first, last) into Binance's fake candles (flat at the previous
    close, zero volume) and rebuild the daily candles from the 4h ones as synthetic_store does."""
    path = pit / "klines_4h" / f"{symbol}.parquet"
    k = pd.read_parquet(path)
    dead = (k["date"] >= first) & ((k["date"] < last) if last is not None else True)
    flat = k.loc[k["date"] < first, "close"].iloc[-1]
    k.loc[dead, ["open", "high", "low", "close"]] = flat
    k.loc[dead, ["volume", "quote_volume", "taker_buy_quote"]] = 0.0
    k.to_parquet(path, index=False)
    day = k.set_index("date").resample("1D")
    d = pd.DataFrame(
        {
            "open": day["open"].first(),
            "high": day["high"].max(),
            "low": day["low"].min(),
            "close": day["close"].last(),
            "volume": day["volume"].sum(),
            "quote_volume": day["quote_volume"].sum(),
        }
    ).reset_index()
    d.to_parquet(pit / "klines_1d" / f"{symbol}.parquet", index=False)


@pytest.fixture(scope="module")
def dead_store(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, pd.DataFrame, str, str]:
    """(store, membership before the fake candles, delisted coin, halted coin). Both coins are
    members right after the change, so the change has something to remove."""
    pit = tmp_path_factory.mktemp("pit_dead")
    synthetic_store(pit, n_coins=24, start="2024-06-01", end="2025-06-01", seed=3)
    before = daily_members(pit, END)
    after_delist = before.loc["2025-03-13":"2025-03-30"].all()
    halted_after = before.loc["2025-02-10":"2025-02-12"].all() & before.loc["2025-03"].any()
    dead = str(after_delist[after_delist].index[0])
    halted = str(halted_after[halted_after & (halted_after.index != dead)].index[0])
    _kill(pit, dead, DELIST_LAST + pd.Timedelta(hours=4), None)
    _kill(pit, halted, *HALT)
    return pit, before, dead, halted


def test_a_delisted_coin_leaves_the_universe_and_its_fake_rows_are_gone(dead_store) -> None:
    pit, before, dead, _ = dead_store
    after = daily_members(pit, END)
    last_day = DELIST_LAST.floor("D")
    assert not after.loc[last_day + pd.Timedelta(days=1) :, dead].any()
    pd.testing.assert_series_equal(after.loc[:last_day, dead], before.loc[:last_day, dead])
    f = coin_frame(pit, dead, "4h", END, after)
    assert f["date"].max() == DELIST_LAST  # the fake tail is not read
    funding = read_store(pit, "funding", dead, END)
    assert funding["date"].max() <= DELIST_LAST + pd.Timedelta(hours=4)
    stored = pd.read_parquet(pit / "funding" / f"{dead}.parquet")["date"].max()
    assert stored > DELIST_LAST + pd.Timedelta(days=30)  # the raw file keeps the fake prints
    # accepted consequence: a position open when trading ends has no outcome
    hook = Hook(
        "always_long", 1, "test", lambda d, p: pd.Series(1, index=d.index), {}, 3, cooldown=1
    )
    cfg = StudyConfig(start=pd.Timestamp("2025-03-01", tz=UTC), end=END, horizons=(1, 3))
    ev = pair_events(hook, dead, f, cfg).set_index("date")
    assert np.isnan(ev.loc[DELIST_LAST, "ret_1"]) and np.isnan(ev.loc[DELIST_LAST, "tb_ret_r"])
    assert np.isfinite(ev.loc[DELIST_LAST - 3 * BAR4, "ret_3"])


def test_a_halt_is_kept_in_the_price_path_but_never_counts(dead_store) -> None:
    pit, before, _, halted = dead_store
    after = daily_members(pit, END)
    halt_days = pd.date_range(HALT[0], HALT[1] - pd.Timedelta(days=1), freq="D")
    assert not after.loc[halt_days, halted].any()
    assert after.loc["2025-02-13":"2025-02-27", halted].all()  # the January ranking still holds
    # 16 traded days at the February ranking (< 90), applied from 02-28 itself: out until the
    # May ranking, when it has 108
    assert before.loc["2025-02-28":"2025-05-30", halted].any()
    assert not after.loc["2025-02-28":"2025-05-30", halted].any()
    f = coin_frame(pit, halted, "4h", END, after)
    in_halt = (f["date"] >= HALT[0]) & (f["date"] < HALT[1])
    assert in_halt.sum() == 18 and not f.loc[in_halt, "active"].any()
    assert (f["close"].diff().loc[in_halt] == 0).iloc[1:].all()  # flat, rows kept in place
    # the halted bars never enter the regime baseline: an event study sees only active bars
    hook = Hook("always_long", 1, "test", lambda d, p: pd.Series(1, index=d.index), {}, 1)
    cfg = StudyConfig(start=pd.Timestamp("2025-02-01", tz=UTC), end=END, horizons=(1,))
    ev = pair_events(hook, halted, f, cfg)
    assert not ev["date"].between(HALT[0], HALT[1], inclusive="left").any()


def test_a_cut_inside_a_halt_gives_the_same_members_and_active_bars(dead_store) -> None:
    pit, _, _, halted = dead_store
    full_members = daily_members(pit, END)
    full = coin_frame(pit, halted, "4h", END, full_members)
    cut = pd.Timestamp("2025-02-11 12:00", tz=UTC)
    members = daily_members(pit, cut)
    pd.testing.assert_frame_equal(members, full_members.loc[members.index, members.columns])
    part = coin_frame(pit, halted, "4h", cut, members)
    assert part["date"].max() == HALT[0] - BAR4  # the halt is the tail of what is known at cut
    head = full[full["date"] < cut]
    cols = ["close", "atr", "shock", "oi_z", "regime", "active"]
    pd.testing.assert_frame_equal(part[cols], head[cols].iloc[: len(part)].reset_index(drop=True))
    assert not head["active"].iloc[len(part) :].any()  # rows only the full read has: inactive
