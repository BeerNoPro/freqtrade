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


def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    return df.set_index("date").resample(rule).agg(agg).dropna().reset_index()


def _synthetic_sources(seed: int, n_1h: int) -> dict[tuple[str, str, str], pd.DataFrame]:
    rng = np.random.default_rng(seed + 100)
    h1 = make_ohlcv(n_1h, seed=seed)
    sources: dict[tuple[str, str, str], pd.DataFrame] = {
        ("1h", "futures", "ohlcv"): h1,
        ("4h", "futures", "ohlcv"): _resample(h1, "4h"),
        ("1d", "futures", "ohlcv"): _resample(h1, "1D"),
    }
    for tf in ("1h", "4h"):
        base = sources[(tf, "futures", "ohlcv")]
        n = len(base)
        sources[(tf, "mark", "ohlcv")] = base.assign(
            close=base["close"] * (1 + rng.normal(0, 1e-4, n))
        )
        sources[(tf, "index", "ohlcv")] = base.assign(
            close=base["close"] * (1 + rng.normal(0, 1e-4, n))
        )
        sources[(tf, "premiumIndex", "ohlcv")] = base.assign(close=rng.normal(0, 5e-4, n))
    funding_dates = pd.date_range(h1["date"].iloc[0], h1["date"].iloc[-1], freq="8h")
    sources[("1h", "funding_rate", "funding")] = pd.DataFrame(
        {"date": funding_dates, "funding_rate": rng.normal(1e-4, 1e-4, len(funding_dates))}
    )
    return sources


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


@pytest.mark.parametrize(
    "timeframe,n_1h,cut_row,inf_col",
    [
        ("1h", 1600, 900, "close_4h"),
        ("1h", 1600, 1203, "close_4h"),
        ("1h", 1600, 1450, "close_4h"),
        ("4h", 6000, 1000, "close_1d"),
        ("4h", 6000, 1301, "close_1d"),
    ],
)
def test_prepare_pair_is_causal(
    tmp_path: Path, timeframe: str, n_1h: int, cut_row: int, inf_col: str
) -> None:
    pair_src, btc_src = _synthetic_sources(1, n_1h), _synthetic_sources(2, n_1h)
    primary = pair_src[(timeframe, "futures", "ohlcv")]
    known_at = primary["date"].iloc[cut_row] + pd.Timedelta(timeframe)
    full_dir, cut_dir = tmp_path / "full", tmp_path / "cut"
    for d, k in ((full_dir, None), (cut_dir, known_at)):
        _write(d, pair_src, PAIR, k)
        _write(d, btc_src, BTC, k)

    full = prepare_pair(PAIR, full_dir, btc_context(full_dir, timeframe), timeframe)
    cut = prepare_pair(PAIR, cut_dir, btc_context(cut_dir, timeframe), timeframe)

    assert len(cut) == cut_row + 1
    assert cut["funding_z"].notna().any() and cut[inf_col].notna().any()
    assert cut["regime"].notna().any() and cut["premium"].notna().any()
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


def test_talib_indicators_are_causal(ohlcv: pd.DataFrame) -> None:
    import talib.abstract as ta

    def frame(d: pd.DataFrame) -> pd.DataFrame:
        macd = ta.MACD(d)
        bb = ta.BBANDS(d, timeperiod=20, nbdevup=2.0, nbdevdn=2.0)
        return pd.DataFrame(
            {
                "rsi": ta.RSI(d, timeperiod=14),
                "macd": macd["macd"],
                "macdsignal": macd["macdsignal"],
                "bb_lower": bb["lowerband"],
                "plus_di": ta.PLUS_DI(d),
                "engulfing": ta.CDLENGULFING(d),
                "hammer": ta.CDLHAMMER(d),
            },
            index=d.index,
        )

    full = frame(ohlcv)
    for n in (300, 1111, 1999):
        pd.testing.assert_frame_equal(full.iloc[:n], frame(ohlcv.iloc[:n]), check_dtype=False)
