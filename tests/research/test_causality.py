"""Causality: values at row t must not change when data after t is added."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from mce.data import btc_context, prepare_pair
from mce.features import add_core_features, breakout, rolling_pct_rank, rolling_zscore
from mce.hooks import Hook
from mce.regime import RegimeRules, add_regime

from freqtrade.data.history import get_datahandler
from freqtrade.enums import CandleType
from tests.research.conftest import make_ohlcv


PAIR = "AAA/USDT:USDT"
BTC = "BTC/USDT:USDT"
N_1H = 1600


def _resample_4h(df: pd.DataFrame) -> pd.DataFrame:
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    return df.set_index("date").resample("4h").agg(agg).dropna().reset_index()


def _synthetic_sources(seed: int) -> dict[tuple[str, str, str], pd.DataFrame]:
    rng = np.random.default_rng(seed + 100)
    h1 = make_ohlcv(N_1H, seed=seed)
    mark = h1.assign(close=h1["close"] * (1 + rng.normal(0, 1e-4, N_1H)))
    index = h1.assign(close=h1["close"] * (1 + rng.normal(0, 1e-4, N_1H)))
    premium = h1.assign(close=rng.normal(0, 5e-4, N_1H))
    funding_dates = pd.date_range(h1["date"].iloc[0], h1["date"].iloc[-1], freq="8h")
    funding = pd.DataFrame(
        {"date": funding_dates, "funding_rate": rng.normal(1e-4, 1e-4, len(funding_dates))}
    )
    return {
        ("1h", "futures", "ohlcv"): h1,
        ("4h", "futures", "ohlcv"): _resample_4h(h1),
        ("1h", "mark", "ohlcv"): mark,
        ("1h", "index", "ohlcv"): index,
        ("1h", "premiumIndex", "ohlcv"): premium,
        ("1h", "funding_rate", "funding"): funding,
    }


def _write(datadir: Path, sources: dict, pair: str, known_at: pd.Timestamp | None) -> None:
    """Store sources, keeping only what is known (closed / printed) at ``known_at``."""
    datadir.mkdir(parents=True, exist_ok=True)
    handler = get_datahandler(datadir, "feather")
    for (tf, ctype, kind), df in sources.items():
        if known_at is not None:
            if kind == "funding":
                df = df[df["date"] <= known_at]
            else:
                df = df[df["date"] + pd.Timedelta(tf) <= known_at]
        handler.ohlcv_store(pair, tf, df.reset_index(drop=True), CandleType.from_string(ctype))


@pytest.mark.parametrize("cut_row", [900, 1203, 1450])
def test_prepare_pair_is_causal(tmp_path: Path, cut_row: int) -> None:
    pair_src, btc_src = _synthetic_sources(1), _synthetic_sources(2)
    known_at = pair_src[("1h", "futures", "ohlcv")]["date"].iloc[cut_row] + pd.Timedelta("1h")
    full_dir, cut_dir = tmp_path / "full", tmp_path / "cut"
    for d, k in ((full_dir, None), (cut_dir, known_at)):
        _write(d, pair_src, PAIR, k)
        _write(d, btc_src, BTC, k)

    full = prepare_pair(PAIR, full_dir, btc_context(full_dir))
    cut = prepare_pair(PAIR, cut_dir, btc_context(cut_dir))

    assert len(cut) == cut_row + 1
    assert cut["funding_z"].notna().any() and cut["close_4h"].notna().any()
    pd.testing.assert_frame_equal(full.iloc[: len(cut)], cut, check_dtype=False, rtol=1e-9)


@pytest.mark.parametrize("cut_row", [800, 1500])
def test_features_and_regime_are_causal(ohlcv: pd.DataFrame, cut_row: int) -> None:
    rules = RegimeRules(vol_window=300)
    full = add_regime(add_core_features(ohlcv), rules)
    cut = add_regime(add_core_features(ohlcv.iloc[: cut_row + 1]), rules)
    pd.testing.assert_frame_equal(full.iloc[: cut_row + 1], cut, rtol=1e-9)


def test_primitives_are_causal(ohlcv: pd.DataFrame) -> None:
    n = 1200
    for fn in (
        lambda d: breakout(d, 24),
        lambda d: rolling_pct_rank(d["close"], 300),
        lambda d: rolling_zscore(d["close"], 300),
    ):
        pd.testing.assert_series_equal(fn(ohlcv).iloc[:n], fn(ohlcv.iloc[:n]))


def test_hook_signal_is_causal(ohlcv: pd.DataFrame) -> None:
    def squeeze_break(df: pd.DataFrame, p: dict) -> pd.Series:
        atr_pct = (df["high"] - df["low"]) / df["close"]
        squeezed = rolling_pct_rank(atr_pct, 200).shift(1) <= p["pct_max"]
        return breakout(df, p["lookback"]).where(squeezed, 0)

    hook = Hook("t", 1, "test", squeeze_break, {"pct_max": 0.3, "lookback": 12})
    full = hook.signal(ohlcv)
    assert full.abs().sum() > 0
    for n in (500, 1000, 1999):
        pd.testing.assert_series_equal(full.iloc[:n], hook.signal(ohlcv.iloc[:n]))
