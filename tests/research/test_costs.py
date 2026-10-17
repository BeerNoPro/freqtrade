"""Trading cost model: fees, spread rebuilt from trades, book depth walk (hand-computed answers)."""

import numpy as np
import pandas as pd
import pytest
from mce.costs import (
    FeeSchedule,
    TradeCost,
    compare,
    depth_bands,
    fill_distance_bps,
    side_bands,
    taker_orders,
    trade_cost,
    trade_spread_bps,
)


T0 = pd.Timestamp("2026-10-12", tz="UTC")


def _trades(rows: list[tuple[float, float, float, bool]]) -> pd.DataFrame:
    """(seconds after T0, price, quantity, is_buyer_maker) -> parsed aggTrades rows."""
    return pd.DataFrame(
        {
            "date": [T0 + pd.Timedelta(seconds=s) for s, *_ in rows],
            "agg_trade_id": np.arange(len(rows)),
            "price": [p for _, p, _, _ in rows],
            "quantity": [q for _, _, q, _ in rows],
            "is_buyer_maker": [m for *_, m in rows],
        }
    )


def test_fees_and_cost_in_r() -> None:
    assert FeeSchedule().rate() == pytest.approx(0.0005)
    assert FeeSchedule().rate(taker=False) == pytest.approx(0.0002)
    assert FeeSchedule(bnb=True).rate() == pytest.approx(0.00045)
    cost = TradeCost(fee_bp=10.0, spread_bp=2.0, impact_bp=0.0)
    assert cost.total_bp == pytest.approx(12.0)
    assert cost.in_r(entry=100.0, stop=98.0) == pytest.approx(0.06)  # 0.12% x 100 / 2


def test_taker_orders_merge_one_sweep() -> None:
    t = _trades(
        [
            (0.0, 100.02, 1.0, False),  # a buy sweeping three ask levels in one ms
            (0.0, 100.03, 2.0, False),
            (0.0, 100.05, 1.0, False),
            (0.0, 99.98, 3.0, True),  # a sell in the same ms is another order
            (1.0, 100.02, 1.0, False),
        ]
    )
    o = taker_orders(t)
    assert list(o["side"]) == [1, -1, 1]
    first = o.iloc[0]
    assert first["touch"] == 100.02
    assert first["notional"] == pytest.approx(100.02 + 2 * 100.03 + 100.05)
    assert first["vwap"] == pytest.approx((100.02 + 2 * 100.03 + 100.05) / 4)
    assert o.iloc[1]["touch"] == 99.98 and o.iloc[1]["notional"] == pytest.approx(3 * 99.98)
    empty = taker_orders(t.iloc[:0])
    assert empty.empty and "touch" in empty.columns
    assert trade_spread_bps(empty, pd.DatetimeIndex([T0])).isna().all()


def test_trade_spread_rebuilds_a_constant_quote() -> None:
    # buys fill at the ask 100.02, sells at the bid 99.98, alternating every 0.5 s
    rows = [(0.5 * i, 100.02 if i % 2 == 0 else 99.98, 1.0, i % 2 == 1) for i in range(240)]
    at = pd.DatetimeIndex([T0 + pd.Timedelta(seconds=60)])
    s = trade_spread_bps(taker_orders(_trades(rows)), at)
    assert s.iloc[0] == pytest.approx(4.0, abs=1e-9)  # 0.04 / 100


def test_trade_spread_median_resists_drift_and_needs_close_pairs() -> None:
    # the whole quote drifts up 1 bp per second; the spread stays 4 bp
    rows = []
    for i in range(240):
        mid = 100.0 * (1 + 1e-4 * 0.5 * i)
        rows.append((0.5 * i, mid * (1.0002 if i % 2 == 0 else 0.9998), 1.0, i % 2 == 1))
    at = pd.DatetimeIndex([T0 + pd.Timedelta(seconds=60)])
    s = trade_spread_bps(taker_orders(_trades(rows)), at)
    assert s.iloc[0] == pytest.approx(4.0, abs=0.6)
    # one buy and one sell 10 s apart: no pair within max_gap (2 s)
    far = _trades([(0.0, 100.02, 1.0, False), (10.0, 99.98, 1.0, True)])
    assert np.isnan(trade_spread_bps(taker_orders(far), pd.DatetimeIndex([T0])).iloc[0])


