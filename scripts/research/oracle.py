"""
Independent reference implementation (a test oracle) of the research engine's core computations,
written from their written specification to cross-check ``mce`` result by result. It never imports
``mce`` (a test enforces it): only pandas, numpy and TA-Lib (ATR, ADX), and it reads the parquet
store directly. Specification, in the engine's own terms:

- Store reads: a candle trades when its quote volume is > 0. Candles after the last one that
  traded (among those dated before ``end``) are dropped: Binance keeps serving flat zero-volume
  candles, and funding prints, for delisted contracts. Funding prints before the open of the first
  traded daily candle (an older contract on a reused ticker) or after the close of the last traded
  candle (4h candles when stored before ``end``, else daily) are dropped. Idle candles
  inside the series stay in the price path, but an idle day is not a close for the universe and
  an idle 4h bar is not in the universe.
- Point-in-time universe: a coin is eligible on day t with ``min_history`` consecutive daily
  closes up to t; liquidity = median quote volume of the last ``volume_window`` days (all
  present); the top ``n`` eligible coins by liquidity at the close of each month's last day are
  the members from that day until the next month end; a member without a close that day is out
  (not replaced). A 4h bar belongs to the universe when its coin is a member on the calendar day
  before its decision time (bar open + 4h).
- 4h features: ATR14 and ADX14 (Wilder, TA-Lib); shock = close change / previous ATR; EMA50 slope
  = EMA50 change over 10 bars / ATR; regime = trend (ADX >= 20 and slope >= 0.5: up, <= -0.5:
  down, else range) | volatility (percentile of ATR% among the last 30 days of bars: <= 0.2 low,
  >= 0.8 high, else normal). Open interest: hourly snapshots (archive first, API bridge after;
  values <= 0 are missing); the last one strictly before the bar's open and before its decision,
  each at most one bar old; change = log ratio; z-score over the last 30 days of bars.
- Events: bars with a signal, inside [start, end), with a regime and in the universe; after a
  kept event the next ``cooldown`` - 1 bars are skipped. Entry at the decision close; outcome after
  h bars in R = move / ATR at entry, never reading a bar at or after ``end``; lift = signed outcome
  minus the coin's mean outcome over its eligible bars of the same regime (times the side); fee =
  round trip x close / ATR; funding = prints in (decision, decision + h bars], signed, in R.
- Trend portfolio: two-sided Donchian per lookback on daily closes (channel of the previous n
  closes; break above -> long, below -> short; the stop starts at the channel midpoint and only
  moves in the trade's favour; a close beyond the stop exits and may reverse at the same close;
  a missing close resets); ensemble = mean state over lookbacks with full history; weight =
  ensemble x min(target / 90-day annualised vol, cap) / slots on members; weights decided at a
  close earn the next day's return; fees on turnover against the drifted weights; funding of the
  holding day (D+1 00:00, D+2 00:00].
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import talib


DAYS = 365
BAR_4H = pd.Timedelta(hours=4)
DAY = pd.Timedelta(days=1)


# ------------------------------------------------------------------ primitives


def shock(close: pd.Series, atr: pd.Series) -> pd.Series:
    """Close-to-close change in units of the previous bar's ATR."""
    return (close - close.shift(1)) / atr.shift(1)


def merge_oi(archive: pd.DataFrame | None, api: pd.DataFrame | None) -> pd.DataFrame:
    """Open interest snapshots (date, oi_value): the archive's, then the API's where the archive
    has no row at that time. Values <= 0 are dropped as missing."""
    parts = [p[["date", "oi_value"]] for p in (archive, api) if p is not None and len(p)]
    if not parts:
        return pd.DataFrame(columns=["date", "oi_value"])
    snaps = pd.concat(parts, ignore_index=True).drop_duplicates("date", keep="first")
    snaps = snaps[snaps["oi_value"] > 0]
    return snaps.sort_values("date").reset_index(drop=True)


