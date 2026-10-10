from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from mce import journal
from mce.event_study import StudyConfig, _dedupe, run, summarize
from mce.features import add_core_features
from mce.hooks import Hook, HookRegistry
from mce.labels import forward_returns_r, triple_barrier
from mce.regime import RegimeRules, add_regime
from mce.stats import (
    benjamini_hochberg,
    brier_skill,
    cluster_bootstrap_mean,
    max_reliability_gap,
    reliability,
)
from mce.walkforward import purged_walk_forward


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


def test_calibration_metrics() -> None:
    rng = np.random.default_rng(4)
    prob = rng.uniform(0, 1, 5000)
    y = (rng.uniform(0, 1, 5000) < prob).astype(int)
    assert brier_skill(prob, y) > 0.2
    assert max_reliability_gap(reliability(prob, y)) < 0.06
    assert brier_skill(np.full(5000, y.mean()), y) == pytest.approx(0, abs=1e-12)


def test_purged_walk_forward_no_overlap() -> None:
    splits = list(purged_walk_forward(1000, 300, 100, label_horizon=24, embargo=5))
    assert len(splits) == 7
    for train, test in splits:
        assert train.max() + 24 + 5 < test.min()


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


def test_journal_record_and_score(tmp_path: Path) -> None:
    conn = journal.connect(tmp_path / "j.sqlite")
    a = journal.Assessment(
        candle_date="2026-01-01T00:00:00+00:00",
        pair="BTC/USDT:USDT",
        hook_id="x@v1",
        fingerprint="abc",
        regime="up|normal",
        side=1,
        prob=0.6,
        horizon=2,
        timeframe="1h",
        entry=100.0,
        atr=2.0,
        invalidation=98.0,
    )
    assert journal.record(conn, a) is True
    assert journal.record(conn, a) is False  # idempotent

    def fetch(pair: str, tf: str, after: str, n: int) -> pd.DataFrame:
        return pd.DataFrame({"high": [101, 105], "low": [97.5, 100], "close": [100.5, 104]})

    assert journal.score_pending(conn, fetch) == 1
    assert journal.score_pending(conn, fetch) == 0
    row = journal.scored(conn).iloc[0]
    assert row["ret_r"] == pytest.approx(2.0) and row["hit"] == 1 and row["invalidated"] == 1
    assert row["brier"] == pytest.approx(0.16)
