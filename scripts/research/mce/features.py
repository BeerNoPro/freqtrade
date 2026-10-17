"""Causal feature builders. Each function uses only rows <= t for the value at row t."""

import numpy as np
import pandas as pd
import talib.abstract as ta


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return pd.Series(ta.ATR(df, timeperiod=period), index=df.index)


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return pd.Series(ta.ADX(df, timeperiod=period), index=df.index)


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def slope_atr(series: pd.Series, atr_series: pd.Series, lag: int) -> pd.Series:
    """Change of ``series`` over ``lag`` rows, in ATR units."""
    return (series - series.shift(lag)) / atr_series


def rolling_pct_rank(series: pd.Series, window: int) -> pd.Series:
    """Percentile (0..1) of the latest value within the trailing window."""
    return series.rolling(window, min_periods=window).rank(pct=True)


def rolling_zscore(series: pd.Series, window: int) -> pd.Series:
    mean = series.rolling(window, min_periods=window).mean()
    std = series.rolling(window, min_periods=window).std()
    return (series - mean) / std.replace(0, np.nan)


def candle_structure(df: pd.DataFrame, atr_series: pd.Series) -> pd.DataFrame:
    """Body/wick shares of the range, range in ATR, and open gap vs previous close in ATR."""
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    top = df[["open", "close"]].max(axis=1)
    bottom = df[["open", "close"]].min(axis=1)
    return pd.DataFrame(
        {
            "body_pct": (df["close"] - df["open"]) / rng,
            "upper_wick_pct": (df["high"] - top) / rng,
            "lower_wick_pct": (bottom - df["low"]) / rng,
            "range_atr": rng / atr_series,
            "gap_atr": (df["open"] - df["close"].shift(1)) / atr_series,
        },
        index=df.index,
    )


def log_return(close: pd.Series, lag: int = 1) -> pd.Series:
    return np.log(close / close.shift(lag))


def add_core_features(df: pd.DataFrame, atr_period: int = 14) -> pd.DataFrame:
    """Add the standard per-pair feature set used by regimes and hooks."""
    out = df.copy()
    out["atr"] = atr(out, atr_period)
    out["atr_pct"] = out["atr"] / out["close"]
    out["adx"] = adx(out, atr_period)
    out["ema20"] = ema(out["close"], 20)
    out["ema50"] = ema(out["close"], 50)
    out["ema50_slope"] = slope_atr(out["ema50"], out["atr"], 10)
    out["ret1"] = log_return(out["close"])
    out["shock"] = (out["close"] - out["close"].shift(1)) / out["atr"].shift(1)
    return pd.concat([out, candle_structure(out, out["atr"])], axis=1)