def snapshot_before(times: pd.Series, snaps: pd.DataFrame, tolerance: pd.Timedelta) -> np.ndarray:
    """For each time: the value of the last snapshot taken strictly before it, NaN when there is
    none or it is older than ``tolerance``."""
    stamps = pd.DatetimeIndex(snaps["date"]).as_unit("ns").asi8  # UTC nanoseconds
    values = snaps["oi_value"].to_numpy(dtype=float)
    tol = pd.Timedelta(tolerance).as_unit("ns").value
    out = np.full(len(times), np.nan)
    for k, t in enumerate(pd.DatetimeIndex(times).as_unit("ns").asi8):
        i = stamps.searchsorted(t, side="left") - 1
        if i >= 0 and t - stamps[i] <= tol:
            out[k] = values[i]
    return out


def rolling_z(x: pd.Series, window: int) -> pd.Series:
    """(x - mean) / sample std over the last ``window`` values; NaN without a full window or
    without spread."""
    mean = x.rolling(window, min_periods=window).mean()
    sd = x.rolling(window, min_periods=window).std(ddof=1)
    return (x - mean) / sd.where(sd > 0)


def _consecutive(has: np.ndarray) -> np.ndarray:
    """Number of consecutive True values up to and including each row, per column."""
    run = np.zeros(has.shape, dtype=int)
    for j in range(has.shape[1]):
        k = 0
        for i in range(has.shape[0]):
            k = k + 1 if has[i, j] else 0
            run[i, j] = k
    return run


def pit_members(
    close: pd.DataFrame, volume: pd.DataFrame, n: int, volume_window: int, min_history: int
) -> pd.DataFrame:
    """Daily membership (module docstring). ``close`` on a continuous daily index."""
    has = close.notna()
    eligible = pd.DataFrame(_consecutive(has.to_numpy()) >= min_history, index=close.index)
    eligible.columns = close.columns
    liq = volume.reindex_like(close).rolling(volume_window, min_periods=volume_window).median()
    out = pd.DataFrame(False, index=close.index, columns=close.columns)
    ranking: set[str] = set()
    for i, day in enumerate(close.index):
        if (day + DAY).month != day.month:  # last calendar day of a month: re-rank
            scores = liq.iloc[i][eligible.iloc[i] & liq.iloc[i].notna()]
            order = sorted(scores.index, key=lambda c: (-scores[c], list(close.columns).index(c)))
            ranking = set(order[:n])
        for c in ranking:
            out.iat[i, close.columns.get_loc(c)] = bool(has.iat[i, close.columns.get_loc(c)])
    return out


def bar_active(
    members: pd.DataFrame, symbol: str, bar_open: pd.Series, bar: pd.Timedelta
) -> np.ndarray:
    """Membership of each bar: the coin's membership on the day before its decision day."""
    if symbol not in members:
        return np.zeros(len(bar_open), dtype=bool)
    day = (pd.DatetimeIndex(bar_open) + bar).floor("D") - DAY
    return members[symbol].reindex(day).fillna(False).to_numpy(dtype=bool)


def _pct_rank_last(window: np.ndarray) -> float:
    last = window[-1]
    below = np.sum(window < last)
    equal = np.sum(window == last)
    return (below + (equal + 1) / 2) / len(window)  # average rank of ties / count


def classify_regime(
    adx: pd.Series,
    slope: pd.Series,
    atr_pct: pd.Series,
    window: int,
    slope_min: float = 0.5,
    adx_min: float = 20.0,
    low: float = 0.2,
    high: float = 0.8,
) -> pd.Series:
    """'trend|vol' label per row (module docstring); NaN when an input is missing."""
    strong = adx >= adx_min
    trend = np.where(strong & (slope >= slope_min), "up", "range")
    trend = np.where(strong & (slope <= -slope_min), "down", trend)
    pct = atr_pct.rolling(window, min_periods=window).apply(_pct_rank_last, raw=True)
    vol = np.where(pct <= low, "low", np.where(pct >= high, "high", "normal"))
    out = pd.Series(trend, index=adx.index, dtype=object) + "|" + vol
    known = adx.notna() & slope.notna() & pct.notna()
    return out.where(known)


