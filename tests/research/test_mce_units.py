import numpy as np
import pandas as pd
import pytest
from mce.event_study import StudyConfig, _dedupe, pair_events, run, summarize
from mce.features import add_core_features
from mce.hooks import Hook, HookRegistry
from mce.labels import forward_returns_r, funding_between, triple_barrier
from mce.regime import RegimeRules, add_regime
from mce.stats import benjamini_hochberg, cluster_bootstrap_diff, cluster_bootstrap_mean


def _path(closes: list[float], highs: list[float], lows: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"close": closes, "high": highs, "low": lows, "atr": 1.0})


def test_triple_barrier_tp_sl_time() -> None:
    df = _path([100, 100.5, 102.5, 101], [100, 101, 103, 101], [100, 100, 100, 100])
    res = triple_barrier(df, np.array([0]), np.array([1]), tp_atr=2, sl_atr=1, max_bars=3)
    assert (
        res.loc[0, "tb_label"] == 1 and res.loc[0, "tb_ret_r"] == 2 and res.loc[0, "tb_bars"] == 2
    )
    # same bar touches both barriers -> stop first
    df = _path([100, 100, 100, 100], [100, 103, 100, 100], [100, 98, 100, 100])
    res = triple_barrier(df, np.array([0]), np.array([1]), tp_atr=2, sl_atr=1, max_bars=3)
    assert res.loc[0, "tb_label"] == -1
    # short, time barrier
    df = _path([100, 99.8, 99.6, 99.5], [100, 100.2, 99.9, 99.7], [100, 99.7, 99.5, 99.4])
    res = triple_barrier(df, np.array([0]), np.array([-1]), tp_atr=2, sl_atr=1, max_bars=3)
    assert res.loc[0, "tb_label"] == 0
    assert res.loc[0, "tb_ret_r"] == pytest.approx(0.5)
    # not enough future candles
    res = triple_barrier(df, np.array([2]), np.array([1]), tp_atr=2, sl_atr=1, max_bars=3)
    assert res.isna().all(axis=None)


def test_forward_returns_r() -> None:
    df = pd.DataFrame({"close": [100.0, 102.0, 101.0], "atr": 2.0})
    out = forward_returns_r(df, [1, 2])
    assert out["fwd_r_1"].tolist()[:2] == [1.0, -0.5]
    assert out["fwd_r_2"].iloc[0] == 0.5 and np.isnan(out["fwd_r_2"].iloc[1])


def test_benjamini_hochberg_known_values() -> None:
    p = np.array([0.01, 0.04, 0.03, 0.005, np.nan])
    reject, adj = benjamini_hochberg(p, 0.05)
    assert reject.tolist() == [True, True, True, True, False]
    assert adj[3] == pytest.approx(0.02) and adj[1] == pytest.approx(0.04)
    reject, _ = benjamini_hochberg(np.array([0.02, 0.5, 0.9]), 0.05)
    assert reject.tolist() == [False, False, False]


def test_cluster_bootstrap_detects_effect_and_null() -> None:
    rng = np.random.default_rng(3)
    clusters = np.repeat(np.arange(100), 10)
    null = cluster_bootstrap_mean(rng.normal(0, 1, 1000), clusters, 1000)
    assert null["lo"] < 0 < null["hi"] and null["p"] > 0.01
    effect = cluster_bootstrap_mean(rng.normal(0.3, 1, 1000), clusters, 1000)
    assert effect["lo"] > 0 and effect["p"] < 0.01


def test_hook_rules_and_fingerprint() -> None:
    def cond(df: pd.DataFrame, p: dict) -> pd.Series:
        return pd.Series(1, index=df.index)

    with pytest.raises(ValueError, match="at most 3"):
        Hook("x", 1, "h", cond, {"a": 1, "b": 2, "c": 3, "d": 4})
    a = Hook("x", 1, "h", cond, {"a": 1})
    b = Hook("x", 1, "h", cond, {"a": 2})
    assert a.fingerprint() != b.fingerprint()
    reg = HookRegistry()
    reg.register(a)
    with pytest.raises(ValueError, match="duplicate"):
        reg.register(b)
    assert _dedupe(np.array([1, 2, 5, 6, 12]), 5).tolist() == [1, 6, 12]


