"""
Building blocks of forward tests: complete trade setups from fixed rules, hook events replayed
on point-in-time frames, a two-sided Donchian trend ensemble, and a dose-response test.

Every rule here is causal (decided at a candle close with data up to it). The same functions
serve the live runner and the replay on archived data, so the two can be reconciled setup by
setup (``mce.journal.reconcile``).
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from mce.event_study import _dedupe
from mce.hooks import Hook
from mce.journal import Setup
from mce.labels import funding_between
from mce.live import round_trip_cost
from mce.pit import read_store


# ---------------------------------------------------------------- setups


@dataclass(frozen=True)
class SetupPolicy:
    """Sizing and cost policy of a product setup (fixed, never optimised)."""

    equity: float = 10_000.0  # reference capital in USDT
    risk_pct: float = 0.01  # capital lost at the stop
    max_leverage: int = 5
    maintenance: float = 0.01  # margin kept between the liquidation price and twice the stop
    round_trip_fee: float = 0.0012
    max_cost_r: float = 0.20  # skip the setup when costs eat more of 1R than this


def leverage_cap(stop_frac: float, policy: SetupPolicy) -> int:
    """Highest isolated leverage whose liquidation stays about twice as far as the stop."""
    if not stop_frac > 0:
        return 1
    return int(min(policy.max_leverage, max(1, np.floor(1 / (2 * stop_frac + policy.maintenance)))))


def make_setup(
    policy: SetupPolicy,
    *,
    strategy: str,
    fingerprint: str,
    source: str,
    pair: str,
    timeframe: str,
    candle_date: pd.Timestamp,
    bar: pd.Timedelta,
    side: int,
    entry: float,
    atr: float,
    stop: float,
    target: float | None,
    max_bars: int | None,
    reason: str,
    quote: dict | None = None,
) -> Setup:
    """Setup risking ``risk_pct`` of the reference capital at the stop, capped so the notional
    stays within the leverage cap. ``cost_r`` = fees (+ twice the measured entry impact when a
    live ``quote`` is given) in R; above ``max_cost_r`` the setup is recorded as skipped."""
    risk = abs(entry - stop)
    lev = leverage_cap(risk / entry, policy)
    qty = min(policy.equity * policy.risk_pct / risk, policy.equity * lev / entry)
    slip = quote.get("slip_bps") if quote else None
    cost_r = round_trip_cost(policy.round_trip_fee, slip) * entry / risk
    return Setup(
        strategy=strategy,
        fingerprint=fingerprint,
        source=source,
        pair=pair,
        timeframe=timeframe,
        candle_date=pd.Timestamp(candle_date).isoformat(),
        decided_at=(pd.Timestamp(candle_date) + bar).isoformat(),
        side=int(side),
        entry=float(entry),
        atr=float(atr),
        stop=float(stop),
        target=None if target is None else float(target),
        max_bars=max_bars,
        qty=float(qty),
        notional=float(qty * entry),
        leverage_max=lev,
        cost_r=float(cost_r),
        status="skipped_cost" if cost_r > policy.max_cost_r else "setup",
        reason=reason,
        bid=None if not quote else quote.get("bid"),
        ask=None if not quote else quote.get("ask"),
        slip_bps=None if slip is None or not np.isfinite(slip) else float(slip),
    )


def barrier_levels(
    side: int, entry: float, atr: float, sl_atr: float, tp_atr: float
) -> tuple[float, float]:
    """(stop, target) at ``sl_atr`` / ``tp_atr`` ATRs from the entry."""
    return entry - side * sl_atr * atr, entry + side * tp_atr * atr


# ---------------------------------------------------------------- hook events


def hook_events(
    hook: Hook, frames: dict[str, pd.DataFrame], start: pd.Timestamp, end: pd.Timestamp
) -> pd.DataFrame:
    """Firings of ``hook`` on member bars opened in [start, end), with the event study's
    cooldown, without any outcome. The cooldown runs from ``start``: live and replay must use
    the same start to agree."""
    rows = []
    for pair, f in frames.items():
        f = f[f["date"] < end].reset_index(drop=True)
        sig = hook.signal(f).to_numpy()
        ok = (f["date"] >= start).to_numpy() & f["regime"].notna().to_numpy()
        ok &= f["active"].to_numpy(dtype=bool)
        for i in _dedupe(np.flatnonzero((sig != 0) & ok), hook.cooldown or hook.horizon):
            r = f.iloc[i]
            rows.append(
                {
                    "hook": hook.id,
                    "pair": pair,
                    "date": r["date"],
                    "side": int(sig[i]),
                    "close": float(r["close"]),
                    "atr": float(r["atr"]),
                    "shock": float(r["shock"]),
                    "oi_z": float(r["oi_z"]),
                }
            )
    cols = ["hook", "pair", "date", "side", "close", "atr", "shock", "oi_z"]
    return pd.DataFrame(rows, columns=cols).sort_values(["date", "pair"], ignore_index=True)


def add_funding(
    events: pd.DataFrame, pit: Path, end: pd.Timestamp, horizons: tuple[int, ...], bar: pd.Timedelta
) -> pd.DataFrame:
    """``funding_r_<h>``: funding paid over each holding window in R (positive = cost), and
    ``net_all_<h>`` = ``net_<h>`` minus it. Events need pair, date, side, close, atr, net_<h>."""
    events = events.copy()
    decision = events["date"] + bar
    for h in horizons:
        events[f"funding_r_{h}"] = np.nan
    for pair, idx in events.groupby("pair").groups.items():
        f = read_store(pit, "funding", str(pair), end)
        if f is None or f.empty:
            continue
        prints = f.set_index("date")["funding_rate"]
        ev = events.loc[idx]
        for h in horizons:
            paid = funding_between(prints, decision.loc[idx], decision.loc[idx] + h * bar)
            events.loc[idx, f"funding_r_{h}"] = ev["side"] * paid * ev["close"] / ev["atr"]
    for h in horizons:
        events[f"net_all_{h}"] = events[f"net_{h}"] - events[f"funding_r_{h}"]
    return events


# ---------------------------------------------------------------- Donchian trend ensemble


def donchian_state(close: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Two-sided Donchian on closes. Channel = max / min of the previous ``n`` closes. Long when
    the close breaks above it, short below; the stop starts at the channel midpoint and only
    moves in the trade's favour; a close beyond the stop exits, and may enter the other way at
    the same close. A missing close or channel resets to flat. Returns (state, stop)."""
    s = pd.Series(close, dtype=float)
    hi = s.rolling(n, min_periods=n).max().shift(1).to_numpy()
    lo = s.rolling(n, min_periods=n).min().shift(1).to_numpy()
    mid = (hi + lo) / 2
    state, stop = np.zeros(len(s), dtype=int), np.full(len(s), np.nan)
    pos, level = 0, np.nan
    for t, c in enumerate(s.to_numpy()):
        if not (np.isfinite(c) and np.isfinite(hi[t])):
            pos, level = 0, np.nan
            continue
        if pos == 1:
            level = max(level, mid[t])
            if c < level:
                pos, level = 0, np.nan
        elif pos == -1:
            level = min(level, mid[t])
            if c > level:
                pos, level = 0, np.nan
        if pos == 0:
            if c > hi[t]:
                pos, level = 1, mid[t]
            elif c < lo[t]:
                pos, level = -1, mid[t]
        state[t], stop[t] = pos, level
    return state, stop


