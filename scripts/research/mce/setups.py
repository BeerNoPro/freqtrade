"""
Turn model probabilities into trade setups and score them in R.

A candidate on row t is decided at the close of candle t and entered at close[t]; its outcome is
the triple-barrier result already joined to the row (``mce.labels.triple_barrier``), and it stays
open until ``exit_time``.
"""

import numpy as np
import pandas as pd


def expected_r(
    p_tp: np.ndarray | pd.Series, tp_r: float, mean_not_tp: float, fee_r: np.ndarray | pd.Series
) -> np.ndarray | pd.Series:
    """Expected R after fees: take-profit with probability p, otherwise the average non-TP R."""
    return p_tp * tp_r + (1 - p_tp) * mean_not_tp - fee_r


def select_setups(cand: pd.DataFrame, ev_min: float, max_open: int) -> pd.DataFrame:
    """
    Pick setups from candidates (columns ``time``, ``pair``, ``side``, ``ev``, ``exit_time``).

    Walk forward in time; at each time, setups whose ``exit_time`` has passed free their slot,
    then candidates with ``ev >= ev_min`` are taken by descending ``ev`` while the pair has no
    open setup and fewer than ``max_open`` setups are open. Only one side per pair is taken.
    """
    pool = cand[cand["ev"] >= ev_min].sort_values(["time", "ev"], ascending=[True, False])
    open_until: dict[str, pd.Timestamp] = {}
    taken = []
    for time, group in pool.groupby("time", sort=True):
        open_until = {p: t for p, t in open_until.items() if t > time}
        for idx, row in group.iterrows():
            if len(open_until) >= max_open:
                break
            if row["pair"] in open_until:
                continue
            open_until[row["pair"]] = row["exit_time"]
            taken.append(idx)
    return cand.loc[taken].sort_values(["time", "pair"])


def r_metrics(r: pd.Series) -> dict[str, float]:
    """Per-setup R results in entry order -> expectancy, hit rate, profit factor, total, max DD."""
    r = r.dropna()
    gains, losses = r[r > 0].sum(), -r[r < 0].sum()
    equity = r.cumsum()
    return {
        "n": float(len(r)),
        "expectancy_r": float(r.mean()) if len(r) else np.nan,
        "hit_rate": float((r > 0).mean()) if len(r) else np.nan,
        "profit_factor": float(gains / losses) if losses > 0 else np.inf,
        "total_r": float(r.sum()),
        "max_dd_r": float((equity - equity.cummax().clip(lower=0)).min()) if len(r) else 0.0,
    }
