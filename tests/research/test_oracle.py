"""Independent reference implementation (``oracle.py``): every expected value below is worked
out by hand from the written specification, never taken from ``mce`` (that would be circular)."""

import math
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from oracle import (
    backtest,
    bar_active,
    classify_regime,
    donchian,
    ensemble,
    events_with_cooldown,
    forward_r,
    funding_sum,
    holding_day_funding,
    merge_oi,
    pit_members,
    regime_baseline,
    rolling_z,
    sharpe,
    shock,
    snapshot_before,
    target_weights,
    total_return,
    vol_scalar,
    week_bootstrap,
)


def _t(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


def test_oracle_never_imports_mce() -> None:
    src = (Path(__file__).parents[2] / "scripts" / "research" / "oracle.py").read_text("utf-8")
    imports = [ln for ln in src.splitlines() if re.match(r"\s*(import|from)\s", ln)]
    assert imports and not any("mce" in ln for ln in imports)


def test_shock_in_prior_atr() -> None:
    close = pd.Series([10.0, 11.0, 9.0, 9.5, 12.0])
    atr = pd.Series([1.0, 2.0, 1.0, 0.5, 1.0])
    out = shock(close, atr)
    assert np.isnan(out.iloc[0])
    assert out.iloc[1:].tolist() == pytest.approx([1.0, -1.0, 0.5, 5.0])


def test_open_interest_snapshots() -> None:
    archive = pd.DataFrame(
        {
            "date": [_t("2024-01-01 00:50"), _t("2024-01-01 01:50"), _t("2024-01-01 03:50")],
            "oi_value": [1.0, 2.0, 0.0],  # the zero print is missing data, not a level
        }
    )
    api = pd.DataFrame(
        {"date": [_t("2024-01-01 00:50"), _t("2024-01-01 05:50")], "oi_value": [9.0, 4.0]}
    )
    snaps = merge_oi(archive, api)
    assert snaps["oi_value"].tolist() == [1.0, 2.0, 4.0]  # archive wins at 00:50, zero dropped
    times = pd.Series(
        [
            _t("2024-01-01 01:50"),
            _t("2024-01-01 02:00"),
            _t("2024-01-01 04:00"),
            _t("2024-01-01 06:00"),
        ]
    )
    got = snapshot_before(times, snaps, pd.Timedelta(hours=4))
    assert got.tolist() == [1.0, 2.0, 2.0, 4.0]  # strictly before: 01:50 is not used at 01:50
    got = snapshot_before(times, snaps, pd.Timedelta(hours=2))
    assert got[:2].tolist() == [1.0, 2.0] and np.isnan(got[2]) and got[3] == 4.0  # 04:00: 2h10 old


def test_rolling_z() -> None:
    z = rolling_z(pd.Series([1.0, 2.0, 3.0, 4.0, 5.0]), 3)
    assert z.iloc[:2].isna().all() and z.iloc[2:].tolist() == pytest.approx([1.0, 1.0, 1.0])
    assert rolling_z(pd.Series([2.0] * 5), 3).iloc[2:].isna().all()  # no spread: undefined


def _panel() -> tuple[pd.DataFrame, pd.DataFrame]:
    """A, C trade all along; B stops on 2021-02-10; D starts on 2021-02-20, the most liquid."""
    idx = pd.date_range("2021-01-01", "2021-03-31", freq="D", tz="UTC")
    close = pd.DataFrame(1.0, index=idx, columns=["A", "B", "C", "D"])
    close.loc["2021-02-10":, "B"] = np.nan
    close.loc[:"2021-02-19", "D"] = np.nan
    volume = pd.DataFrame({"A": 4.0, "B": 3.0, "C": 2.0, "D": 100.0}, index=idx).where(
        close.notna()
    )
    return close, volume


def test_point_in_time_members() -> None:
    close, volume = _panel()
    m = pit_members(close, volume, n=2, volume_window=3, min_history=5)

    def members(day: str) -> set[str]:
        return {c for c in m.columns if m.loc[_t(day), c]}

    assert members("2021-01-30") == set()  # no month-end ranking yet
    assert members("2021-01-31") == {"A", "B"}  # January ranking: A 4 > B 3 > C 2
    assert members("2021-02-09") == {"A", "B"}
    assert members("2021-02-10") == {"A"}  # B has no close: out, not replaced
    assert members("2021-02-27") == {"A"}
    assert members("2021-02-28") == {"A", "D"}  # February ranking: D 100 > A 4
    assert members("2021-03-31") == {"A", "D"}


def test_bar_membership_uses_the_previous_day() -> None:
    close, volume = _panel()
    m = pit_members(close, volume, n=2, volume_window=3, min_history=5)
    bars = pd.Series([_t("2021-02-09 20:00"), _t("2021-02-10 16:00"), _t("2021-02-10 20:00")])
    # decisions at 02-10 00:00, 02-10 20:00, 02-11 00:00 -> rankings of 02-09, 02-09, 02-10
    assert bar_active(m, "B", bars, pd.Timedelta(hours=4)).tolist() == [True, True, False]
    assert bar_active(m, "ZZ", bars, pd.Timedelta(hours=4)).tolist() == [False, False, False]


def test_regime_labels() -> None:
    adx = pd.Series([25.0, 25.0, 10.0, 25.0, np.nan, 25.0])
    slope = pd.Series([0.6, -0.7, 0.9, 0.2, 1.0, 0.5])
    atr_pct = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 1.0])
    got = classify_regime(adx, slope, atr_pct, window=5)
    # ATR% rank in a 5-value window: row 4 is the highest (1.0: high), row 5 the lowest (0.2: low)
    assert got.iloc[:4].isna().all() and pd.isna(got.iloc[4])  # vol unknown, then adx missing
    assert got.iloc[5] == "up|low"
    trend = classify_regime(adx, slope, pd.Series([1.0] * 6), window=1)
    assert trend.str.split("|").str[0].tolist()[:4] == ["up", "down", "range", "range"]


