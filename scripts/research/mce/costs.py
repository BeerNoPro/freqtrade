"""
Trading costs of a Binance USDT-M perpetual for an order of a given size at a given time: exchange
fee, half the bid-ask spread, the impact of walking the book, and funding over the holding period.

- Fee: ``FeeSchedule`` (VIP0 by default: maker 0.02%, taker 0.05%; BNB payment takes 10% off).
  Market orders pay the taker rate on entry and exit.
- Spread: rebuilt from the archive trades (``aggTrades``). The first fill of a taker buy is at the
  best ask of that moment and the first fill of a taker sell at the best bid, so a buy and a sell
  close together in time bracket the quote (``trade_spread_bps``).
- Book impact: the archive book depth (``bookDepth``, every 30 s) gives the notional resting
  within ±0.2/1/2/3/4/5% of the mid. ``fill_distance_bps`` walks it from the touch, liquidity
  spread evenly within each band.
- Funding: the sum of the prints over the holding period (``mce.labels.funding_between``),
  paid by a long when positive.

All distances are in basis points of the mid price. The recorder's best bid/ask and full books
(``mce.recorder``) are the ground truth the estimates are checked against
(``user_data/research/cost_check_v1.py``).
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd


BPS = 1e4
SPREAD_WINDOW = pd.Timedelta(seconds=60)  # trade pairs pooled around each time
PAIR_GAP = pd.Timedelta(seconds=2)  # longest time between a buy and a sell bracketing the quote
BOOK_MAX_AGE = pd.Timedelta(seconds=30)  # bookDepth snapshots come every 30 s


@dataclass(frozen=True)
class FeeSchedule:
    maker: float = 0.0002
    taker: float = 0.0005
    bnb: bool = False  # fees paid in BNB: 10% off

    def rate(self, taker: bool = True) -> float:
        r = self.taker if taker else self.maker
        return r * 0.9 if self.bnb else r


VIP0 = FeeSchedule()


@dataclass(frozen=True)
class TradeCost:
    """Round-trip cost in bp of the entry price; ``funding_bp`` < 0 is funding received."""

    fee_bp: float
    spread_bp: float
    impact_bp: float
    funding_bp: float = 0.0

    @property
    def total_bp(self) -> float:
        return self.fee_bp + self.spread_bp + self.impact_bp + self.funding_bp

    def in_r(self, entry: float, stop: float) -> float:
        """Cost in R: a fraction of the entry price over the stop distance."""
        return self.total_bp / BPS * entry / abs(entry - stop)


def trade_cost(
    side: int,
    entry_half_spread_bps: float,
    entry_fill_bps: float,
    exit_half_spread_bps: float,
    exit_fill_bps: float,
    funding: float = 0.0,
    fees: FeeSchedule = VIP0,
) -> TradeCost:
    """Market order in and out. ``*_fill_bps``: mean fill distance from the mid
    (``fill_distance_bps``, half spread included); ``funding``: sum of the funding prints held."""
    return TradeCost(
        fee_bp=2 * fees.rate(taker=True) * BPS,
        spread_bp=entry_half_spread_bps + exit_half_spread_bps,
        impact_bp=(entry_fill_bps - entry_half_spread_bps) + (exit_fill_bps - exit_half_spread_bps),
        funding_bp=side * funding * BPS,
    )


def taker_orders(trades: pd.DataFrame) -> pd.DataFrame:
    """Parsed aggTrades rows -> one row per taker order: consecutive rows in the same millisecond
    on the same side are one order sweeping several prices. ``touch`` is its first fill (the best
    price on the other side when it arrived), ``side`` +1 for a buy, -1 for a sell."""
    if trades.empty:
        typed = {"date": "datetime64[ns, UTC]", "side": "int64"}
        cols = ("date", "side", "touch", "vwap", "notional")
        return pd.DataFrame({c: pd.Series(dtype=typed.get(c, "float64")) for c in cols})
    t = trades.sort_values("agg_trade_id", kind="stable")
    side = np.where(t["is_buyer_maker"].to_numpy(), -1, 1)
    date = t["date"].to_numpy()
    new = np.r_[True, (date[1:] != date[:-1]) | (side[1:] != side[:-1])]
    order = np.cumsum(new)
    price, qty = t["price"].to_numpy(float), t["quantity"].to_numpy(float)
    frame = pd.DataFrame({"order": order, "notional": price * qty, "qty": qty})
    sums = frame.groupby("order", sort=True)[["notional", "qty"]].sum()
    first = np.flatnonzero(new)
    return pd.DataFrame(
        {
            "date": t["date"].to_numpy()[first],
            "side": side[first],
            "touch": price[first],
            "vwap": (sums["notional"] / sums["qty"]).to_numpy(),
            "notional": sums["notional"].to_numpy(),
        }
    )


def trade_spread_bps(
    orders: pd.DataFrame,
    at: pd.DatetimeIndex,
    window: pd.Timedelta = SPREAD_WINDOW,
    max_gap: pd.Timedelta = PAIR_GAP,
) -> pd.Series:
    """Quoted spread rebuilt from taker orders, in bp of the mid: every order is paired with the
    last order on the other side at most ``max_gap`` earlier (their touches are the ask and the
    bid); the value at each time of ``at`` is the median pair within ``window`` centred on it.
    NaN when no pair falls in the window."""
    o = orders.sort_values("date", kind="stable").reset_index(drop=True)
    pairs = []
    for side in (1, -1):
        mine = o[o["side"] == side][["date", "touch"]]
        other = o[o["side"] == -side][["date", "touch"]].rename(columns={"touch": "other"})
        m = pd.merge_asof(mine, other, on="date", tolerance=max_gap, allow_exact_matches=True)
        m = m.dropna(subset=["other"])
        ask = m["touch"] if side == 1 else m["other"]
        bid = m["other"] if side == 1 else m["touch"]
        pairs.append(
            pd.DataFrame({"date": m["date"], "bps": (ask - bid) / ((ask + bid) / 2) * BPS})
        )
    p = pd.concat(pairs, ignore_index=True).sort_values("date", kind="stable")
    times, values = pd.DatetimeIndex(p["date"]), p["bps"].to_numpy(float)
    lo = times.searchsorted(at - window / 2, side="left")
    hi = times.searchsorted(at + window / 2, side="right")
    out = [np.median(values[a:b]) if b > a else np.nan for a, b in zip(lo, hi, strict=True)]
    return pd.Series(out, index=at, dtype=float)


def depth_bands(
    book_depth: pd.DataFrame, at: pd.DatetimeIndex, max_age: pd.Timedelta = BOOK_MAX_AGE
) -> pd.DataFrame:
    """For each time of ``at``, the last bookDepth snapshot at or before it (no older than
    ``max_age``): one column per percentage (negative: bids), cumulative notional. NaN rows when
    there is no such snapshot."""
    wide = book_depth.pivot_table(
        index="date", columns="percentage", values="notional", aggfunc="last"
    ).sort_index()
    return wide.reindex(at, method="ffill", tolerance=max_age)


def side_bands(row: pd.Series, side: int) -> tuple[np.ndarray, np.ndarray]:
    """Band edges (bp from the mid, nearest first) and cumulative notional on the side a market
    order of ``side`` takes: the asks for a buy, the bids for a sell."""
    pct = row.index.to_numpy(float)
    keep = pct > 0 if side > 0 else pct < 0
    edges = np.round(np.abs(pct[keep]) * 100, 6)
    order = np.argsort(edges)
    return edges[order], row.to_numpy(float)[keep][order]


def fill_distance_bps(notional: float, edges_bps, cum_notional, half_spread_bps: float) -> float:
    """Mean distance from the mid (bp, notional-weighted) of the fills of a market order worth
    ``notional``. Fills start at the touch (``half_spread_bps``); within each band the resting
    notional is spread evenly, so the band from the touch to 0.2% holds the 0.2% notional.
    Notional the book places inside the touch sits at the touch. NaN beyond the last band."""
    h = float(half_spread_bps)
    e = np.asarray(edges_bps, float)
    n = np.maximum.accumulate(np.asarray(cum_notional, float))
    if not np.isfinite(h) or not np.isfinite(n).all() or notional > n[-1]:
        return float("nan")
    inside = e <= h
    x = np.r_[h, e[~inside]]
    q = np.r_[n[inside].max() if inside.any() else 0.0, n[~inside]]
    if notional <= q[0]:
        return h
    area = q[0] * h
    for k in range(len(x) - 1):
        lo, hi = q[k], q[k + 1]
        if notional <= lo:
            break
        take = min(notional, hi) - lo
        if hi > lo and take > 0:
            end = x[k] + (x[k + 1] - x[k]) * take / (hi - lo)
            area += take * (x[k] + end) / 2
    return float(area / notional)


def compare(est: pd.Series, truth: pd.Series) -> dict[str, float]:
    """Agreement of an estimate with the ground truth on the pairs where both exist: median
    ratio est / truth, median |log ratio| (infinite for a non-positive estimate) and rank
    correlation."""
    both = pd.DataFrame({"est": est, "truth": truth}).dropna()
    both = both[both["truth"] > 0]
    ratio = (both["est"] / both["truth"]).to_numpy(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        log_ratio = np.where(ratio > 0, np.abs(np.log(np.where(ratio > 0, ratio, 1.0))), np.inf)
    rank = both.rank()
    return {
        "n": len(both),
        "median_ratio": float(np.median(ratio)) if len(ratio) else float("nan"),
        "median_abs_log_ratio": float(np.median(log_ratio)) if len(ratio) else float("nan"),
        "spearman": float(rank["est"].corr(rank["truth"])) if len(both) > 1 else float("nan"),
    }
