"""Forward-test rules: setups, Donchian ensemble, hook events and the dose-response test."""

import numpy as np
import pandas as pd
import pytest
from mce.forward import (
    SetupPolicy,
    assign_bins,
    barrier_levels,
    bin_trend_test,
    donchian_ensemble,
    donchian_state,
    holm,
    hook_events,
    leverage_cap,
    make_setup,
    merge_sparse_bins,
    trend_trades,
    trend_weights,
)
from mce.hooks import Hook
from mce.pit import load_frames, synthetic_store


BAR = pd.Timedelta(hours=4)
DAY = pd.Timestamp("2026-01-01", tz="UTC")


def _setup(stop: float, quote: dict | None = None):
    return make_setup(
        SetupPolicy(),
        strategy="s@v1",
        fingerprint="f",
        source="live",
        pair="AUSDT",
        timeframe="4h",
        candle_date=DAY,
        bar=BAR,
        side=1,
        entry=100.0,
        atr=2.0,
        stop=stop,
        target=None,
        max_bars=3,
        reason="r",
        quote=quote,
    )


def test_leverage_and_setup_sizing() -> None:
    p = SetupPolicy()
    assert leverage_cap(0.02, p) == 5 and leverage_cap(0.10, p) == 4 and leverage_cap(0.4, p) == 1
    s = _setup(98.0)
    assert s.qty == pytest.approx(50) and s.notional == pytest.approx(5000)  # 1% of 10k at 2 away
    assert s.cost_r == pytest.approx(0.06) and s.status == "setup"
    assert s.decided_at == "2026-01-01T04:00:00+00:00"
    tight = _setup(99.5)  # fees alone are 0.24R
    assert tight.status == "skipped_cost" and tight.cost_r == pytest.approx(0.24)
    q = _setup(98.0, {"bid": 99.9, "ask": 100.1, "slip_bps": 10.0})
    assert q.cost_r == pytest.approx((0.0012 + 0.002) * 100 / 2) and q.ask == 100.1
    very_tight = _setup(99.99)  # 1% risk would need 100x: capped at 5x notional
    assert very_tight.notional == pytest.approx(50_000) and very_tight.leverage_max == 5
    assert barrier_levels(-1, 100.0, 2.0, 1.0, 2.0) == (102.0, 96.0)


def test_donchian_state_trails_exits_and_flips() -> None:
    close = np.array([1, 2, 3, 4, 5, 4, 3, 2, 1, np.nan, 2], dtype=float)
    state, stop = donchian_state(close, 3)
    assert state[:3].tolist() == [0, 0, 0]  # no channel yet
    assert state[3] == 1 and stop[3] == 2.0  # breakout above 3, stop at the midpoint
    assert stop[4] == 3.0 and stop[5] == 4.0 and state[5] == 1  # trails up; close 4 == stop holds
    assert state[6] == -1 and stop[6] == 4.5  # close 3 < 4.5 exits and breaks below 4: short
    assert state[7] == -1 and stop[7] == 4.0  # short stop only moves down
    assert state[9] == 0 and state[10] == 0  # a missing close resets


def test_ensemble_weights_and_trades() -> None:
    idx = pd.date_range("2026-01-01", periods=12, freq="D", tz="UTC")
    up = np.r_[np.arange(1, 9), 6, 5, 4, 3].astype(float)
    close = pd.DataFrame({"A": up, "B": np.full(12, 10.0)}, index=idx)
    ens = donchian_ensemble(close, (2, 4))
    a = ens.signal["A"]
    assert a.iloc[1] == 0 and a.iloc[2] == 1.0  # only lookback 2 has history: long
    assert a.iloc[4] == 1.0 and ens.long_stop["A"].iloc[4] == pytest.approx(2.5)  # min of stops
    assert (ens.signal["B"] == 0).all()  # a flat price never breaks out
    members = pd.DataFrame(True, index=idx, columns=close.columns)
    members.iloc[:3] = False
    w = trend_weights(ens.signal, pd.DataFrame(0.5, index=idx, columns=close.columns), members, 2)
    assert w["A"].iloc[2] == 0 and w["A"].iloc[3] == pytest.approx(0.25)
    trades = trend_trades(w, ens, close)
    first = trades.iloc[0]
    assert first["date"] == idx[3] and first["side"] == 1 and first["entry"] == 4.0
    assert first["stop"] < first["entry"] and first["exit_date"] > first["date"]
    assert first["exit_price"] == close["A"].loc[first["exit_date"]]


def test_hook_events_follow_membership_and_cooldown(tmp_path) -> None:
    synthetic_store(tmp_path, n_coins=22, start="2024-06-01", end="2025-03-01", seed=2)
    start, end = pd.Timestamp("2025-01-01", tz="UTC"), pd.Timestamp("2025-03-01", tz="UTC")
    frames = load_frames(tmp_path, "4h", start, end)

    def drops(df: pd.DataFrame, p: dict) -> pd.Series:
        return pd.Series(np.where(df["shock"] <= -p["k"], 1, 0), index=df.index)

    ev = hook_events(Hook("crash", 1, "h", drops, {"k": 1.5}, horizon=3), frames, start, end)
    assert len(ev) > 0 and (ev["date"] >= start).all() and (ev["date"] < end).all()
    for pair, g in ev.groupby("pair"):
        f = frames[pair].set_index("date")
        assert f.loc[g["date"], "active"].all() and (f.loc[g["date"], "shock"] <= -1.5).all()
        assert g["date"].diff().dropna().min() >= 3 * BAR


def test_bins_merge_and_trend_test() -> None:
    assert assign_bins(np.array([0.5, -1.2, 2.0, 7.0]), (1, 2, 3)).tolist() == [-1, 0, 1, 2]
    assert merge_sparse_bins(np.array([0, 0, 1, 2, 2, -1]), 2).tolist() == [0, 0, 0, 1, 1, -1]
    rng = np.random.default_rng(1)
    bins = rng.integers(0, 5, 4000)
    weeks = np.repeat(np.arange(200), 20)
    rising = bin_trend_test(0.1 * bins + rng.normal(0, 1, 4000), bins, weeks, n_boot=500)
    assert rising["slope"] == pytest.approx(0.1, abs=0.03) and rising["lo"] > 0
    assert rising["p"] < 0.01 and sum(rising["n"]) == 4000
    flat = bin_trend_test(rng.normal(0, 1, 4000), bins, weeks, n_boot=500)
    assert flat["lo"] < 0 < flat["hi"]
    assert holm([0.01, 0.04]) == [True, True] and holm([0.03, 0.04]) == [False, False]
    assert holm([0.04, 0.01]) == [True, True] and holm([0.2, 0.01]) == [False, True]
