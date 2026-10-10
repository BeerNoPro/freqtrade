"""
Load Freqtrade feather data and build one causal 1h frame per pair.

Alignment rules (row t = 1h candle opened at t, decided at its close t+1h):
- mark / index / premiumIndex 1h candles close together with row t -> merged on the same date.
- 4h candles -> ``merge_informative_pair`` (only the last *closed* 4h candle is visible).
- funding_rate prints at settlement time T -> visible to row t when T <= t + 1h.
- BTC 1h context -> same date (closes together with row t).
"""

from pathlib import Path

import pandas as pd

from freqtrade.data.history import load_pair_history
from freqtrade.enums import CandleType
from freqtrade.strategy import merge_informative_pair
from mce.features import add_core_features, rolling_zscore
from mce.regime import RegimeRules, add_regime


TIMEFRAME = "1h"
TF_DELTA = pd.Timedelta(hours=1)


def load(pair: str, timeframe: str, datadir: Path, candle_type: str = "futures") -> pd.DataFrame:
    return load_pair_history(
        pair=pair,
        timeframe=timeframe,
        datadir=datadir,
        candle_type=CandleType.from_string(candle_type),
        fill_up_missing=candle_type == "futures",
    )


def _merge_same_candle(df: pd.DataFrame, other: pd.DataFrame, cols: dict[str, str]) -> pd.DataFrame:
    if other.empty:
        for new in cols.values():
            df[new] = float("nan")
        return df
    right = other[["date", *cols]].rename(columns=cols)
    return df.merge(right, on="date", how="left")


def add_derivatives(df: pd.DataFrame, pair: str, datadir: Path) -> pd.DataFrame:
    mark = load(pair, TIMEFRAME, datadir, "mark")
    index = load(pair, TIMEFRAME, datadir, "index")
    premium = load(pair, TIMEFRAME, datadir, "premiumIndex")
    df = _merge_same_candle(df, mark, {"close": "mark_close"})
    df = _merge_same_candle(df, index, {"close": "index_close"})
    df = _merge_same_candle(df, premium, {"close": "premium"})
    df["basis"] = df["mark_close"] / df["index_close"] - 1
    df["premium_z"] = rolling_zscore(df["premium"], 720)

    funding = load(pair, "1h", datadir, "funding_rate")
    if funding.empty:
        df["funding_rate"] = df["funding_z"] = float("nan")
        return df
    fr = funding[["date", "funding_rate"]].sort_values("date").copy()
    fr["funding_z"] = rolling_zscore(fr["funding_rate"], 90)  # ~30 days of 8h prints
    df["_decided_at"] = df["date"] + TF_DELTA
    fr["date"] = fr["date"].astype(df["_decided_at"].dtype)
    df = pd.merge_asof(
        df.sort_values("_decided_at"),
        fr.rename(columns={"date": "_funding_at"}),
        left_on="_decided_at",
        right_on="_funding_at",
        direction="backward",
    )
    return df.drop(columns=["_decided_at", "_funding_at"])


def add_4h(df: pd.DataFrame, pair: str, datadir: Path) -> pd.DataFrame:
    df4 = load(pair, "4h", datadir)
    if df4.empty:
        return df
    df4 = add_core_features(df4)
    df4 = add_regime(df4, RegimeRules(vol_window=180))
    df4["ret6"] = df4["close"] / df4["close"].shift(6) - 1  # one day of 4h candles
    df4["ret42"] = df4["close"] / df4["close"].shift(42) - 1  # one week
    keep = ["date", "close", "atr", "ema20", "ema50", "ema50_slope", "adx"]
    keep += ["trend", "vol", "regime", "ret6", "ret42"]
    return merge_informative_pair(df, df4[keep], TIMEFRAME, "4h", ffill=True)


def btc_context(datadir: Path) -> pd.DataFrame:
    btc = add_regime(add_core_features(load("BTC/USDT:USDT", TIMEFRAME, datadir)))
    cols = ["shock", "ret1", "trend", "vol", "regime", "atr"]
    return btc[["date", *cols]].rename(columns={c: f"btc_{c}" for c in cols})


def prepare_pair(pair: str, datadir: Path, btc: pd.DataFrame | None = None) -> pd.DataFrame:
    """Full causal feature frame for one pair (no forward-looking columns)."""
    df = load(pair, TIMEFRAME, datadir)
    if df.empty:
        return df
    df = add_regime(add_core_features(df))
    df = add_4h(df, pair, datadir)
    df = add_derivatives(df, pair, datadir)
    if btc is not None:
        df = df.merge(btc, on="date", how="left")
    return df.sort_values("date").reset_index(drop=True)
