"""
Load Freqtrade feather data and build one causal frame per pair on a primary timeframe.

Alignment rules (row t = candle opened at t on the primary timeframe, decided at its close):
- mark / index / premiumIndex candles of the same timeframe close with row t -> same date.
- the informative timeframe (1h -> 4h, 4h -> 1d) goes through ``merge_informative_pair``,
  so only the last *closed* informative candle is visible. Columns get a ``_<tf>`` suffix.
- funding_rate prints at settlement time T -> visible to row t when T <= close time of t.
- BTC context on the primary timeframe -> same date (closes together with row t).
"""

from pathlib import Path

import pandas as pd

from freqtrade.data.history import load_pair_history
from freqtrade.enums import CandleType
from freqtrade.exchange import timeframe_to_minutes
from freqtrade.strategy import merge_informative_pair
from mce.features import add_core_features, rolling_zscore
from mce.regime import RegimeRules, add_regime


INFORMATIVE = {"1h": "4h", "4h": "1d"}
FUNDING_Z_PRINTS = 90  # ~30 days of 8h funding prints


def tf_delta(timeframe: str) -> pd.Timedelta:
    return pd.Timedelta(minutes=timeframe_to_minutes(timeframe))


def window_30d(timeframe: str) -> int:
    """Number of candles in 30 days on ``timeframe``."""
    return int(pd.Timedelta(days=30) / tf_delta(timeframe))


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
    right["date"] = right["date"].astype(df["date"].dtype)
    return df.merge(right, on="date", how="left")


def add_derivatives(df: pd.DataFrame, pair: str, datadir: Path, timeframe: str) -> pd.DataFrame:
    mark = load(pair, timeframe, datadir, "mark")
    index = load(pair, timeframe, datadir, "index")
    premium = load(pair, timeframe, datadir, "premiumIndex")
    df = _merge_same_candle(df, mark, {"close": "mark_close"})
    df = _merge_same_candle(df, index, {"close": "index_close"})
    df = _merge_same_candle(df, premium, {"close": "premium"})
    df["basis"] = df["mark_close"] / df["index_close"] - 1
    df["premium_z"] = rolling_zscore(df["premium"], window_30d(timeframe))

    funding = load(pair, "1h", datadir, "funding_rate")
    if funding.empty:
        df["funding_rate"] = df["funding_z"] = float("nan")
        return df
    fr = funding[["date", "funding_rate"]].sort_values("date").copy()
    fr["funding_z"] = rolling_zscore(fr["funding_rate"], FUNDING_Z_PRINTS)
    df["_decided_at"] = df["date"] + tf_delta(timeframe)
    fr["date"] = fr["date"].astype(df["_decided_at"].dtype)
    df = pd.merge_asof(
        df.sort_values("_decided_at"),
        fr.rename(columns={"date": "_funding_at"}),
        left_on="_decided_at",
        right_on="_funding_at",
        direction="backward",
    )
    return df.drop(columns=["_decided_at", "_funding_at"])


def add_informative(df: pd.DataFrame, pair: str, datadir: Path, timeframe: str) -> pd.DataFrame:
    inf_tf = INFORMATIVE.get(timeframe)
    if inf_tf is None:
        return df
    inf = load(pair, inf_tf, datadir)
    if inf.empty:
        return df
    inf = add_core_features(inf)
    inf = add_regime(inf, RegimeRules(vol_window=window_30d(inf_tf)))
    inf["ret6"] = inf["close"] / inf["close"].shift(6) - 1
    inf["ret42"] = inf["close"] / inf["close"].shift(42) - 1
    keep = ["date", "close", "atr", "ema20", "ema50", "ema50_slope", "adx"]
    keep += ["trend", "vol", "regime", "ret6", "ret42"]
    return merge_informative_pair(df, inf[keep], timeframe, inf_tf, ffill=True)


def btc_context(datadir: Path, timeframe: str = "1h") -> pd.DataFrame:
    rules = RegimeRules(vol_window=window_30d(timeframe))
    btc = add_regime(add_core_features(load("BTC/USDT:USDT", timeframe, datadir)), rules)
    cols = ["shock", "ret1", "trend", "vol", "regime", "atr"]
    return btc[["date", *cols]].rename(columns={c: f"btc_{c}" for c in cols})


def prepare_pair(
    pair: str, datadir: Path, btc: pd.DataFrame | None = None, timeframe: str = "1h"
) -> pd.DataFrame:
    """Full causal feature frame for one pair (no forward-looking columns)."""
    df = load(pair, timeframe, datadir)
    if df.empty:
        return df
    df = add_regime(add_core_features(df), RegimeRules(vol_window=window_30d(timeframe)))
    df = add_informative(df, pair, datadir, timeframe)
    df = add_derivatives(df, pair, datadir, timeframe)
    if btc is not None:
        df = df.merge(btc, on="date", how="left")
    return df.sort_values("date").reset_index(drop=True)