def events_with_cooldown(signal: np.ndarray, eligible: np.ndarray, cooldown: int) -> np.ndarray:
    """Positions of the kept events: a firing on an eligible bar at least ``cooldown`` bars after
    the previous kept event."""
    kept: list[int] = []
    for i in np.flatnonzero((np.asarray(signal) != 0) & np.asarray(eligible, dtype=bool)):
        if not kept or i - kept[-1] >= cooldown:
            kept.append(int(i))
    return np.asarray(kept, dtype=int)


def forward_r(
    close: np.ndarray, atr: np.ndarray, rows: np.ndarray, h: int, delay: int = 0
) -> np.ndarray:
    """(close[t + delay + h] - close[t + delay]) / atr[t]; NaN when a bar is beyond the data."""
    out = np.full(len(rows), np.nan)
    for k, t in enumerate(rows):
        if t + delay + h < len(close):
            out[k] = (close[t + delay + h] - close[t + delay]) / atr[t]
    return out


def regime_baseline(fwd: np.ndarray, regime: np.ndarray, eligible: np.ndarray) -> dict:
    """Mean outcome per regime over eligible bars (missing outcomes ignored)."""
    out = {}
    for r in pd.unique(regime[eligible]):
        vals = fwd[eligible & (regime == r)]
        vals = vals[np.isfinite(vals)]
        if len(vals):
            out[r] = float(vals.mean())
    return out


def funding_sum(prints: pd.Series, start: pd.Series, end: pd.Series) -> np.ndarray:
    """Sum of the funding prints stamped in (start, end] for each row."""
    p = prints.dropna().sort_index()
    out = np.zeros(len(start))
    for k, (a, b) in enumerate(zip(pd.DatetimeIndex(start), pd.DatetimeIndex(end), strict=True)):
        out[k] = p[(p.index > a) & (p.index <= b)].sum()
    return out


def week_bootstrap(
    values: np.ndarray, weeks: np.ndarray, n_boot: int = 2000, seed: int = 0, alpha: float = 0.05
) -> dict[str, float]:
    """Mean and a percentile CI from resampling whole weeks with replacement."""
    v = np.asarray(values, dtype=float)
    ok = np.isfinite(v)
    v, w = v[ok], np.asarray(weeks)[ok]
    labels = sorted(set(w))
    sums = np.array([v[w == lab].sum() for lab in labels])
    counts = np.array([(w == lab).sum() for lab in labels], dtype=float)
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(labels), size=(n_boot, len(labels)))
    boot = sums[draws].sum(axis=1) / counts[draws].sum(axis=1)
    lo, hi = np.quantile(boot, [alpha / 2, 1 - alpha / 2])
    return {"mean": float(v.mean()), "lo": float(lo), "hi": float(hi)}


# ------------------------------------------------------------------ trend portfolio