def test_event_study_end_to_end(ohlcv: pd.DataFrame) -> None:
    df = add_regime(add_core_features(ohlcv), RegimeRules(vol_window=200))

    def always_long(d: pd.DataFrame, p: dict) -> pd.Series:
        return pd.Series(1, index=d.index)

    hook = Hook("long", 1, "h", always_long, horizon=4)
    cfg = StudyConfig(start=df["date"].iloc[300], end=df["date"].iloc[1800], n_boot=200)
    events = run([hook], {"A": df}, cfg)
    assert events["date"].max() < cfg.end
    assert events["date"].diff().dropna().min() >= pd.Timedelta(hours=4)
    # labels never read beyond the study end
    last = events["date"].max()
    assert last + pd.Timedelta(hours=24) < cfg.end or np.isnan(events["ret_24"].iloc[-1])
    summary = summarize(events, [hook], cfg)
    assert set(summary["horizon"]) == {1, 4, 12, 24}
    assert summary["p_bh"].between(0, 1).all()


def test_forward_returns_delay_and_funding_between() -> None:
    df = pd.DataFrame({"close": [100.0, 102.0, 101.0, 105.0], "atr": 2.0})
    late = forward_returns_r(df, [1, 2], delay=1)
    assert late["fwd_r_1"].tolist()[:2] == [-0.5, 2.0]  # entered at close[t+1]
    assert late["fwd_r_2"].iloc[0] == 1.5 and late["fwd_r_2"].iloc[1:].isna().all()
    stamps = ["2025-01-01 08:00", "2025-01-01 16:00", "2025-01-02 00:00"]
    prints = pd.Series([0.001, 0.002, -0.001], index=pd.to_datetime(stamps, utc=True))
    start = pd.Series(pd.to_datetime(["2025-01-01 08:00", "2025-01-01 00:00"], utc=True))
    end = pd.Series(pd.to_datetime(["2025-01-02 00:00", "2025-01-01 08:00"], utc=True))
    # (08:00, 00:00] holds the 16:00 and 00:00 prints; (00:00, 08:00] holds the 08:00 print
    assert funding_between(prints, start, end) == pytest.approx([0.001, 0.001])


def test_event_study_skips_inactive_rows_and_reports_late_entry(ohlcv: pd.DataFrame) -> None:
    df = add_regime(add_core_features(ohlcv), RegimeRules(vol_window=200))
    df["active"] = (np.arange(len(df)) // 100) % 2 == 0  # in the universe every other 100 rows

    def always_long(d: pd.DataFrame, p: dict) -> pd.Series:
        return pd.Series(1, index=d.index)

    hook = Hook("long", 1, "h", always_long, horizon=4)
    cfg = StudyConfig(start=df["date"].iloc[300], end=df["date"].iloc[1800], horizons=(1, 4))
    ev = run([hook], {"A": df}, cfg)
    assert len(ev) == len(pair_events(hook, "A", df, cfg))
    rows = df.reset_index(drop=True).set_index("date").loc[ev["date"]]
    assert rows["active"].all()
    i = df.index[df["date"] == ev["date"].iloc[0]][0]
    late = (df["close"].iloc[i + 5] - df["close"].iloc[i + 1]) / df["atr"].iloc[i]
    assert ev["late_net_4"].iloc[0] == pytest.approx(late - ev["fee_r"].iloc[0])
    summary = summarize(ev, [hook], cfg)
    assert "late_net_r" in summary and summary["late_net_r"].notna().all()


def test_cluster_bootstrap_diff_finds_a_gap_and_a_null() -> None:
    rng = np.random.default_rng(8)
    weeks = np.repeat(np.arange(60), 5)
    common = np.repeat(rng.normal(0, 1, 60), 5)  # a weekly shock shared by both groups
    a = common + rng.normal(0.5, 1, 300)
    b = common + rng.normal(0.0, 1, 300)
    gap = cluster_bootstrap_diff(a, weeks, b, weeks, n_boot=1000)
    assert gap["lo"] > 0 and gap["diff"] == pytest.approx(a.mean() - b.mean())
    same = cluster_bootstrap_diff(b, weeks, common + rng.normal(0, 1, 300), weeks, n_boot=1000)
    assert same["lo"] < 0 < same["hi"]
