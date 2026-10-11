import numpy as np
import pandas as pd
import pytest
from mce.portfolio import (
    block_bootstrap_sharpe,
    daily_funding,
    deflated_sharpe,
    funding_known_at_close,
    metrics,
    quantile_long_short,
    simulate,
    vol_target_scalar,
    yearly_returns,
)


IDX = pd.date_range("2024-01-01", periods=4, freq="D", tz="UTC")


def test_simulate_hand_computed() -> None:
    close = pd.DataFrame({"A": [100.0, 110.0, 99.0, 99.0]}, index=IDX)
    weights = pd.DataFrame({"A": [1.0, 1.0, 0.0, 0.0]}, index=IDX)
    res = simulate(weights, close, fee=0.001)
    d = res.daily
    # day 0: buy 1.0 (turnover 1), earn +10%
    assert d["turnover"].iloc[0] == pytest.approx(1.0)
    assert d["net"].iloc[0] == pytest.approx(0.10 - 0.001)
    # day 1: weight drifted to 1.0 still (single asset) -> no trade, earn -10%
    assert d["turnover"].iloc[1] == pytest.approx(0.0)
    assert d["net"].iloc[1] == pytest.approx(-0.10)
    # day 2: sell everything
    assert d["turnover"].iloc[2] == pytest.approx(1.0)
    assert d["net"].iloc[2] == pytest.approx(-0.001)
    assert d["equity"].iloc[-1] == pytest.approx((1.099) * 0.9 * 0.999)


def test_drift_generates_rebalance_turnover() -> None:
    close = pd.DataFrame({"A": [100.0, 200.0, 200.0], "B": [100.0, 100.0, 100.0]}, index=IDX[:3])
    weights = pd.DataFrame({"A": [0.5, 0.5, 0.5], "B": [0.5, 0.5, 0.5]}, index=IDX[:3])
    d = simulate(weights, close, fee=0.0).daily
    # after A doubles the book is 2/3 A, 1/3 B -> back to 50/50 trades 1/6 on each side
    assert d["turnover"].iloc[1] == pytest.approx(2 * (2 / 3 - 0.5))


def test_lag_delays_execution() -> None:
    close = pd.DataFrame({"A": [100.0, 110.0, 121.0, 121.0]}, index=IDX)
    weights = pd.DataFrame({"A": [1.0, 0.0, 0.0, 0.0]}, index=IDX)
    on_time = simulate(weights, close, fee=0.0).daily["net"]
    late = simulate(weights, close, fee=0.0, lag=1).daily["net"]
    assert on_time.iloc[0] == pytest.approx(0.10) and on_time.iloc[1] == 0
    assert late.iloc[0] == 0 and late.iloc[1] == pytest.approx(0.10)


def test_daily_funding_alignment_and_cost() -> None:
    prints = pd.date_range("2024-01-02 00:00", "2024-01-04 00:00", freq="8h", tz="UTC")
    funding = pd.DataFrame({"A": 0.001}, index=prints)
    fd = daily_funding(funding, IDX)
    # row 2024-01-01 holds (01-02 00:00, 01-03 00:00]: prints 08:00, 16:00, 00:00 of 01-03
    assert fd["A"].iloc[0] == pytest.approx(0.003)
    assert fd["A"].iloc[1] == pytest.approx(0.003)
    close = pd.DataFrame({"A": [100.0] * 4}, index=IDX)
    long = simulate(pd.DataFrame({"A": [1.0] * 4}, index=IDX), close, fee=0.0, funding=fd)
    short = simulate(pd.DataFrame({"A": [-1.0] * 4}, index=IDX), close, fee=0.0, funding=fd)
    assert long.daily["net"].iloc[0] == pytest.approx(-0.003)
    assert short.daily["net"].iloc[0] == pytest.approx(0.003)


def test_funding_known_at_close_is_one_day_before_paid() -> None:
    prints = pd.date_range("2024-01-01 08:00", "2024-01-05 00:00", freq="8h", tz="UTC")
    funding = pd.DataFrame({"A": np.arange(len(prints), dtype=float)}, index=prints)
    known = funding_known_at_close(funding, IDX)
    # row 2024-01-01 sees the prints of 01-01 08:00, 16:00 and 01-02 00:00 (values 0, 1, 2)
    assert known["A"].iloc[0] == pytest.approx(3.0)
    # what is known at the close of row D+1 is what a holder of row D paid
    paid = daily_funding(funding, IDX)
    assert known["A"].iloc[1:].tolist() == pytest.approx(paid["A"].iloc[:-1].tolist())
    empty = funding_known_at_close(funding.iloc[:0], IDX)
    assert empty.isna().all().all()


