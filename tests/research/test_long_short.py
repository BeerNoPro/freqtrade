"""Cross-sectional long-short tranches: construction, causality and synthetic-market behaviour."""

import numpy as np
import pandas as pd
import pytest
from mce.features import xs_rank_mean
from mce.labels import forward_hold_return, forward_sum
from mce.portfolio import (
    beta_attribution,
    long_short_tranches,
    metrics,
    quantile_legs,
    rolling_beta,
    simulate,
    staggered,
)


def _days(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")


def test_xs_rank_mean_ranks_members_with_all_signals() -> None:
    idx, cols = _days(2), list("ABCD")
    a = pd.DataFrame([[1.0, 2, 3, 4], [4.0, 3, 2, 1]], index=idx, columns=cols)
    b = pd.DataFrame([[10.0, 30, 20, np.nan], [1.0, 2, 3, 4]], index=idx, columns=cols)
    members = pd.DataFrame(True, index=idx, columns=cols)
    members.iloc[1, 0] = False
    score = xs_rank_mean([a, b], members)
    # day 0: D has no b -> ranked among A, B, C: a ranks 1/3, 2/3, 1; b ranks 1/3, 1, 2/3
    assert score.iloc[0].tolist()[:3] == pytest.approx([1 / 3, 5 / 6, 5 / 6])
    assert np.isnan(score.iloc[0, 3])
    # day 1: A is not a member -> B, C, D: a ranks 1, 2/3, 1/3; b ranks 1/3, 2/3, 1
    assert np.isnan(score.iloc[1, 0])
    assert score.iloc[1].tolist()[1:] == pytest.approx([2 / 3, 2 / 3, 2 / 3])


def test_quantile_legs_size_and_members() -> None:
    idx = _days(1)
    signal = pd.DataFrame([np.arange(10, dtype=float)], index=idx, columns=list("ABCDEFGHIJ"))
    members = pd.DataFrame(True, index=idx, columns=signal.columns)
    top, bottom = quantile_legs(signal, members, 0.3)
    assert top.iloc[0][top.iloc[0]].index.tolist() == ["H", "I", "J"]
    assert bottom.iloc[0][bottom.iloc[0]].index.tolist() == ["A", "B", "C"]
    members["J"] = False  # 9 members -> k = 2
    top, _ = quantile_legs(signal, members, 0.3)
    assert top.iloc[0][top.iloc[0]].index.tolist() == ["H", "I"]


def test_long_short_tranches_beta_neutral_hand_computed() -> None:
    idx = _days(3)
    cols = list("ABCDEFGHIJ")
    score = pd.DataFrame([np.arange(10, 0, -1, dtype=float)] * 3, index=idx, columns=cols)
    members = pd.DataFrame(True, index=idx, columns=cols)
    members.iloc[2] = False  # nothing ranked -> empty tranche
    beta = pd.DataFrame(1.0, index=idx, columns=cols)
    beta[["H", "I", "J"]] = 2.0
    beta.loc[idx[1], "J"] = -8.0  # short leg beta (2 + 2 - 8) / 3 < 0 -> dollar neutral
    w = long_short_tranches(score, members, 0.3, beta)
    # day 0: long A, B, C (beta 1), short H, I, J (beta 2): long gross 2/3, short gross 1/3
    assert w.iloc[0, :3].tolist() == pytest.approx([2 / 9] * 3)
    assert w.iloc[0, 7:].tolist() == pytest.approx([-1 / 9] * 3)
    assert (w.iloc[0] * beta.iloc[0]).sum() == pytest.approx(0.0)
    assert w.iloc[0].abs().sum() == pytest.approx(1.0)
    assert w.iloc[1, :3].tolist() == pytest.approx([1 / 6] * 3)
    assert w.iloc[1, 7:].tolist() == pytest.approx([-1 / 6] * 3)
    assert (w.iloc[2] == 0).all()
    dollar = long_short_tranches(score, members, 0.3)
    assert dollar.iloc[0].sum() == pytest.approx(0.0) and dollar.iloc[0, 0] == pytest.approx(1 / 6)


def test_staggered_mean_of_tranches_and_forced_exit() -> None:
    idx = _days(5)
    tranches = pd.DataFrame(0.0, index=idx, columns=["A", "B"])
    tranches.iloc[0] = [1.0, -1.0]
    tranches.iloc[1] = [-1.0, 1.0]
    alive = pd.DataFrame(True, index=idx, columns=["A", "B"])
    alive.iloc[3, 1] = False  # B has no close on day 3
    w = staggered(tranches, 3, alive)
    assert w.iloc[0].tolist() == pytest.approx([1 / 3, -1 / 3])
    assert w.iloc[1].tolist() == pytest.approx([0.0, 0.0])  # the two tranches offset
    assert w.iloc[2].tolist() == pytest.approx([0.0, 0.0])
    assert w.iloc[3].tolist() == pytest.approx([-1 / 3, 0.0])  # tranche 0 expired, B closed
    assert w.iloc[4].tolist() == pytest.approx([0.0, 0.0])  # tranche 1 expired
    part = staggered(tranches.iloc[:3], 3, alive.iloc[:3])
    pd.testing.assert_frame_equal(part, w.iloc[:3])


def test_rolling_beta_known_value_and_causal() -> None:
    rng = np.random.default_rng(5)
    m = rng.normal(0, 0.03, 200)
    close = pd.DataFrame(
        {
            "BTC": 100 * np.cumprod(1 + m),
            "A": 50 * np.cumprod(1 + 2 * m),
            "B": 20 * np.cumprod(1 - 0.5 * m + 0.001),
        },
        index=_days(200),
    )
    close.iloc[100, 1] = np.nan  # a missing day only removes the returns that touch it
    beta = rolling_beta(close, "BTC", window=90, min_periods=60)
    assert beta["A"].iloc[:60].isna().all() and np.isfinite(beta["A"].iloc[60])
    assert beta["BTC"].dropna().to_numpy() == pytest.approx(1.0)
    assert beta["A"].dropna().to_numpy() == pytest.approx(2.0)
    assert beta["B"].dropna().to_numpy() == pytest.approx(-0.5)
    part = rolling_beta(close.iloc[:150], "BTC", window=90, min_periods=60)
    pd.testing.assert_frame_equal(part, beta.iloc[:150])


def test_forward_hold_return_delist_and_end() -> None:
    close = pd.DataFrame(
        {"A": [100.0, 110.0, 121.0, np.nan, np.nan], "B": [10.0, 11.0, 12.0, 13.0, 14.0]},
        index=_days(5),
    )
    fwd = forward_hold_return(close, 2)
    assert fwd["A"].iloc[0] == pytest.approx(0.21)
    assert fwd["A"].iloc[1] == pytest.approx(0.10)  # exits at the last close (delisted)
    assert fwd["A"].iloc[2] == pytest.approx(0.0)
    assert fwd["A"].iloc[3:].isna().all()
    assert fwd["B"].iloc[2] == pytest.approx(14 / 12 - 1) and fwd["B"].iloc[3:].isna().all()
    paid = pd.DataFrame({"A": [1.0, 2.0, 3.0, 4.0, 5.0]}, index=_days(5))
    assert forward_sum(paid, 2)["A"].tolist()[:4] == [3.0, 5.0, 7.0, 9.0]
    assert np.isnan(forward_sum(paid, 2)["A"].iloc[4])


def test_beta_attribution_separates_beta_from_alpha() -> None:
    rng = np.random.default_rng(6)
    idx = _days(2000)
    market = pd.Series(rng.normal(-0.004, 0.03, 2000), index=idx)
    net = -0.8 * market + rng.normal(0, 0.005, 2000)  # pure short beta, no alpha
    att = beta_attribution(net, market)
    assert att["beta"] == pytest.approx(-0.8, abs=0.02)
    assert abs(att["resid_sharpe"]) < 1.2 and metrics(net)["sharpe"] > 1.5


# ---------------------------------------------------------------- synthetic markets


def _market(
    seed: int,
    drift: float,
    idio: float,
    momentum: float = 0.0,
    mkt_vol: float = 0.03,
    n_coins: int = 30,
    n_days: int = 1100,
) -> pd.DataFrame:
    """Daily closes: BTC is the market; coins have betas 0.5..2.5, idiosyncratic noise and,
    with ``momentum``, an idiosyncratic drift that persists for 60-day spells."""
    rng = np.random.default_rng(seed)
    mkt = rng.normal(drift, mkt_vol, n_days)
    betas = np.linspace(0.5, 2.5, n_coins)
    spells = np.repeat(rng.normal(0, momentum, (n_days // 60 + 1, n_coins)), 60, axis=0)
    ret = mkt[:, None] * betas + spells[:n_days] + rng.normal(0, idio, (n_days, n_coins))
    close = pd.DataFrame(
        100 * np.cumprod(1 + np.clip(ret, -0.9, None), axis=0),
        index=_days(n_days),
        columns=[f"C{i}" for i in range(n_coins)],
    )
    close.insert(0, "BTC", 100 * np.cumprod(1 + mkt))
    return close


def _xs_momentum(close: pd.DataFrame, neutral: str = "beta") -> pd.DataFrame:
    members = close.notna()
    score = xs_rank_mean([close / close.shift(h) - 1 for h in (7, 14, 21, 28)], members)
    beta = rolling_beta(close, "BTC", 90, 60) if neutral == "beta" else None
    tranches = long_short_tranches(score, members, 0.3, beta)
    return staggered(tranches, 7, members)


def _sharpe(close: pd.DataFrame, weights: pd.DataFrame) -> float:
    net = simulate(weights.iloc[120:], close.iloc[120:], fee=0.0).daily["net"].iloc[:-1]
    return metrics(net)["sharpe"]


def test_synthetic_null_has_no_edge() -> None:
    # one market's Sharpe spreads ~0.75 around zero, so average 20 markets (s.e. ~0.17)
    sharpes = [_sharpe(c, _xs_momentum(c)) for c in (_market(s, 0.0, 0.03) for s in range(20))]
    assert abs(np.mean(sharpes)) < 0.6


def test_synthetic_planted_momentum_is_found() -> None:
    close = _market(11, 0.0, 0.03, momentum=0.004)
    assert _sharpe(close, _xs_momentum(close)) > 1.5


def test_synthetic_beta_trap_is_removed_by_beta_neutral_legs() -> None:
    """Falling market, no idiosyncratic edge: high-beta coins become the losers, so a dollar-
    neutral book is net short beta and looks profitable; the beta-neutral book does not."""
    close = _market(21, -0.003, 0.01, mkt_vol=0.015)
    dollar, neutral = _xs_momentum(close, "dollar"), _xs_momentum(close)
    btc = close["BTC"].pct_change().shift(-1)
    net_dollar = simulate(dollar, close, fee=0.0).daily["net"].iloc[120:-1]
    net_neutral = simulate(neutral, close, fee=0.0).daily["net"].iloc[120:-1]
    trap = beta_attribution(net_dollar, btc)
    assert metrics(net_dollar)["sharpe"] > 1.5
    assert trap["beta"] < -0.2 and trap["resid_sharpe"] < 0.5  # the profit is all beta
    assert abs(metrics(net_neutral)["sharpe"]) < 1.0
    assert abs(beta_attribution(net_neutral, btc)["beta"]) < 0.1


def test_pipeline_is_causal() -> None:
    close = _market(31, 0.0, 0.03, n_coins=12, n_days=400)
    close.iloc[250:, 5] = np.nan  # a coin delisted on day 250
    full = _xs_momentum(close)
    for cut in (150, 251, 330):
        pd.testing.assert_frame_equal(_xs_momentum(close.iloc[:cut]), full.iloc[:cut])
    assert (full.iloc[250:, 5] == 0).all()
