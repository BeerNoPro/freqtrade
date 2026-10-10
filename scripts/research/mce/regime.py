"""
Transparent, rule-based regimes. Causal: each label uses only closed candles up to row t.

trend: up / down / range   (EMA50 slope in ATR units + ADX)
vol:   low / normal / high (trailing percentile of ATR%)
is_shock: True when the last candle moved more than ``shock_atr`` prior ATRs
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

from mce.features import rolling_pct_rank


@dataclass(frozen=True)
class RegimeRules:
    slope_min: float = 0.5  # EMA50 change over 10 candles, in ATR
    adx_min: float = 20.0
    vol_window: int = 720  # 30 days of 1h candles
    vol_low: float = 0.2
    vol_high: float = 0.8
    shock_atr: float = 3.0


def trend_state(df: pd.DataFrame, rules: RegimeRules) -> pd.Series:
    strong = df["adx"] >= rules.adx_min
    up = strong & (df["ema50_slope"] >= rules.slope_min)
    down = strong & (df["ema50_slope"] <= -rules.slope_min)
    state = np.select([up, down], ["up", "down"], default="range")
    state = pd.Series(state, index=df.index, dtype=object)
    return state.where(df["adx"].notna() & df["ema50_slope"].notna())


def vol_state(df: pd.DataFrame, rules: RegimeRules) -> pd.Series:
    pct = rolling_pct_rank(df["atr_pct"], rules.vol_window)
    state = np.select(
        [pct <= rules.vol_low, pct >= rules.vol_high], ["low", "high"], default="normal"
    )
    return pd.Series(state, index=df.index, dtype=object).where(pct.notna())


def add_regime(
    df: pd.DataFrame, rules: RegimeRules | None = None, prefix: str = ""
) -> pd.DataFrame:
    """Add ``trend``, ``vol``, ``is_shock`` and ``regime`` columns (with ``prefix``)."""
    rules = rules or RegimeRules()
    out = df.copy()
    out[f"{prefix}trend"] = trend_state(out, rules)
    out[f"{prefix}vol"] = vol_state(out, rules)
    out[f"{prefix}is_shock"] = out["shock"].abs() >= rules.shock_atr
    out[f"{prefix}regime"] = out[f"{prefix}trend"] + "|" + out[f"{prefix}vol"]
    return out


def regime_separation(labels: pd.Series, outcome: pd.Series) -> dict[str, float]:
    """
    Do regimes differ in a future outcome (e.g. |fwd return| or fwd return)?
    Kruskal-Wallis across groups; returns statistic, p-value and epsilon-squared effect size.
    Note: p-values are optimistic with overlapping outcomes; read the effect size first.
    """
    data = pd.DataFrame({"label": labels, "y": outcome}).dropna()
    groups = [g["y"].to_numpy() for _, g in data.groupby("label") if len(g) >= 30]
    if len(groups) < 2:
        return {"h": np.nan, "p": np.nan, "eps2": np.nan}
    h, p = stats.kruskal(*groups)
    n = sum(len(g) for g in groups)
    return {"h": float(h), "p": float(p), "eps2": float(h / (n - 1))}