def test_events_with_cooldown() -> None:
    signal = np.array([0, 1, 1, 0, 1, 1, 1, 0, -1])
    eligible = np.array([True] * 9)
    eligible[4] = False
    assert events_with_cooldown(signal, eligible, 3).tolist() == [1, 5, 8]


def test_labels_baseline_fee_and_funding() -> None:
    close = np.array([10.0, 11.0, 12.0, 9.0, 10.0])
    atr = np.array([2.0] * 5)
    rows = np.array([1])
    assert forward_r(close, atr, rows, 1).tolist() == [0.5]  # (12 - 11) / 2
    assert forward_r(close, atr, rows, 3).tolist() == [-0.5]  # (10 - 11) / 2
    assert np.isnan(forward_r(close, atr, rows, 4)[0])  # beyond the data: never read
    assert forward_r(close, atr, rows, 1, delay=1).tolist() == [-1.5]  # (9 - 12) / 2, late entry

    fwd = np.array([1.0, 2.0, 3.0, np.nan, 5.0])
    regime = np.array(["a", "a", "b", "b", "a"], dtype=object)
    eligible = np.array([True, True, True, True, False])
    assert regime_baseline(fwd, regime, eligible) == pytest.approx({"a": 1.5, "b": 3.0})

    prints = pd.Series(
        [0.001, 0.002, -0.001],
        index=[_t("2024-01-01 00:00"), _t("2024-01-01 08:00"), _t("2024-01-01 16:00")],
    )
    start = pd.Series([_t("2024-01-01 04:00"), _t("2024-01-01 00:00")])
    end = pd.Series([_t("2024-01-01 16:00"), _t("2024-01-01 07:59")])
    assert funding_sum(prints, start, end).tolist() == pytest.approx([0.001, 0.0])  # (start, end]


def test_week_bootstrap() -> None:
    weeks = np.array(["w1", "w1", "w2", "w3"])
    out = week_bootstrap(np.array([0.5, 0.5, 0.5, 0.5]), weeks, n_boot=200, seed=1)
    assert out["mean"] == pytest.approx(0.5) and out["lo"] == pytest.approx(0.5)
    assert out["hi"] == pytest.approx(0.5)
    out = week_bootstrap(np.array([1.0, 3.0, 2.0, np.nan]), weeks, n_boot=500, seed=1)
    assert out["mean"] == pytest.approx(2.0) and out["lo"] <= 2.0 <= out["hi"]


def test_donchian_by_hand() -> None:
    close = np.array([10, 11, 12, 11.4, 13, 9, 8, 10, 12, 11], dtype=float)
    state, stop = donchian(close, 2)
    assert state.tolist() == [0, 0, 1, 0, 1, -1, -1, 1, 1, 1]
    expected = [np.nan, np.nan, 10.5, np.nan, 11.7, 12.2, 11.0, 8.5, 9.0, 11.0]
    np.testing.assert_allclose(stop, expected, equal_nan=True)
    gap = close.copy()
    gap[5] = np.nan  # a missing close resets to flat
    assert donchian(gap, 2)[0][5] == 0