def test_quantile_long_short_construction() -> None:
    idx = pd.date_range("2024-01-01", periods=5, freq="D", tz="UTC")
    cols = list("ABCDEF")
    signal = pd.DataFrame([[6.0, 5, 4, 3, 2, 1]] * 5, index=idx, columns=cols)
    signal.iloc[2] = [1.0, 2, 3, 4, 5, 6]  # flips between rebalances: must not trade
    members = pd.DataFrame(True, index=idx, columns=cols)
    members.loc[idx[3] :, "B"] = False  # B leaves the universe on day 3
    rebalance = np.array([True, False, False, False, True])
    w = quantile_long_short(signal, members, rebalance)
    assert w.iloc[0].tolist() == pytest.approx([-0.25, -0.25, 0, 0, 0.25, 0.25])
    assert w.iloc[2].tolist() == w.iloc[0].tolist()  # held through the signal flip
    assert w.iloc[3]["B"] == 0 and w.iloc[3]["A"] == pytest.approx(-0.25)
    # day 4: 5 members -> k = 1: short A (highest), long F (lowest)
    assert w.iloc[4].tolist() == pytest.approx([-0.5, 0, 0, 0, 0, 0.5])
    assert w.sum(axis=1).iloc[[0, 4]].abs().max() < 1e-12
    # causal: truncating the future does not change past weights
    part = quantile_long_short(signal.iloc[:3], members.iloc[:3], rebalance[:3])
    pd.testing.assert_frame_equal(part, w.iloc[:3])


def test_vol_target_scalar_causal_and_capped() -> None:
    rng = np.random.default_rng(0)
    idx = pd.date_range("2022-01-01", periods=400, freq="D", tz="UTC")
    close = pd.DataFrame({"A": 100 * np.exp(np.cumsum(rng.normal(0, 0.03, 400)))}, index=idx)
    full = vol_target_scalar(close, 0.25)
    part = vol_target_scalar(close.iloc[:250], 0.25)
    pd.testing.assert_frame_equal(full.iloc[:250], part)
    assert full.max().max() <= 2.0
    calm = pd.DataFrame({"A": 100 * np.exp(np.cumsum(rng.normal(0, 0.001, 400)))}, index=idx)
    assert vol_target_scalar(calm, 0.25).dropna().eq(2.0).all().all()


def test_metrics_and_deflated_sharpe() -> None:
    rng = np.random.default_rng(1)
    idx = pd.date_range("2020-01-01", periods=1460, freq="D", tz="UTC")
    good = pd.Series(rng.normal(0.002, 0.02, 1460), index=idx)
    m = metrics(good)
    assert m["sharpe"] > 1.0 and m["max_dd"] < 0
    assert len(yearly_returns(good)) == 4
    one = deflated_sharpe(good, n_trials=1, sr_std_trials=0.0)
    many = deflated_sharpe(good, n_trials=100, sr_std_trials=0.05)
    assert one["dsr"] > 0.95
    assert many["dsr"] < one["dsr"]
    noise = pd.Series(rng.normal(0, 0.02, 1460), index=idx)
    assert deflated_sharpe(noise, n_trials=20, sr_std_trials=0.03)["dsr"] < 0.5


def test_block_bootstrap_sharpe() -> None:
    rng = np.random.default_rng(2)
    idx = pd.date_range("2020-01-01", periods=1460, freq="D", tz="UTC")
    net = pd.Series(rng.normal(0.001, 0.02, 1460), index=idx)
    a = block_bootstrap_sharpe(net, block=20, n_boot=500, seed=1)
    assert a == block_bootstrap_sharpe(net, block=20, n_boot=500, seed=1)  # seeded
    assert a["sharpe"] == pytest.approx(metrics(net)["sharpe"])
    assert a["lo"] < a["sharpe"] < a["hi"]
    # iid returns: annual Sharpe s.e. ~ sqrt(365 / 1460) = 0.5, so the 5-95% band is ~1.65 wide
    assert 1.2 < a["hi"] - a["lo"] < 2.2