@dataclass
class Ensemble:
    signal: pd.DataFrame  # mean sub-model state of the lookbacks with enough history, in [-1, 1]
    long_stop: pd.DataFrame  # lowest stop of the long sub-models: where the whole long exits
    short_stop: pd.DataFrame  # highest stop of the short sub-models


def donchian_ensemble(close: pd.DataFrame, lookbacks: tuple[int, ...]) -> Ensemble:
    """Ensemble of ``donchian_state`` over ``lookbacks`` for every column of daily closes."""
    signal, long_stop, short_stop = {}, {}, {}
    for col in close.columns:
        c = close[col].to_numpy(dtype=float)
        states, stops, valid = [], [], []
        for n in lookbacks:
            st, lv = donchian_state(c, n)
            have = pd.Series(c).rolling(n, min_periods=n).count().shift(1).to_numpy() == n
            states.append(st)
            stops.append(lv)
            valid.append(have & np.isfinite(c))
        st, lv, ok = np.array(states), np.array(stops), np.array(valid)
        count = ok.sum(axis=0)
        with np.errstate(invalid="ignore", divide="ignore"):
            signal[col] = np.where(count > 0, (st * ok).sum(axis=0) / count, 0.0)
        long_lv = np.where(st == 1, lv, np.nan)
        short_lv = np.where(st == -1, lv, np.nan)
        long_stop[col] = _nan_reduce(long_lv, np.nanmin)
        short_stop[col] = _nan_reduce(short_lv, np.nanmax)
    idx = close.index
    return Ensemble(
        pd.DataFrame(signal, index=idx),
        pd.DataFrame(long_stop, index=idx),
        pd.DataFrame(short_stop, index=idx),
    )


def _nan_reduce(a: np.ndarray, fn) -> np.ndarray:
    out = np.full(a.shape[1], np.nan)
    has = np.isfinite(a).any(axis=0)
    out[has] = fn(a[:, has], axis=0)
    return out


def trend_weights(
    signal: pd.DataFrame, vol_scalar: pd.DataFrame, members: pd.DataFrame, slots: int
) -> pd.DataFrame:
    """Target weight per coin: ensemble signal x vol-target scalar / ``slots``, members only."""
    w = signal * vol_scalar.reindex_like(signal) / slots
    return w.where(members.reindex_like(signal).fillna(False).astype(bool)).fillna(0.0)


