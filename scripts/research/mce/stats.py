"""Inference helpers: cluster bootstrap, Benjamini-Hochberg, fees in R."""

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


def fee_in_r(entry_price: np.ndarray, atr: np.ndarray, round_trip_fee: float) -> np.ndarray:
    """Round-trip fee expressed in R (ATR units) at the entry price."""
    return round_trip_fee * np.asarray(entry_price, float) / np.asarray(atr, float)