def donchian(close: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
    """(state in {-1, 0, 1}, stop) per close for one lookback (module docstring)."""
    c = np.asarray(close, dtype=float)
    state, stop = np.zeros(len(c), dtype=int), np.full(len(c), np.nan)
    pos, level = 0, np.nan
    for t in range(len(c)):
        prev = c[t - n : t] if t >= n else np.array([])
        if len(prev) < n or not np.isfinite(prev).all() or not np.isfinite(c[t]):
            pos, level = 0, np.nan
            continue
        hi, lo = prev.max(), prev.min()
        mid = (hi + lo) / 2
        if pos == 1:
            level = max(level, mid)
            if c[t] < level:
                pos, level = 0, np.nan
        elif pos == -1:
            level = min(level, mid)
            if c[t] > level:
                pos, level = 0, np.nan
        if pos == 0 and c[t] > hi:
            pos, level = 1, mid
        elif pos == 0 and c[t] < lo:
            pos, level = -1, mid
        state[t], stop[t] = pos, level
    return state, stop


def ensemble(close: pd.DataFrame, lookbacks: tuple[int, ...]) -> dict[str, pd.DataFrame]:
    """long / short share of the lookbacks with full history, signal = long - short, and the
    stop where the whole long (lowest long stop) or short (highest short stop) side exits."""
    parts: dict[str, dict] = {k: {} for k in ("long", "short", "long_stop", "short_stop")}
    for col in close.columns:
        c = close[col].to_numpy(dtype=float)
        states, stops, valid = [], [], []
        for n in lookbacks:
            st, lv = donchian(c, n)
            full = np.array([t >= n and np.isfinite(c[t - n : t + 1]).all() for t in range(len(c))])
            states.append(st)
            stops.append(lv)
            valid.append(full)
        st, lv, ok = np.array(states), np.array(stops), np.array(valid)
        count = np.maximum(ok.sum(axis=0), 1)
        parts["long"][col] = ((st == 1) & ok).sum(axis=0) / count
        parts["short"][col] = ((st == -1) & ok).sum(axis=0) / count
        long_lv = np.where(st == 1, lv, np.inf).min(axis=0)
        short_lv = np.where(st == -1, lv, -np.inf).max(axis=0)
        parts["long_stop"][col] = np.where(np.isfinite(long_lv), long_lv, np.nan)
        parts["short_stop"][col] = np.where(np.isfinite(short_lv), short_lv, np.nan)
    out = {k: pd.DataFrame(v, index=close.index) for k, v in parts.items()}
    out["signal"] = out["long"] - out["short"]
    return out


def vol_scalar(close: pd.DataFrame, target: float, window: int, cap: float) -> pd.DataFrame:
    """min(target / annualised sample vol of daily returns over ``window`` days, cap)."""
    vol = close.pct_change(fill_method=None).rolling(window, min_periods=window).std(ddof=1)
    return (target / (vol * np.sqrt(DAYS))).clip(upper=cap)


def target_weights(
    signal: pd.DataFrame, scalar: pd.DataFrame, members: pd.DataFrame, slots: int
) -> pd.DataFrame:
    """signal x scalar / slots on members, 0 elsewhere (and where the scalar is unknown)."""
    w = signal * scalar.reindex_like(signal) / slots
    return w.where(members.reindex_like(signal).fillna(False).astype(bool)).fillna(0.0)


def holding_day_funding(prints: pd.DataFrame, days: pd.DatetimeIndex) -> pd.DataFrame:
    """Funding summed over the holding day of each daily row D: prints in (D+1 00:00, D+2 00:00]."""
    out = pd.DataFrame(0.0, index=days, columns=prints.columns)
    for col in prints.columns:
        p = prints[col].dropna()
        for d in days:
            out.at[d, col] = p[(p.index > d + DAY) & (p.index <= d + 2 * DAY)].sum()
    return out


def backtest(
    weights: pd.DataFrame,
    close: pd.DataFrame,
    fee: float,
    funding: pd.DataFrame | None = None,
    lag: int = 0,
) -> pd.DataFrame:
    """Daily gross, fee, funding, net and turnover of a target-weight book (module docstring). A
    return needs both closes; otherwise it is 0."""
    w = weights.reindex_like(close).fillna(0.0).shift(lag).fillna(0.0).to_numpy()
    c = close.to_numpy(dtype=float)
    nxt = np.vstack([c[1:], np.full((1, c.shape[1]), np.nan)])
    ret = np.where(np.isfinite(c) & np.isfinite(nxt), nxt / c - 1, 0.0)
    f = 0.0 * w if funding is None else funding.reindex_like(close).fillna(0.0).to_numpy()
    rows, drifted = [], np.zeros(w.shape[1])
    for t in range(len(w)):
        turnover = np.abs(w[t] - drifted).sum()
        gross = float((w[t] * ret[t]).sum())
        paid = float((w[t] * f[t]).sum())
        rows.append((gross, fee * turnover, paid, turnover))
        grown = 1 + gross
        drifted = w[t] * (1 + ret[t]) / grown if grown > 0 else np.zeros_like(w[t])
    out = pd.DataFrame(rows, index=close.index, columns=["gross", "fee", "funding", "turnover"])
    out["net"] = out["gross"] - out["fee"] - out["funding"]
    return out


def sharpe(net: pd.Series) -> float:
    x = net.dropna()
    return float(x.mean() * DAYS / (x.std(ddof=1) * np.sqrt(DAYS)))


def total_return(net: pd.Series) -> float:
    return float((1 + net.dropna()).prod() - 1)


# ------------------------------------------------------------------ store readers and pipelines


def read(
    store: Path, kind: str, symbol: str, end: pd.Timestamp, raw: bool = False
) -> pd.DataFrame | None:
    """One stored file, rows dated strictly before ``end``, duplicates dropped, sorted; then
    (unless ``raw``) the store's fake rows removed: candles after the last one with a trade, and
    funding prints (any ``funding*`` kind) outside the coin's trading span (``trade_span``)."""
    path = store / kind / f"{symbol}.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df = df[df["date"] < end].drop_duplicates("date").sort_values("date").reset_index(drop=True)
    if raw:
        return df
    if kind.startswith("klines_"):
        trades = np.flatnonzero(df["quote_volume"].to_numpy(dtype=float) > 0)
        keep = trades[-1] + 1 if trades.size else 0
        return df.iloc[:keep].reset_index(drop=True)
    if kind.startswith("funding"):
        span = trade_span(store, symbol, end)
        if span is not None:  # NaT bounds (no trade at all) keep no print
            first, last = span
            return df[(df["date"] >= first) & (df["date"] <= last)].reset_index(drop=True)
    return df


def trade_span(
    store: Path, symbol: str, end: pd.Timestamp
) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    """(open of the coin's first candle with a trade, close of its last one) before ``end``: the
    first from the daily candles (the 4h ones may start later), the last from the 4h candles; each
    from the other interval when one has no candle before ``end``. (NaT, NaT) when none traded,
    None without any candle (nothing to judge funding by). Funding outside it belongs to an older
    contract on the ticker or follows the delisting."""
    found = {}
    for kind, bar in (("klines_1d", DAY), ("klines_4h", BAR_4H)):
        stored = read(store, kind, symbol, end, raw=True)
        if stored is not None and len(stored):
            traded = stored[stored["quote_volume"] > 0]
            if traded.empty:
                return pd.NaT, pd.NaT
            found[kind] = (traded["date"].iloc[0], traded["date"].iloc[-1] + bar)
    if not found:
        return None
    first = found["klines_1d"][0] if "klines_1d" in found else found["klines_4h"][0]
    last = found["klines_4h"][1] if "klines_4h" in found else found["klines_1d"][1]
    return first, last


def daily_panel(store: Path, symbols: list[str], field: str, end: pd.Timestamp) -> pd.DataFrame:
    cols = {}
    for s in symbols:
        df = read(store, "klines_1d", s, end)
        if df is not None and len(df):
            cols[s] = df.set_index("date")[field]
    return pd.DataFrame(cols).sort_index()


def universe(store: Path, end: pd.Timestamp, n: int) -> pd.DataFrame:
    """Daily top-``n`` membership of every candidate from the first stored day to end - 1 day.
    A day without a trade (a halt inside the series) is not a close."""
    names = json.loads((store / "symbols.json").read_text(encoding="utf-8"))["candidates"]
    close = daily_panel(store, names, "close", end)
    days = pd.date_range(close.index.min(), end - DAY, freq="D")
    close = close.reindex(days).dropna(axis=1, how="all")
    volume = daily_panel(store, list(close.columns), "quote_volume", end).reindex(days)
    volume = volume[close.columns]
    traded = volume > 0
    return pit_members(close.where(traded), volume.where(traded), n, 30, 90)


def frame_4h(
    store: Path, symbol: str, end: pd.Timestamp, members: pd.DataFrame, window: int = 180
) -> pd.DataFrame:
    """4h bars of a coin before ``end`` with ATR, shock, regime, open interest z and membership."""
    k = read(store, "klines_4h", symbol, end)
    if k is None or k.empty:
        return pd.DataFrame()
    h, lo, c = (k[x].to_numpy(dtype=float) for x in ("high", "low", "close"))
    f = k[["date", "open", "high", "low", "close"]].copy()
    f["atr"] = talib.ATR(h, lo, c, timeperiod=14)
    adx = pd.Series(talib.ADX(h, lo, c, timeperiod=14))
    ema = f["close"].ewm(span=50, adjust=False, min_periods=50).mean()
    slope = (ema - ema.shift(10)) / f["atr"]
    f["shock"] = shock(f["close"], f["atr"])
    f["ret1"] = np.log(f["close"] / f["close"].shift(1))
    f["regime"] = classify_regime(adx, slope, f["atr"] / f["close"], window)
    oi = merge_oi(read(store, "metrics", symbol, end), read(store, "metrics_api", symbol, end))
    now = snapshot_before(f["date"] + BAR_4H, oi, BAR_4H)
    prev = snapshot_before(f["date"], oi, BAR_4H)
    f["oi_z"] = rolling_z(pd.Series(np.log(now) - np.log(prev)), window)
    traded = k["quote_volume"].to_numpy(dtype=float) > 0  # an idle bar is never in the universe
    f["active"] = bar_active(members, symbol, f["date"], BAR_4H) & traded
    return f


def event_table(
    f: pd.DataFrame,
    signal: np.ndarray,
    start: pd.Timestamp,
    end: pd.Timestamp,
    horizons: tuple[int, ...],
    cooldown: int,
    round_trip_fee: float,
    prints: pd.Series | None,
) -> pd.DataFrame:
    """Kept events of one coin with outcomes, lift, fees and funding per horizon (in R)."""
    f = f[f["date"] < end].reset_index(drop=True)
    sig = np.asarray(signal)[: len(f)]
    eligible = (f["date"] >= start).to_numpy() & f["regime"].notna().to_numpy()
    eligible &= f["active"].to_numpy(dtype=bool)
    rows = events_with_cooldown(sig, eligible, cooldown)
    if rows.size == 0:
        return pd.DataFrame()
    close, atr = f["close"].to_numpy(dtype=float), f["atr"].to_numpy(dtype=float)
    regime = f["regime"].to_numpy(dtype=object)
    side = sig[rows]
    ev = f.iloc[rows][["date", "close", "atr", "shock", "oi_z", "regime"]].reset_index(drop=True)
    ev["side"] = side
    ev["fee_r"] = round_trip_fee * ev["close"] / ev["atr"]
    decision = ev["date"] + BAR_4H
    for hz in horizons:
        all_fwd = forward_r(close, atr, np.arange(len(f)), hz)
        base = regime_baseline(all_fwd, regime, eligible)
        signed = side * forward_r(close, atr, rows, hz)
        ev[f"ret_{hz}"] = signed
        ev[f"excess_{hz}"] = signed - side * np.array([base.get(r, np.nan) for r in regime[rows]])
        ev[f"net_{hz}"] = signed - ev["fee_r"]
        ev[f"late_net_{hz}"] = side * forward_r(close, atr, rows, hz, delay=1) - ev["fee_r"]
        paid = 0.0 if prints is None else funding_sum(prints, decision, decision + hz * BAR_4H)
        ev[f"funding_r_{hz}"] = side * paid * ev["close"] / ev["atr"]
        ev[f"net_all_{hz}"] = ev[f"net_{hz}"] - ev[f"funding_r_{hz}"]
    ev["week"] = ev["date"].dt.strftime("%G-%V")
    return ev
