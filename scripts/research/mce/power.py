"""
Statistical power of the project's pass rules (a positive control before any study is run): a
known edge is planted into outcome-blind noise and the gate is applied as the real study would.
The noise is always demeaned first, so no output can depend on the sign or the size of the real
outcome: adding a constant to the input leaves every output unchanged (tested). Only counts and
dispersion of the data reach the result.

- Portfolio rules (daily weights), gate of ``.claude/rules/quant-strategy.md``: annual Sharpe >=
  1.0, 5% lower bound of the 20-day block-bootstrap Sharpe > 0, Deflated Sharpe >= 0.5 with the
  cumulative number of strategy trials.
- Event rules, ``thresholds.json`` ``hook_gate``: >= 200 events, two-sided weekly-cluster
  bootstrap p at or below the Benjamini-Hochberg threshold of the first of ``n_tests`` tests
  (alpha / n_tests when a single hypothesis is true), mean net of cost > 0.
- History block (run once, pass when the edge has the same sign): the share of simulations whose
  estimate has the planted sign (``positive``). ``joint_pass`` combines the two blocks, which
  share no data.
"""

import math

import numpy as np
import pandas as pd

from mce.portfolio import block_bootstrap_sharpe, deflated_sharpe
from mce.stats import cluster_bootstrap_mean


DAYS = 365


def demeaned(x: np.ndarray | pd.Series) -> np.ndarray:
    """``x`` minus its mean (missing values ignored and kept)."""
    arr = np.asarray(x, dtype=float)
    return arr - np.nanmean(arr)


def block_resample(x: np.ndarray, block: int, rng: np.random.Generator) -> np.ndarray:
    """Moving-block bootstrap sample of ``x`` (same length): runs of ``block`` consecutive values
    keep volatility clustering and short autocorrelation."""
    n = len(x)
    starts = rng.integers(0, n - block + 1, size=math.ceil(n / block))
    return x[(starts[:, None] + np.arange(block)).ravel()[:n]]


def portfolio_power(
    daily: np.ndarray | pd.Series,
    sharpes: tuple[float, ...],
    n_sims: int = 200,
    block: int = 20,
    n_boot: int = 500,
    n_trials: int = 11,
    sr_std_trials: float = 0.5,
    seed: int = 0,
) -> pd.DataFrame:
    """
    For each true annual Sharpe in ``sharpes``: the share of simulations passing each part of the
    portfolio gate and all of it (``pass``), and the share with a positive sample Sharpe
    (``positive``). A simulation is a block resample of the demeaned daily series plus the daily
    drift giving that Sharpe at the series' own volatility. ``n_trials``: strategy trials counted
    so far plus this one; ``sr_std_trials``: annual std of the Sharpe across trials.
    """
    x = demeaned(daily)
    x = x[np.isfinite(x)]
    sd = x.std(ddof=1)
    rng = np.random.default_rng(seed)
    rows = []
    for s in sharpes:
        mu = s * sd / math.sqrt(DAYS)
        hits = np.zeros((n_sims, 4), dtype=bool)  # ci_lo > 0, sharpe >= 1, dsr >= 0.5, positive
        for i in range(n_sims):
            net = pd.Series(block_resample(x, block, rng) + mu)
            boot = block_bootstrap_sharpe(net, block, n_boot, int(rng.integers(2**31)))
            dsr = deflated_sharpe(net, n_trials, sr_std_trials / math.sqrt(DAYS))["dsr"]
            hits[i] = (boot["lo"] > 0, boot["sharpe"] >= 1.0, dsr >= 0.5, boot["sharpe"] > 0)
        rows.append(
            {
                "sharpe": s,
                "days": len(x),
                "ci_lo_above_0": hits[:, 0].mean(),
                "sharpe_ge_1": hits[:, 1].mean(),
                "dsr_ge_0_5": hits[:, 2].mean(),
                "pass": hits[:, :3].all(axis=1).mean(),
                "positive": hits[:, 3].mean(),
            }
        )
    return pd.DataFrame(rows)


def event_power(
    outcomes: np.ndarray | pd.Series,
    clusters: np.ndarray | pd.Series,
    effects: tuple[float, ...],
    cost: float = 0.0,
    n_tests: int = 1,
    n_sims: int = 200,
    n_boot: int = 1000,
    alpha: float = 0.05,
    min_events: int = 200,
    seed: int = 0,
) -> pd.DataFrame:
    """
    For each planted mean edge in ``effects`` (outcome units, e.g. R or a return): the share of
    simulations passing the event gate (``pass``) and with a positive mean (``positive``). A
    simulation resamples whole clusters (calendar weeks) of the demeaned outcomes with
    replacement and adds the edge. ``cost``: mean cost per event in the same units.
    """
    x = demeaned(outcomes)
    ok = np.isfinite(x)
    x, cl = x[ok], np.asarray(clusters)[ok]
    codes, uniq = pd.factorize(cl)
    groups = [x[codes == j] for j in range(len(uniq))]
    sizes = np.array([len(g) for g in groups])
    rng = np.random.default_rng(seed)
    rows = []
    for e in effects:
        passed = positive = 0
        for _ in range(n_sims):
            pick = rng.integers(0, len(groups), size=len(groups))
            vals = np.concatenate([groups[j] for j in pick]) + e
            labels = np.repeat(np.arange(len(pick)), sizes[pick])
            r = cluster_bootstrap_mean(vals, labels, n_boot, alpha, int(rng.integers(2**31)))
            positive += r["mean"] > 0
            passed += len(x) >= min_events and r["p"] <= alpha / n_tests and r["mean"] > cost
        rows.append(
            {
                "effect": e,
                "events": len(x),
                "clusters": len(groups),
                "pass": passed / n_sims,
                "positive": positive / n_sims,
            }
        )
    return pd.DataFrame(rows)


def joint_pass(p_dev: float, p_hist_same_sign: float) -> float:
    """Probability of passing the pass rule: the gate on the development range and the same sign
    on the history block (independent data)."""
    return p_dev * p_hist_same_sign
