"""Causality: values at row t must not change when data after t is added."""

import pandas as pd
import pytest
from mce.features import add_core_features, rolling_pct_rank, rolling_zscore
from mce.hooks import Hook
from mce.regime import RegimeRules, add_regime


@pytest.mark.parametrize("cut_row", [800, 1500])
def test_features_and_regime_are_causal(ohlcv: pd.DataFrame, cut_row: int) -> None:
    rules = RegimeRules(vol_window=300)
    full = add_regime(add_core_features(ohlcv), rules)
    cut = add_regime(add_core_features(ohlcv.iloc[: cut_row + 1]), rules)
    pd.testing.assert_frame_equal(full.iloc[: cut_row + 1], cut, rtol=1e-9)


def test_primitives_are_causal(ohlcv: pd.DataFrame) -> None:
    n = 1200
    for fn in (
        lambda d: rolling_pct_rank(d["close"], 300),
        lambda d: rolling_zscore(d["close"], 300),
    ):
        pd.testing.assert_series_equal(fn(ohlcv).iloc[:n], fn(ohlcv.iloc[:n]))


def test_hook_signal_is_causal(ohlcv: pd.DataFrame) -> None:
    def squeeze_break(df: pd.DataFrame, p: dict) -> pd.Series:
        atr_pct = (df["high"] - df["low"]) / df["close"]
        squeezed = rolling_pct_rank(atr_pct, 200).shift(1) <= p["pct_max"]
        prior_high = df["high"].rolling(p["lookback"]).max().shift(1)
        return (df["close"] > prior_high).astype(int).where(squeezed, 0)

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
