import numpy as np
import pandas as pd
import pytest
from mce.archive import parse_metrics_csv
from mce.model import Standardizer, fit_logistic, panel_splits, walk_forward_predict
from mce.setups import expected_r, r_metrics, select_setups


def _logit_data(n: int, seed: int = 0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    x = rng.normal(0, 1, (n, 3))
    w = np.array([1.0, -0.5, 0.0])
    y = (rng.uniform(size=n) < 1 / (1 + np.exp(-(-0.3 + x @ w)))).astype(float)
    return x, y, w


def test_fit_logistic_recovers_weights_and_shrinks() -> None:
    x, y, w = _logit_data(20000)
    model = fit_logistic(x, y, c=1.0, features=["a", "b", "c"])
    # inputs are already ~standard, so weights are close to the true ones
    assert model.coef[1:] == pytest.approx(w, abs=0.06)
    assert model.coef[0] == pytest.approx(-0.3, abs=0.06)
    assert model.predict(x).mean() == pytest.approx(y.mean(), abs=0.01)
    assert list(model.weights().index) == ["a", "b", "c"]
    strong = fit_logistic(x[:500], y[:500], c=0.001)
    assert np.abs(strong.coef[1:]).max() < 0.2 * np.abs(model.coef[1:]).max()


def test_standardizer_uses_fit_statistics_and_clips() -> None:
    s = Standardizer.fit(np.array([[0.0, 5.0], [2.0, 5.0]]))
    out = s.transform(np.array([[1.0, 5.0], [100.0, 5.0]]))
    assert out[0].tolist() == [0.0, 0.0]  # constant column -> std 1, no division by zero
    assert out[1, 0] == 5.0  # clipped


def test_panel_splits_purge_and_expand() -> None:
    times = pd.date_range("2024-01-01", periods=100, freq="4h", tz="UTC")
    panel_times = pd.DatetimeIndex(np.repeat(times, 3))  # 3 coins per timestamp
    first = times[40]
    splits = list(panel_splits(panel_times, first, test_bars=20, label_horizon=5, embargo=2))
    assert len(splits) == 3
    for k, (train, test) in enumerate(splits):
        t_train, t_test = panel_times[train], panel_times[test]
        assert t_test.min() == times[40 + 20 * k] and len(test) == 20 * 3
        assert t_train.max() < t_test.min() - pd.Timedelta(hours=4 * 7) + pd.Timedelta(hours=4)
        assert t_train.min() == times[0]  # expanding window


def test_walk_forward_predict_only_on_test_rows() -> None:
    x, y, _ = _logit_data(600, seed=1)
    times = pd.date_range("2024-01-01", periods=200, freq="4h", tz="UTC")
    panel = pd.DataFrame(x, columns=["a", "b", "c"])
    panel["y"] = y
    panel["time"] = np.repeat(times, 3)
    panel.loc[5, "a"] = np.nan  # incomplete row: never trained on
    panel.loc[450, "b"] = np.nan  # incomplete test row: no prediction
    splits = list(panel_splits(panel["time"], times[100], 50, label_horizon=3))
    pred, folds = walk_forward_predict(panel, ["a", "b", "c"], "y", splits)
    tested = np.concatenate([f.test for f in folds])
    assert np.isnan(np.delete(pred, tested)).all() and np.isfinite(pred[tested]).all()
    assert 5 not in folds[0].train and np.isnan(pred[450])


def _cand(rows: list[tuple]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["time", "pair", "side", "ev", "exit_time"])
    for col in ("time", "exit_time"):
        df[col] = pd.Timestamp("2025-01-01", tz="UTC") + pd.to_timedelta(df[col], unit="h")
    return df


def test_select_setups_rules() -> None:
    cand = _cand(
        [
            (0, "A", 1, 0.5, 8),
            (0, "A", -1, 0.6, 8),  # same pair, higher ev -> this side wins
            (0, "B", 1, 0.4, 4),
            (0, "C", 1, 0.3, 4),  # cap 2 reached
            (0, "D", 1, 0.1, 4),  # below ev_min
            (4, "C", 1, 0.3, 12),  # B closed at 4 -> slot free
            (4, "A", 1, 0.9, 12),  # A still open until 8
        ]
    )
    out = select_setups(cand, ev_min=0.2, max_open=2)
    picked = list(zip(out["time"].dt.hour, out["pair"], out["side"], strict=True))
    assert picked == [(0, "A", -1), (0, "B", 1), (4, "C", 1)]


def test_expected_r_and_r_metrics() -> None:
    assert expected_r(0.4, 2.0, -0.9, 0.1) == pytest.approx(0.8 - 0.54 - 0.1)
    m = r_metrics(pd.Series([-1.0, 2.0, -1.0, -1.0, 2.0]))
    assert m["n"] == 5 and m["total_r"] == pytest.approx(1.0)
    assert m["profit_factor"] == pytest.approx(4 / 3)
    assert m["max_dd_r"] == pytest.approx(-2.0)  # 1 -> -1 after two losses from the peak of 1
    assert r_metrics(pd.Series([-1.0, -1.0]))["max_dd_r"] == pytest.approx(-2.0)  # from zero


def test_parse_metrics_csv_keeps_hourly_snapshots() -> None:
    head = (
        "create_time,symbol,sum_open_interest,sum_open_interest_value,"
        "count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,"
        "count_long_short_ratio,sum_taker_long_short_vol_ratio\n"
    )
    rows = [
        f"2025-06-01 00:{m:02d}:00,BTCUSDT,1,{100 + m},1.2,1.5,1.1,0.9\n" for m in range(0, 60, 5)
    ]
    df = parse_metrics_csv(head + "".join(rows))
    assert len(df) == 1 and df["date"].iloc[0] == pd.Timestamp("2025-06-01 00:50", tz="UTC")
    assert df["oi_value"].iloc[0] == 150.0 and df["top_ls_position"].iloc[0] == 1.5
    assert set(df.columns) == {
        "date",
        "oi_value",
        "top_ls_position",
        "top_ls_account",
        "global_ls_account",
        "taker_ls",
    }