def trend_trades(weights: pd.DataFrame, ens: Ensemble, close: pd.DataFrame) -> pd.DataFrame:
    """Product view of the trend portfolio: a setup each time a coin's weight takes a new sign
    (entry at that close, stop where the whole ensemble would exit), closed at the first close
    where the sign changes again (exit at that close, ``exit_date`` NaT while still open)."""
    sign = np.sign(weights).fillna(0.0).astype(int)
    rows = []
    for col in weights.columns:
        s = sign[col].to_numpy()
        prev = np.r_[0, s[:-1]]
        for t in np.flatnonzero((s != 0) & (s != prev)):
            side = int(s[t])
            stop = (ens.long_stop if side > 0 else ens.short_stop)[col].iloc[t]
            entry = float(close[col].iloc[t])
            later = np.flatnonzero(s[t + 1 :] != side)
            out = t + 1 + later[0] if later.size else None
            rows.append(
                {
                    "pair": col,
                    "date": weights.index[t],
                    "side": side,
                    "entry": entry,
                    "stop": float(stop),
                    "signal": float(ens.signal[col].iloc[t]),
                    "exit_date": weights.index[out] if out is not None else pd.NaT,
                    "exit_price": float(close[col].iloc[out]) if out is not None else np.nan,
                }
            )
    cols = ["pair", "date", "side", "entry", "stop", "signal", "exit_date", "exit_price"]
    return pd.DataFrame(rows, columns=cols).sort_values(["date", "pair"], ignore_index=True)


# ---------------------------------------------------------------- dose-response


def assign_bins(magnitude: np.ndarray, edges: tuple[float, ...]) -> np.ndarray:
    """Bin index of |magnitude| for left-closed bins starting at each edge (last open-ended);
    -1 below the first edge."""
    return np.digitize(np.abs(magnitude), np.asarray(edges, dtype=float)) - 1


def merge_sparse_bins(bins: np.ndarray, min_n: int) -> np.ndarray:
    """Merge, from the top down, every bin with fewer than ``min_n`` rows into the bin below it,
    then renumber 0..k-1. Rows with bin -1 are left out. Counts only, no outcome."""
    out = bins.copy()
    top = int(out.max()) if (out >= 0).any() else -1
    for b in range(top, 0, -1):
        if (out == b).sum() < min_n:
            out[out == b] = b - 1
    kept = np.unique(out[out >= 0])
    remap = {old: new for new, old in enumerate(kept)}
    return np.array([remap.get(b, -1) for b in out])


def _slopes(means: np.ndarray) -> np.ndarray:
    """OLS slope of each row of bin means on the bin index, finite bins only."""
    x = np.arange(means.shape[1], dtype=float)
    w = np.isfinite(means)
    m = np.where(w, means, 0.0)
    nw = w.sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        xbar = (w * x).sum(axis=1) / nw
        ybar = m.sum(axis=1) / nw
        dx = np.where(w, x - xbar[:, None], 0.0)
        return (dx * (m - ybar[:, None])).sum(axis=1) / (dx**2).sum(axis=1)


def bin_trend_test(
    values: np.ndarray,
    bins: np.ndarray,
    clusters: np.ndarray,
    n_boot: int = 2000,
    alpha: float = 0.05,
    seed: int = 0,
) -> dict:
    """Does the mean of ``values`` rise or fall with the bin index? Slope of an OLS of the bin
    means (each bin weighted equally) on the index, with a CI and a two-sided p from resampling
    whole clusters (calendar weeks)."""
    values, bins = np.asarray(values, dtype=float), np.asarray(bins)
    ok = np.isfinite(values) & (bins >= 0)
    values, bins, clusters = values[ok], bins[ok], np.asarray(clusters)[ok]
    k = int(bins.max()) + 1 if len(bins) else 0
    if k < 2:
        return {"slope": np.nan, "lo": np.nan, "hi": np.nan, "p": np.nan, "means": [], "n": []}
    codes, uniq = pd.factorize(clusters)
    sums = np.zeros((len(uniq), k))
    counts = np.zeros((len(uniq), k))
    np.add.at(sums, (codes, bins), values)
    np.add.at(counts, (codes, bins), 1.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        means = sums.sum(axis=0) / counts.sum(axis=0)
        draws = np.random.default_rng(seed).integers(0, len(uniq), size=(n_boot, len(uniq)))
        boot = _slopes(sums[draws].sum(axis=1) / counts[draws].sum(axis=1))
    boot = boot[np.isfinite(boot)]
    lo, hi = np.quantile(boot, [alpha / 2, 1 - alpha / 2])
    p = min(1.0, 2 * min(np.mean(boot <= 0), np.mean(boot >= 0)) + 1 / (len(boot) + 1))
    return {
        "slope": float(_slopes(means[None, :])[0]),
        "lo": float(lo),
        "hi": float(hi),
        "p": float(p),
        "means": means.tolist(),
        "n": counts.sum(axis=0).astype(int).tolist(),
    }


def holm(pvalues: list[float], alpha: float = 0.05) -> list[bool]:
    """Holm step-down rejections for a small family of tests."""
    order = np.argsort(pvalues)
    reject = [False] * len(pvalues)
    for rank, i in enumerate(order):
        if pvalues[i] > alpha / (len(pvalues) - rank):
            break
        reject[i] = True
    return reject
