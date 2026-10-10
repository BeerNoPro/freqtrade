"""
Event study: for every hook firing, measure what happened next and compare with a
same-regime baseline. Inference uses a weekly block bootstrap and Benjamini-Hochberg.
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from mce.hooks import Hook
from mce.labels import forward_returns_r, triple_barrier
from mce.stats import benjamini_hochberg, cluster_bootstrap_mean, fee_in_r


@dataclass(frozen=True)
class StudyConfig:
    start: pd.Timestamp
    end: pd.Timestamp  # exclusive; labels never read candles at or after this
    horizons: tuple[int, ...] = (1, 4, 12, 24)
    tp_atr: float = 2.0
    sl_atr: float = 1.0
    round_trip_fee: float = 0.0012
    n_boot: int = 2000
    fdr_q: float = 0.05
    gates: dict = field(
        default_factory=lambda: {
            "min_events": 200,
            "min_year_share": 2 / 3,
            "min_pair_share": 0.6,
            "min_pair_events": 10,
        }
    )


def _dedupe(rows: np.ndarray, cooldown: int) -> np.ndarray:
    kept: list[int] = []
    last = -(10**9)
    for r in rows:
        if r - last >= cooldown:
            kept.append(r)
            last = r
    return np.asarray(kept, dtype=int)


def _label_frame(df: pd.DataFrame, cfg: StudyConfig) -> pd.DataFrame:
    """Truncate at the study end so no label reads beyond it, then add forward returns."""
    cut = df[df["date"] < cfg.end].reset_index(drop=True)
    return pd.concat([cut, forward_returns_r(cut, list(cfg.horizons))], axis=1)


def pair_events(hook: Hook, pair: str, df: pd.DataFrame, cfg: StudyConfig) -> pd.DataFrame:
    lab = _label_frame(df, cfg)
    sig = hook.signal(lab).to_numpy()
    in_range = (lab["date"] >= cfg.start).to_numpy() & lab["regime"].notna().to_numpy()
    rows = _dedupe(np.flatnonzero((sig != 0) & in_range), hook.cooldown or hook.horizon)
    if rows.size == 0:
        return pd.DataFrame()
    base = lab.loc[in_range].groupby("regime")[[f"fwd_r_{h}" for h in cfg.horizons]].mean()
    ev = lab.iloc[rows][["date", "close", "atr", "regime", "trend", "vol"]].reset_index(drop=True)
    ev.insert(0, "pair", pair)
    ev.insert(0, "hook", hook.id)
    ev["side"] = sig[rows]
    ev["fee_r"] = fee_in_r(ev["close"], ev["atr"], cfg.round_trip_fee)
    for h in cfg.horizons:
        col = f"fwd_r_{h}"
        signed = ev["side"] * lab[col].to_numpy()[rows]
        baseline = ev["side"] * ev["regime"].map(base[col]).to_numpy()
        ev[f"ret_{h}"] = signed
        ev[f"excess_{h}"] = signed - baseline
        ev[f"net_{h}"] = signed - ev["fee_r"]
    tb = triple_barrier(lab, rows, ev["side"].to_numpy(), cfg.tp_atr, cfg.sl_atr, hook.horizon)
    ev = pd.concat([ev, tb], axis=1)
    ev["tb_net_r"] = ev["tb_ret_r"] - ev["fee_r"]
    return ev


def run(hooks: list[Hook], frames: dict[str, pd.DataFrame], cfg: StudyConfig) -> pd.DataFrame:
    parts = [pair_events(h, p, df, cfg) for h in hooks for p, df in frames.items()]
    parts = [p for p in parts if not p.empty]
    if not parts:
        return pd.DataFrame()
    events = pd.concat(parts, ignore_index=True)
    events["year"] = events["date"].dt.year
    events["week"] = events["date"].dt.to_period("W").astype(str)
    return events


def _sign_share(ev: pd.DataFrame, by: str, col: str, sign: float, min_n: int) -> float:
    g = ev.groupby(by)[col].agg(["mean", "size"])
    g = g[g["size"] >= min_n]
    if g.empty or sign == 0:
        return np.nan
    return float((np.sign(g["mean"]) == sign).mean())


def summarize(events: pd.DataFrame, hooks: list[Hook], cfg: StudyConfig) -> pd.DataFrame:
    """One row per hook x horizon with lift CI, BH-adjusted p and go/no-go gates."""
    gates = cfg.gates
    rows = []
    for hook in hooks:
        ev = events[events["hook"] == hook.id] if not events.empty else events
        for h in cfg.horizons:
            row: dict = {"hook": hook.id, "horizon": h, "primary": h == hook.horizon}
            row["n"] = int(ev[f"ret_{h}"].notna().sum()) if len(ev) else 0
            if row["n"] == 0:
                rows.append(row)
                continue
            valid = ev[ev[f"ret_{h}"].notna()]
            lift = cluster_bootstrap_mean(
                valid[f"excess_{h}"].to_numpy(), valid["week"].to_numpy(), cfg.n_boot
            )
            net = cluster_bootstrap_mean(
                valid[f"net_{h}"].to_numpy(), valid["week"].to_numpy(), cfg.n_boot, seed=1
            )
            sign = float(np.sign(lift["mean"]))
            row.update(
                mean_ret_r=valid[f"ret_{h}"].mean(),
                hit_rate=(valid[f"ret_{h}"] > 0).mean(),
                lift_r=lift["mean"],
                lift_lo=lift["lo"],
                lift_hi=lift["hi"],
                p=lift["p"],
                net_r=net["mean"],
                net_lo=net["lo"],
                net_hi=net["hi"],
                fee_r=valid["fee_r"].mean(),
                year_share=_sign_share(valid, "year", f"excess_{h}", sign, 1),
                pair_share=_sign_share(
                    valid, "pair", f"excess_{h}", sign, gates["min_pair_events"]
                ),
            )
            if h == hook.horizon:
                row.update(
                    tb_tp=(valid["tb_label"] == 1).mean(),
                    tb_sl=(valid["tb_label"] == -1).mean(),
                    tb_net_r=valid["tb_net_r"].mean(),
                    mfe_r=valid["mfe_r"].mean(),
                    mae_r=valid["mae_r"].mean(),
                )
            rows.append(row)
    table = pd.DataFrame(rows)
    if "p" not in table:
        return table
    reject, p_adj = benjamini_hochberg(table["p"].to_numpy(), cfg.fdr_q)
    table["p_bh"] = p_adj
    table["bh_reject"] = reject
    table["gate_n"] = table["n"] >= gates["min_events"]
    table["gate_lift"] = table["bh_reject"]
    table["gate_stable"] = (table["year_share"] >= gates["min_year_share"]) & (
        table["pair_share"] >= gates["min_pair_share"]
    )
    table["gate_net"] = table["net_r"] > 0
    table["pass"] = table[["gate_n", "gate_lift", "gate_stable", "gate_net"]].all(axis=1)
    return table


def by_regime(events: pd.DataFrame, hook: Hook, horizon: int, min_n: int = 30) -> pd.DataFrame:
    ev = events[(events["hook"] == hook.id) & events[f"ret_{horizon}"].notna()]
    rows = []
    for regime, g in ev.groupby("regime"):
        if len(g) < min_n:
            continue
        ci = cluster_bootstrap_mean(g[f"excess_{horizon}"].to_numpy(), g["week"].to_numpy(), 1000)
        rows.append({"regime": regime, "n": len(g), **ci})
    return pd.DataFrame(rows)
