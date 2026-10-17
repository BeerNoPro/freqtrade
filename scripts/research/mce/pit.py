"""
Point-in-time research frames built from the store of ``download_pit_universe.py``
(``<pit>/klines_<timeframe>``, ``metrics``, ``klines_1d`` and ``symbols.json``).

Row t = candle opened at ``date`` and decided at its close ``time = date + bar``. Every column is
causal: price features use candles <= t; positioning snapshots (open interest) are the latest
taken strictly before the decision time; ``active`` says whether the coin belongs to the top-N
point-in-time universe ranked on the last daily candle closed at the decision time and the bar
itself traded. Reads drop the fake rows served after a delisting (``mce.archive.read_store_file``):
candles after the last trade and funding after it. Idle candles inside a series (a halt) stay in
the price path but never count: not a close for the universe, never an active bar.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from mce.archive import load_wide, read_store_file, traded
from mce.features import add_core_features, rolling_zscore
from mce.regime import RegimeRules, add_regime
from mce.universe import point_in_time_members


BAR = {"1h": pd.Timedelta(hours=1), "4h": pd.Timedelta(hours=4), "1d": pd.Timedelta(days=1)}


def bars_per_30d(timeframe: str) -> int:
    return int(pd.Timedelta(days=30) / BAR[timeframe])


def read_store(pit: Path, kind: str, symbol: str, end: pd.Timestamp) -> pd.DataFrame | None:
    """One stored file cut strictly before ``end`` (no row at or after it is ever read), without
    the fake rows served after a delisting (``mce.archive.read_store_file``)."""
    return read_store_file(pit, kind, symbol, end)


def daily_members(
    pit: Path, end: pd.Timestamp, top_n: int = 20, volume_window: int = 30, min_history: int = 90
) -> pd.DataFrame:
    """Top-N point-in-time membership per calendar day from every candidate's daily candles. A
    day without any trade (a halt, candles before trading starts) is not a close."""
    meta = json.loads((pit / "symbols.json").read_text(encoding="utf-8"))
    close = load_wide(pit, "klines_1d", "close", meta["candidates"], end)
    days = pd.date_range(close.index.min(), end - pd.Timedelta(days=1), freq="D")
    close = close.reindex(days).dropna(axis=1, how="all")
    volume = load_wide(pit, "klines_1d", "quote_volume", list(close.columns), end)
    volume = volume.reindex(days)[close.columns]
    live = volume > 0
    return point_in_time_members(
        close.where(live), volume.where(live), top_n, volume_window, min_history
    )


def bar_membership(members: pd.DataFrame, symbol: str, decision: pd.Series) -> np.ndarray:
    """Membership of each bar, from the ranking of the last daily candle closed at the decision
    time (the candle of the previous calendar day)."""
    if symbol not in members:
        return np.zeros(len(decision), dtype=bool)
    day = decision.dt.floor("D") - pd.Timedelta(days=1)
    return members[symbol].reindex(day).fillna(False).to_numpy(dtype=bool)


def snapshot_before(
    times: pd.Series, snaps: pd.DataFrame, column: str, tolerance: pd.Timedelta
) -> np.ndarray:
    """``column`` of the latest snapshot taken strictly before each time, NaN when the latest
    is older than ``tolerance``. A snapshot stamped exactly at the time is not used."""
    left = pd.DataFrame({"t": times.to_numpy()})
    right = snaps[["date", column]].rename(columns={"date": "t"}).dropna()
    right["t"] = right["t"].astype(left["t"].dtype)
    merged = pd.merge_asof(
        left,
        right.sort_values("t"),
        on="t",
        direction="backward",
        tolerance=tolerance,
        allow_exact_matches=False,
    )
    return merged[column].to_numpy(dtype=float)


def open_interest(pit: Path, symbol: str, end: pd.Timestamp) -> pd.DataFrame | None:
    """Hourly open interest snapshots (``date``, ``oi_value``) before ``end``: the archive's
    ``metrics``, completed by ``metrics_api`` (the same series from the live API, see
    ``mce.live``) where the archive has no row yet. The archive wins on a shared timestamp."""
    parts = [read_store(pit, kind, symbol, end) for kind in ("metrics", "metrics_api")]
    parts = [p[["date", "oi_value"]] for p in parts if p is not None and len(p)]
    if not parts:
        return None
    m = pd.concat(parts, ignore_index=True).drop_duplicates("date", keep="first")
    return m.sort_values("date").reset_index(drop=True)


def coin_frame(
    pit: Path, symbol: str, timeframe: str, end: pd.Timestamp, members: pd.DataFrame
) -> pd.DataFrame:
    """Causal frame of one coin: price features, regime, open interest change and its z-score
    over 30 days, universe membership of bars that traded. Empty when the coin has no candles of
    ``timeframe`` (or none with a trade)."""
    k = read_store(pit, f"klines_{timeframe}", symbol, end)
    if k is None or k.empty:
        return pd.DataFrame()
    bar, window = BAR[timeframe], bars_per_30d(timeframe)
    f = add_core_features(k[["date", "open", "high", "low", "close", "volume"]])
    f = add_regime(f, RegimeRules(vol_window=window))
    f["time"] = f["date"] + bar
    m = open_interest(pit, symbol, end)
    if m is not None and len(m):
        m = m[m["oi_value"] > 0]  # the archive has a few zero prints: missing, not a real level
        f["oi_now"] = snapshot_before(f["time"], m, "oi_value", bar)
        f["oi_prev"] = snapshot_before(f["date"], m, "oi_value", bar)
    else:
        f["oi_now"] = f["oi_prev"] = np.nan
    f["oi_chg"] = np.log(f["oi_now"]) - np.log(f["oi_prev"])
    f["oi_z"] = rolling_zscore(f["oi_chg"], window)
    f["active"] = bar_membership(members, symbol, f["time"]) & traded(k).to_numpy()
    f["pair"] = symbol
    return f


def load_frames(
    pit: Path, timeframe: str, start: pd.Timestamp, end: pd.Timestamp, top_n: int = 20
) -> dict[str, pd.DataFrame]:
    """Frames of every coin that is in the top-N universe on at least one bar in [start, end)."""
    members = daily_members(pit, end, top_n)
    frames = {}
    for symbol in members.columns[members.loc[start - pd.Timedelta(days=1) :].any()]:
        f = coin_frame(pit, symbol, timeframe, end, members)
        if len(f) and (f["active"] & (f["date"] >= start)).any():
            frames[symbol] = f
    return frames


def synthetic_store(
    pit: Path, n_coins: int = 24, start: str = "2024-01-01", end: str = "2026-04-01", seed: int = 0
) -> None:
    """Write a fake store (daily and 4h candles, hourly open interest snapshots at minute 50,
    funding every 8h) for dry runs and tests. Prices follow a market factor plus noise with
    occasional crashes in which open interest drops, and no edge after them."""
    rng = np.random.default_rng(seed)
    idx4 = pd.date_range(start, end, freq="4h", tz="UTC", inclusive="left")
    mkt = rng.normal(0, 0.012, len(idx4))
    crash = rng.uniform(size=(len(idx4), n_coins)) < 0.004
    hours = pd.date_range(start, end, freq="1h", tz="UTC", inclusive="left")
    bar_of_hour = (hours - hours[0]) // pd.Timedelta(hours=4)
    hours = hours + pd.Timedelta(minutes=50)
    symbols = [f"C{i:02d}USDT" for i in range(n_coins)]
    for kind in ("klines_1d", "klines_4h", "metrics", "funding"):
        (pit / kind).mkdir(parents=True, exist_ok=True)
    for j, symbol in enumerate(symbols):
        ret = mkt * rng.uniform(0.5, 2.0) + rng.normal(0, 0.015, len(idx4)) - 0.06 * crash[:, j]
        close = 10 * np.exp(np.cumsum(ret))
        spread = np.abs(rng.normal(0, 0.006, len(idx4))) * close
        k = pd.DataFrame({"date": idx4, "open": np.r_[close[0], close[:-1]], "close": close})
        k["high"] = k[["open", "close"]].max(axis=1) + spread
        k["low"] = k[["open", "close"]].min(axis=1) - spread
        k["volume"] = rng.uniform(1e5, 1e6, len(idx4)) * (1 + 3 * crash[:, j])
        k["quote_volume"] = k["volume"] * k["close"] * rng.uniform(0.5, 2.0)
        k["taker_buy_quote"] = k["quote_volume"] * rng.uniform(0.4, 0.6, len(idx4))
        k.to_parquet(pit / "klines_4h" / f"{symbol}.parquet", index=False)
        day = k.set_index("date").resample("1D")
        d = pd.DataFrame(
            {
                "open": day["open"].first(),
                "high": day["high"].max(),
                "low": day["low"].min(),
                "close": day["close"].last(),
                "volume": day["volume"].sum(),
                "quote_volume": day["quote_volume"].sum(),
            }
        ).reset_index()
        d.to_parquet(pit / "klines_1d" / f"{symbol}.parquet", index=False)
        # hourly snapshots at minute 50; open interest falls through the hours of a crash bar
        hour_ret = rng.normal(0, 0.004, len(hours)) - 0.02 * crash[bar_of_hour, j]
        pd.DataFrame({"date": hours, "oi_value": 1e7 * np.exp(np.cumsum(hour_ret))}).to_parquet(
            pit / "metrics" / f"{symbol}.parquet", index=False
        )
        prints = pd.date_range(start, end, freq="8h", tz="UTC", inclusive="neither")
        pd.DataFrame(
            {"date": prints, "funding_rate": rng.normal(0.0001, 0.0002, len(prints))}
        ).to_parquet(pit / "funding" / f"{symbol}.parquet", index=False)
    meta = {"listed_at": "synthetic", "candidates": symbols}
    (pit / "symbols.json").write_text(json.dumps(meta), encoding="utf-8")