def test_ensemble_vol_scalar_and_weights() -> None:
    idx = pd.date_range("2024-01-01", periods=6, freq="D", tz="UTC")
    close = pd.DataFrame({"A": [10.0, 11.0, 12.0, 13.0, 9.0, 14.0]}, index=idx)
    ens = ensemble(close, (2, 3))
    # n=2: [0,0,1,1,-1,1]; n=3: [0,0,0,1,-1,1]; n=3 has history from row 3 only
    assert ens["signal"]["A"].tolist() == [0.0, 0.0, 1.0, 1.0, -1.0, 1.0]
    assert ens["long_stop"]["A"].iloc[3] == pytest.approx(11.0)  # min(11.5, 11)
    assert ens["short_stop"]["A"].iloc[4] == pytest.approx(12.5)  # max(12.5, 12)

    pct = [0.01, -0.01, 0.01, -0.01]
    c = pd.DataFrame({"A": 100 * np.cumprod([1.0, *[1 + r for r in pct]])})
    scalar = vol_scalar(c, target=0.25, window=4, cap=2.0)
    vol = math.sqrt(4 * 0.01**2 / 3) * math.sqrt(365)
    assert scalar["A"].iloc[:4].isna().all() and scalar["A"].iloc[4] == pytest.approx(0.25 / vol)
    assert vol_scalar(c, target=10.0, window=4, cap=2.0)["A"].iloc[4] == 2.0  # capped

    sig = pd.DataFrame({"A": [1.0, -0.5], "B": [1.0, 1.0]})
    sc = pd.DataFrame({"A": [2.0, 2.0], "B": [1.0, np.nan]})
    mem = pd.DataFrame({"A": [True, True], "B": [False, True]})
    w = target_weights(sig, sc, mem, slots=20)
    assert w.values.tolist() == [[0.1, 0.0], [-0.05, 0.0]]  # B: not a member, then no scalar


def test_holding_day_funding() -> None:
    days = pd.date_range("2020-12-31", periods=3, freq="D", tz="UTC")
    prints = pd.DataFrame(
        {"A": [0.001, 0.002, 0.004]},
        index=[_t("2021-01-02 00:00"), _t("2021-01-02 08:00"), _t("2021-01-03 00:00")],
    )
    got = holding_day_funding(prints, days)["A"]
    # row D holds from D+1 00:00 to D+2 00:00, the print at its end included
    assert got.tolist() == pytest.approx([0.001, 0.006, 0.0])


def test_backtest_by_hand() -> None:
    idx = pd.date_range("2024-01-01", periods=4, freq="D", tz="UTC")
    close = pd.DataFrame(
        {"A": [100.0, 110.0, 99.0, 99.0], "B": [50.0, 50.0, 55.0, 55.0]}, index=idx
    )
    w = pd.DataFrame({"A": [0.5, 0.5, 0.0, 0.0], "B": [-0.5, -0.5, 0.0, 0.0]}, index=idx)
    fund = pd.DataFrame({"A": [0.001, 0.0, 0.0, 0.0], "B": [0.0, 0.002, 0.0, 0.0]}, index=idx)
    out = backtest(w, close, fee=0.0006, funding=fund)
    drift1 = [0.55 / 1.05, -0.5 / 1.05]  # day-0 weights after A +10%, B flat, book +5%
    turn1 = abs(0.5 - drift1[0]) + abs(-0.5 - drift1[1])
    drift2 = [0.5 * 0.9 / 0.9, -0.5 * 1.1 / 0.9]  # day 1: A -10%, B +10%, book -10%
    turn2 = abs(drift2[0]) + abs(drift2[1])
    assert out["turnover"].tolist() == pytest.approx([1.0, turn1, turn2, 0.0])
    assert out["gross"].tolist() == pytest.approx([0.05, -0.10, 0.0, 0.0])
    assert out["funding"].tolist() == pytest.approx([0.0005, -0.001, 0.0, 0.0])  # short receives
    expected = [
        0.05 - 0.0006 - 0.0005,
        -0.10 - 0.0006 * turn1 + 0.001,
        -0.0006 * turn2,
        0.0,
    ]
    assert out["net"].tolist() == pytest.approx(expected)
    late = backtest(w, close, fee=0.0006, funding=fund, lag=1)
    assert late["gross"].tolist() == pytest.approx([0.0, -0.10, 0.05 * 0 + 0.0, 0.0])


def test_sharpe_and_total_return() -> None:
    net = pd.Series([0.01, -0.01, 0.02])
    mean = 0.02 / 3
    sd = math.sqrt(((0.01 - mean) ** 2 + (-0.01 - mean) ** 2 + (0.02 - mean) ** 2) / 2)
    assert sharpe(net) == pytest.approx(mean * 365 / (sd * math.sqrt(365)))
    assert total_return(net) == pytest.approx(1.01 * 0.99 * 1.02 - 1)
