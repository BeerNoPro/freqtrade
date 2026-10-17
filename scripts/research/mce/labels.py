"""
Outcome labels. The ONLY module allowed to look forward in time.

Convention: an event on row t is decided at the close of candle t and entered at close[t].
Outcomes use candles t+1 .. t+h. Returns are expressed in R = price move / ATR[t].
"""

import numpy as np
import pandas as pd


def forward_returns_r(df: pd.DataFrame, horizons: list[int], delay: int = 0) -> pd.DataFrame:
    """Unsigned forward move close[t+delay+h] - close[t+delay] in ATR[t] units, per horizon.
    ``delay`` = 1 is the late-entry check: the same trade entered one candle later."""
    out = {}
    entry = df["close"].shift(-delay)
    for h in horizons:
        out[f"fwd_r_{h}"] = (df["close"].shift(-delay - h) - entry) / df["atr"]
    return pd.DataFrame(out, index=df.index)


def funding_between(prints: pd.Series, start: pd.Series, end: pd.Series) -> np.ndarray:
    """Sum of funding prints stamped in (start, end] for each row: what a long position held
    from ``start`` to ``end`` pays (a short receives it). ``prints``: rates indexed by time."""
    prints = prints.dropna().sort_index()
    cum = np.r_[0.0, prints.cumsum().to_numpy()]
    times = prints.index
    s = times.searchsorted(pd.DatetimeIndex(start), side="right")
    e = times.searchsorted(pd.DatetimeIndex(end), side="right")
    return cum[e] - cum[s]


def triple_barrier(
    df: pd.DataFrame,
    rows: np.ndarray,
    sides: np.ndarray,
    tp_atr: float,
    sl_atr: float,
    max_bars: int,
) -> pd.DataFrame:
    """
    Triple-barrier outcome for each event row (positional index) and side (+1 long, -1 short).

    label: +1 take-profit first, -1 stop-loss first, 0 time barrier.
    If both barriers are touched inside the same candle the stop is assumed first (conservative).
    ret_r: exit move in R (side-adjusted). mfe_r / mae_r: best / worst excursion in R.
    Events without ``max_bars`` future candles are returned with NaN.
    """
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    atr = df["atr"].to_numpy(dtype=float)
    n = len(df)
    res = np.full((len(rows), 5), np.nan)
    for k, (i, side) in enumerate(zip(rows, sides, strict=True)):
        if i + max_bars >= n or not np.isfinite(atr[i]) or atr[i] <= 0 or side == 0:
            continue
        entry, unit = close[i], atr[i]
        path_hi = (high[i + 1 : i + 1 + max_bars] - entry) / unit
        path_lo = (low[i + 1 : i + 1 + max_bars] - entry) / unit
        fav, adv = (path_hi, -path_lo) if side > 0 else (-path_lo, path_hi)
        hit_tp = np.flatnonzero(fav >= tp_atr)
        hit_sl = np.flatnonzero(adv >= sl_atr)
        t_tp = hit_tp[0] if hit_tp.size else max_bars
        t_sl = hit_sl[0] if hit_sl.size else max_bars
        if t_sl <= t_tp and hit_sl.size:
            label, ret, bars = -1, -sl_atr, t_sl + 1
        elif hit_tp.size:
            label, ret, bars = 1, tp_atr, t_tp + 1
        else:
            label, bars = 0, max_bars
            ret = side * (close[i + max_bars] - entry) / unit
        res[k] = (label, ret, bars, fav[:bars].max(), adv[:bars].max())
    return pd.DataFrame(res, columns=["tb_label", "tb_ret_r", "tb_bars", "mfe_r", "mae_r"])
