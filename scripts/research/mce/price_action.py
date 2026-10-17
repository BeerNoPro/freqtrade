"""
Price action structure on closed candles: fractal swing points, breaks of structure, the first
retest of the broken level (flip zone) and a confirmation candle at it. Every detection function
is causal: a value at bar t only uses candles up to t. ``score_stops`` is an outcome label (it
reads later candles, like ``mce.labels``) and is the only function here that does.

- Swing high at bar j: high[j] above the k bars before it and not below the k bars after it
  (Williams fractal); it is known at bar j + k. Swing lows mirror it.
- Structure: a close above the last known swing high starts (or continues, when that swing is
  newer than the last broken one) an up state; a close below the last known swing low starts a
  down state. The broken swing is the flip ``level``; the ``stop`` of a trade in the state is
  the last known opposite swing (where the structure would break).
- Retest (long): after the break, price first leaves the zone (low above level + zone ATRs),
  then the first bar whose low comes back into it. One retest per break.
- Confirmation (long): within ``window`` bars from the retest, the first bullish candle closing
  above the level that is a rejection (lower wick >= half the range) or a bullish engulfing
  (previous candle bearish, open <= previous close, close > previous open).
"""

import numpy as np
import pandas as pd

from mce.journal import barrier_exit


REJECTION_WICK = 0.5  # share of the candle range


def swings(high: np.ndarray, low: np.ndarray, k: int = 2) -> pd.DataFrame:
    """Last known swing high / low (price and bar) at each bar."""
    high, low = np.asarray(high, float), np.asarray(low, float)
    n = len(high)
    out = np.full((n, 4), np.nan)  # high_price, high_bar, low_price, low_bar
    hp = hb = lp = lb = np.nan
    for t in range(n):
        j = t - k  # the bar whose swing becomes known at t
        if j >= k:
            if high[j] > high[j - k : j].max() and high[j] >= high[j + 1 : t + 1].max():
                hp, hb = high[j], j
            if low[j] < low[j - k : j].min() and low[j] <= low[j + 1 : t + 1].min():
                lp, lb = low[j], j
        out[t] = (hp, hb, lp, lb)
    return pd.DataFrame(out, columns=["high_price", "high_bar", "low_price", "low_bar"])


def structure(df: pd.DataFrame, k: int = 2) -> pd.DataFrame:
    """Per bar: ``state`` (+1 up, -1 down, 0 none yet), flip ``level`` and ``stop`` of the
    state, ``bos`` (a break of structure on this bar) and ``break_id`` (which break is in
    force)."""
    s = swings(df["high"].to_numpy(), df["low"].to_numpy(), k)
    close = df["close"].to_numpy(float)
    hp, hb = s["high_price"].to_numpy(), s["high_bar"].to_numpy()
    lp, lb = s["low_price"].to_numpy(), s["low_bar"].to_numpy()
    n = len(df)
    state = np.zeros(n, int)
    level, stop = np.full(n, np.nan), np.full(n, np.nan)
    bos, break_id = np.zeros(n, bool), np.zeros(n, int)
    cur, lvl, lvl_bar, bid = 0, np.nan, np.nan, 0
    for t in range(n):
        if np.isfinite(hp[t]) and close[t] > hp[t] and (cur != 1 or hb[t] != lvl_bar):
            cur, lvl, lvl_bar, bid = 1, hp[t], hb[t], bid + 1
            bos[t] = True
        elif np.isfinite(lp[t]) and close[t] < lp[t] and (cur != -1 or lb[t] != lvl_bar):
            cur, lvl, lvl_bar, bid = -1, lp[t], lb[t], bid + 1
            bos[t] = True
        state[t], level[t], break_id[t] = cur, lvl, bid
        stop[t] = lp[t] if cur == 1 else (hp[t] if cur == -1 else np.nan)
    return pd.DataFrame(
        {"state": state, "level": level, "stop": stop, "bos": bos, "break_id": break_id}
    )


def _events(rows: list[int], st: pd.DataFrame, extra: dict | None = None) -> pd.DataFrame:
    idx = np.asarray(rows, dtype=int)
    out = pd.DataFrame(
        {
            "row": idx,
            "side": st["state"].to_numpy()[idx].astype(int),
            "level": st["level"].to_numpy()[idx].astype(float),
            "stop": st["stop"].to_numpy()[idx].astype(float),
            "break_id": st["break_id"].to_numpy()[idx].astype(int),
        }
    )
    for name, values in (extra or {}).items():
        out[name] = np.asarray(values, dtype=int)
    return out


