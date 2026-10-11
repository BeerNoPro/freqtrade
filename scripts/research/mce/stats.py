"""Inference and calibration helpers (numpy/scipy only)."""

import numpy as np
import pandas as pd


def cluster_bootstrap_mean(
    values: np.ndarray,
    clusters: np.ndarray,
    n_boot: int = 2000,
    alpha: float = 0.05,
    seed: int = 0,
) -> dict[str, float]:
    """
    Mean with a block (cluster) bootstrap CI. Clusters are contiguous time blocks
    (e.g. calendar weeks) so that autocorrelation, overlapping labels and cross-pair
    co-movement inside a block are resampled together.
    p is the two-sided bootstrap p-value for mean == 0 (percentile method).
    """
    values = np.asarray(values, dtype=float)
    mask = np.isfinite(values)
    values, clusters = values[mask], np.asarray(clusters)[mask]
    if values.size == 0:
        return {"mean": np.nan, "lo": np.nan, "hi": np.nan, "p": np.nan, "n_blocks": 0}
    codes, uniq = pd.factorize(clusters)
    k = len(uniq)
    sums = np.bincount(codes, weights=values, minlength=k)
    counts = np.bincount(codes, minlength=k).astype(float)
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, k, size=(n_boot, k))
    boot = sums[draws].sum(axis=1) / counts[draws].sum(axis=1)
    lo, hi = np.quantile(boot, [alpha / 2, 1 - alpha / 2])
    p_le = (np.sum(boot <= 0) + 1) / (n_boot + 1)
    p_ge = (np.sum(boot >= 0) + 1) / (n_boot + 1)
    return {
        "mean": float(values.mean()),
        "lo": float(lo),
        "hi": float(hi),
        "p": float(min(1.0, 2 * min(p_le, p_ge))),
        "n_blocks": k,
    }


def cluster_bootstrap_diff(
    a: np.ndarray,
    a_clusters: np.ndarray,
    b: np.ndarray,
    b_clusters: np.ndarray,
    n_boot: int = 2000,
    alpha: float = 0.05,
    seed: int = 0,
) -> dict[str, float]:
    """
    mean(a) - mean(b) with a CI from resampling the shared calendar clusters (e.g. weeks): a
    drawn week brings its rows of both groups, so common shocks hit both sides of the
    difference. Draws where a group has no row are skipped.
    """
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    keep_a, keep_b = np.isfinite(a), np.isfinite(b)
    a, a_clusters = a[keep_a], np.asarray(a_clusters)[keep_a]
    b, b_clusters = b[keep_b], np.asarray(b_clusters)[keep_b]
    codes, uniq = pd.factorize(np.r_[a_clusters, b_clusters])
    ca, cb, k = codes[: len(a)], codes[len(a) :], len(uniq)
    sa, na = np.bincount(ca, a, k), np.bincount(ca, minlength=k).astype(float)
    sb, nb = np.bincount(cb, b, k), np.bincount(cb, minlength=k).astype(float)
    draws = np.random.default_rng(seed).integers(0, k, size=(n_boot, k))
    na_d, nb_d = na[draws].sum(axis=1), nb[draws].sum(axis=1)
    ok = (na_d > 0) & (nb_d > 0)
    boot = sa[draws].sum(axis=1)[ok] / na_d[ok] - sb[draws].sum(axis=1)[ok] / nb_d[ok]
    lo, hi = np.quantile(boot, [alpha / 2, 1 - alpha / 2])
    p = min(1.0, 2 * min(np.mean(boot <= 0), np.mean(boot >= 0)) + 1 / (len(boot) + 1))
    return {"diff": float(a.mean() - b.mean()), "lo": float(lo), "hi": float(hi), "p": float(p)}


def benjamini_hochberg(pvalues: np.ndarray, q: float = 0.05) -> tuple[np.ndarray, np.ndarray]:
    """Return (rejected mask, BH-adjusted p-values). NaNs are never rejected."""
    p = np.asarray(pvalues, dtype=float)
    adj = np.full_like(p, np.nan)
    ok = np.isfinite(p)
    m = int(ok.sum())
    if m == 0:
        return np.zeros_like(p, dtype=bool), adj
    pv = p[ok]
    order = np.argsort(pv)
    ranked = pv[order] * m / np.arange(1, m + 1)
    ranked = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(m)
    out[order] = np.minimum(ranked, 1.0)
    adj[ok] = out
    return np.where(ok, adj <= q, False), adj


def brier(prob: np.ndarray, outcome: np.ndarray) -> float:
    return float(np.mean((np.asarray(prob, float) - np.asarray(outcome, float)) ** 2))


def log_loss(prob: np.ndarray, outcome: np.ndarray, eps: float = 1e-6) -> float:
    p = np.clip(np.asarray(prob, float), eps, 1 - eps)
    y = np.asarray(outcome, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def brier_skill(prob: np.ndarray, outcome: np.ndarray, base_rate: float | None = None) -> float:
    """1 - Brier / Brier(climatology). base_rate should come from data before the forecasts."""
    y = np.asarray(outcome, float)
    base = y.mean() if base_rate is None else base_rate
    ref = brier(np.full_like(y, base), y)
    return float(1 - brier(prob, y) / ref) if ref > 0 else np.nan


def reliability(prob: np.ndarray, outcome: np.ndarray, bins: int = 10) -> pd.DataFrame:
    """Reliability table: mean forecast vs observed frequency per probability bin."""
    data = pd.DataFrame({"p": prob, "y": outcome}).dropna()
    data["bin"] = pd.cut(data["p"], np.linspace(0, 1, bins + 1), include_lowest=True)
    table = data.groupby("bin", observed=True).agg(
        n=("y", "size"), p_mean=("p", "mean"), y_rate=("y", "mean")
    )
    table["gap"] = table["y_rate"] - table["p_mean"]
    return table.reset_index()


def max_reliability_gap(table: pd.DataFrame, min_n: int = 30) -> float:
    big = table[table["n"] >= min_n]
    return float(big["gap"].abs().max()) if len(big) else np.nan


def fee_in_r(entry_price: np.ndarray, atr: np.ndarray, round_trip_fee: float) -> np.ndarray:
    """Round-trip fee expressed in R (ATR units) at the entry price."""
    return round_trip_fee * np.asarray(entry_price, float) / np.asarray(atr, float)