def _book(seconds: float, ask: tuple, bid: tuple) -> pd.DataFrame:
    """One bookDepth snapshot: cumulative notional at 0.2 / 1 / 5 % on each side."""
    pct = [-5.0, -1.0, -0.2, 0.2, 1.0, 5.0]
    notional = [bid[2], bid[1], bid[0], ask[0], ask[1], ask[2]]
    return pd.DataFrame(
        {
            "date": T0 + pd.Timedelta(seconds=seconds),
            "percentage": pct,
            "depth": np.nan,
            "notional": notional,
        }
    )


def test_depth_bands_pick_the_last_snapshot_not_too_old() -> None:
    book = pd.concat(
        [_book(0, (1, 2, 3), (4, 5, 6)), _book(30, (10, 20, 30), (40, 50, 60))], ignore_index=True
    )
    at = pd.DatetimeIndex([T0 + pd.Timedelta(seconds=s) for s in (29, 31, 100)])
    wide = depth_bands(book, at)
    assert wide.loc[at[0], 0.2] == 1 and wide.loc[at[1], 0.2] == 10  # at or before
    assert wide.loc[at[2]].isna().all()  # 70 s old: too old
    edges, cum = side_bands(wide.loc[at[1]], side=-1)  # a sell takes the bids
    assert list(edges) == [20.0, 100.0, 500.0] and list(cum) == [40, 50, 60]


def test_fill_distance_walks_the_bands() -> None:
    edges, cum = [20.0, 100.0, 500.0], [10_000.0, 50_000.0, 90_000.0]
    # half spread 1 bp; the first 10k spread evenly over 1-20 bp
    assert fill_distance_bps(5_000, edges, cum, 1.0) == pytest.approx(5.75)  # last fill 10.5
    # 10k at a mean of 10.5 bp, then 20k over 20-60 bp at a mean of 40 bp
    assert fill_distance_bps(30_000, edges, cum, 1.0) == pytest.approx((105 + 800) / 30)
    assert fill_distance_bps(0, edges, cum, 1.0) == pytest.approx(1.0)
    assert np.isnan(fill_distance_bps(100_000, edges, cum, 1.0))  # beyond 5 %
    # a quote wider than the first band: its notional sits at the touch
    assert fill_distance_bps(5_000, edges, cum, 30.0) == pytest.approx(30.0)


def test_trade_cost_sums_parts_and_signs_funding() -> None:
    long = trade_cost(1, 1.0, 3.0, 2.0, 2.5, funding=0.0001)
    assert long.fee_bp == pytest.approx(10.0)  # 5 bp taker each way
    assert long.spread_bp == pytest.approx(3.0) and long.impact_bp == pytest.approx(2.5)
    assert long.funding_bp == pytest.approx(1.0)  # a long pays a positive rate
    assert long.total_bp == pytest.approx(16.5)
    short = trade_cost(-1, 1.0, 3.0, 2.0, 2.5, funding=0.0001)
    assert short.funding_bp == pytest.approx(-1.0) and short.total_bp == pytest.approx(14.5)
    thin = trade_cost(1, 1.0, float("nan"), 2.0, 2.5)
    assert np.isnan(thin.total_bp)


def test_compare_reports_ratio_and_rank_agreement() -> None:
    truth = pd.Series([1.0, 2.0, 4.0, 8.0])
    out = compare(truth * 1.5, truth)
    assert out["n"] == 4 and out["median_ratio"] == pytest.approx(1.5)
    assert out["median_abs_log_ratio"] == pytest.approx(np.log(1.5))
    assert out["spearman"] == pytest.approx(1.0)
    # a non-positive estimate is an infinitely bad ratio, a missing pair is left out
    out = compare(pd.Series([-1.0, 2.0, np.nan]), pd.Series([1.0, 2.0, 3.0]))
    assert out["n"] == 2 and np.isinf(out["median_abs_log_ratio"])