def zone_touches(df: pd.DataFrame, st: pd.DataFrame, zone_atr: float = 0.5) -> pd.DataFrame:
    """First retest of the flip level after each break, once price has left the zone."""
    high, low = df["high"].to_numpy(float), df["low"].to_numpy(float)
    atr = df["atr"].to_numpy(float)
    state, level, bos = st["state"].to_numpy(), st["level"].to_numpy(), st["bos"].to_numpy()
    rows: list[int] = []
    left, done = False, True
    for t in range(len(df)):
        if bos[t]:
            left, done = False, False
        if done or state[t] == 0 or not np.isfinite(atr[t]):
            continue
        edge = level[t] + state[t] * zone_atr * atr[t]
        inside = low[t] <= edge if state[t] > 0 else high[t] >= edge
        if left and inside and not bos[t]:
            rows.append(t)
            done = True
        elif not inside:
            left = True
    return _events(rows, st)


def confirmations(
    df: pd.DataFrame, st: pd.DataFrame, touches: pd.DataFrame, window: int = 3
) -> pd.DataFrame:
    """First confirmation candle within ``window`` bars from each retest (the retest bar
    included), while the same break is in force."""
    o, h = df["open"].to_numpy(float), df["high"].to_numpy(float)
    lo, c = df["low"].to_numpy(float), df["close"].to_numpy(float)
    bid = st["break_id"].to_numpy()
    rows, touch_rows = [], []
    for r, side, lvl, b in touches[["row", "side", "level", "break_id"]].itertuples(index=False):
        for t in range(r, min(r + window, len(df))):
            if bid[t] != b:
                break
            rng = h[t] - lo[t]
            if side > 0:
                ok = c[t] > lvl and c[t] > o[t]
                wick = (min(o[t], c[t]) - lo[t]) / rng if rng > 0 else 0.0
                engulf = t > 0 and c[t - 1] < o[t - 1] and o[t] <= c[t - 1] and c[t] > o[t - 1]
            else:
                ok = c[t] < lvl and c[t] < o[t]
                wick = (h[t] - max(o[t], c[t])) / rng if rng > 0 else 0.0
                engulf = t > 0 and c[t - 1] > o[t - 1] and o[t] >= c[t - 1] and c[t] < o[t - 1]
            if ok and (wick >= REJECTION_WICK or engulf):
                rows.append(t)
                touch_rows.append(r)
                break
    return _events(rows, st, {"touch_row": touch_rows})


def score_stops(
    df: pd.DataFrame,
    rows: np.ndarray,
    sides: np.ndarray,
    stops: np.ndarray,
    tp_r: float = 2.0,
    max_bars: int = 30,
    min_stop_atr: float = 0.5,
    delay: int = 0,
) -> pd.DataFrame:
    """Outcome of entering at the close of ``row + delay`` with a stop at the given price (at
    least ``min_stop_atr`` ATRs away), a target ``tp_r`` risks away and a time exit after
    ``max_bars`` candles (``mce.journal.barrier_exit``: a candle touching both is a stop).
    ``gross_r`` is NaN when fewer than ``max_bars`` candles follow, or when a late entry is
    already through the stop."""
    close, atr = df["close"].to_numpy(float), df["atr"].to_numpy(float)
    n = len(df)
    cols = ["entry", "risk", "stop_price", "target", "gross_r", "bars", "exit_row"]
    out = np.full((len(rows), len(cols)), np.nan)
    reasons: list[str | None] = [None] * len(rows)
    for e, (r, side, stop) in enumerate(zip(rows, sides, stops, strict=True)):
        i = int(r) + delay
        if side == 0 or i + max_bars >= n or not (np.isfinite(stop) and np.isfinite(atr[i])):
            continue
        entry = close[i]
        dist = side * (entry - stop)
        if dist <= 0:
            continue  # entered beyond the stop
        risk = max(dist, min_stop_atr * atr[i])
        stop_price, target = entry - side * risk, entry + side * tp_r * risk
        reason, j, price = barrier_exit(
            int(side), stop_price, target, df.iloc[i + 1 : i + 1 + max_bars]
        )
        out[e] = (entry, risk, stop_price, target, side * (price - entry) / risk, j + 1, i + 1 + j)
        reasons[e] = reason
    res = pd.DataFrame(out, columns=cols)
    res["reason"] = reasons
    return res
