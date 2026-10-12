"""
Audit of a research data store (the layout of ``download_pit_universe.py``): checks of stored
candles, funding prints and positioning snapshots, a daily-vs-4h consistency check, and a sampled
comparison with two independent sources, the fapi endpoints and the data.binance.vision archive.
The sources are passed in as ``Sources`` so that this module never touches the network
(``scripts/research/audit_store.py`` wires the real ones).

Every issue is one row ``kind, symbol, check, date, detail, severity``:
- critical: the data is wrong (differs from a source, duplicated, out of order, impossible prices,
  daily candle not equal to its 4h candles);
- warning: incomplete or misleading (fake candles after a delisting, gaps, a reused ticker, zero
  open interest, funding printed after the last traded day, history that does not match the
  archive's listing months);
- info: worth knowing (a trading halt).

After a delisting the klines endpoint keeps serving candles up to today with a constant price and
zero volume. The last traded day (last candle with quote volume > 0) is the real end of trading.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


ISSUE_COLS = ["kind", "symbol", "check", "date", "detail", "severity"]
DIFF_COLS = ["date", "column", "stored", "source", "status"]
PRICE = ["open", "high", "low", "close"]
KLINE_COMPARE = [*PRICE, "volume", "quote_volume", "taker_buy_quote"]
BAR = {"1h": pd.Timedelta(hours=1), "4h": pd.Timedelta(hours=4), "1d": pd.Timedelta(days=1)}
HALT = pd.Timedelta(days=7)  # zero-volume runs at least this long are reported
RELIST_JUMP = 5.0  # price ratio across a halt that suggests a different token on the ticker
FUNDING_OUTLIER = 0.03  # above every Binance funding cap
SNAPSHOT_MINUTE = 50  # the hourly open interest snapshot kept by mce.archive.parse_metrics_csv
DAY = pd.Timedelta(days=1)
VERIFY_KINDS = ("klines_1d", "klines_4h", "funding", "metrics", "dates")


def _issue(kind: str, symbol: str, check: str, date, detail: str, severity: str) -> dict:
    return {
        "kind": kind,
        "symbol": symbol,
        "check": check,
        "date": date,
        "detail": detail,
        "severity": severity,
    }


def _frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=ISSUE_COLS)


def _order(df: pd.DataFrame, kind: str, symbol: str) -> tuple[list[dict], pd.DataFrame]:
    """Duplicated and out-of-order dates, and the frame deduplicated (first row kept) and sorted."""
    dates = df["date"].reset_index(drop=True)
    dup = dates.duplicated(keep="first")
    rows = [
        _issue(kind, symbol, "duplicate", d, "date stored twice", "critical") for d in dates[dup]
    ]
    unique = dates[~dup]
    back = unique.diff() < pd.Timedelta(0)
    rows += [
        _issue(kind, symbol, "unsorted", d, "date earlier than the row before", "critical")
        for d in unique[back]
    ]
    clean = df.drop_duplicates("date", keep="first").sort_values("date").reset_index(drop=True)
    return rows, clean


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """(first, last) positions of each run of True values."""
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return []
    breaks = np.flatnonzero(np.diff(idx) > 1)
    starts = np.r_[idx[0], idx[breaks + 1]]
    ends = np.r_[idx[breaks], idx[-1]]
    return list(zip(starts.tolist(), ends.tolist(), strict=True))


def _missing_runs(dates: pd.Series, step: pd.Timedelta) -> list[tuple[pd.Timestamp, int]]:
    """(first missing time, count) of each hole in the regular grid from first to last date."""
    if len(dates) < 2:
        return []
    grid = pd.date_range(dates.iloc[0], dates.iloc[-1], freq=step)
    missing = ~grid.isin(dates)
    return [(grid[a], b - a + 1) for a, b in _runs(missing)]


# ------------------------------------------------------------------ candles


def _bad_prices(k: pd.DataFrame) -> pd.Series:
    p = k[PRICE]
    body_hi = p[["open", "close"]].max(axis=1)
    body_lo = p[["open", "close"]].min(axis=1)
    return (
        p.isna().any(axis=1)
        | (p <= 0).any(axis=1)
        | (k["high"] < k["low"])
        | (k["high"] < body_hi)
        | (k["low"] > body_lo)
    )


def _halts(
    k: pd.DataFrame, traded: np.ndarray, tail: int, step: pd.Timedelta, kind: str, symbol: str
):
    """Interior zero-volume runs of at least ``HALT``: a halt, or a relisted ticker when the price
    jumps by ``RELIST_JUMP`` or more across it. Runs at the start (before the first trade) and the
    trailing zombie run are left out."""
    rows = []
    min_bars = int(HALT / step)
    for a, b in _runs(~traded[: len(traded) - tail]):
        if a == 0 or b + 1 >= len(k) or b - a + 1 < min_bars:
            continue
        before, after = k["close"].iloc[a - 1], k["close"].iloc[b + 1]
        ratio = after / before if before > 0 else np.inf
        detail = f"{b - a + 1} zero-volume candles, close {before:.6g} -> {after:.6g}"
        if ratio >= RELIST_JUMP or ratio <= 1 / RELIST_JUMP:
            rows.append(
                _issue(kind, symbol, "relist_suspect", k["date"].iloc[b + 1], detail, "warning")
            )
        else:
            rows.append(_issue(kind, symbol, "halt", k["date"].iloc[a], detail, "info"))
    return rows


def scan_klines(
    df: pd.DataFrame, interval: str, symbol: str = "", kind: str | None = None
) -> tuple[dict, pd.DataFrame]:
    """Checks of one candle file. Summary: rows, first / last date, last traded date, trailing
    zero-volume flat candles (``zombie_rows``) and missing candles inside the series."""
    kind = kind or f"klines_{interval}"
    step = BAR[interval]
    rows, k = _order(df, kind, symbol)
    if k.empty:  # a coin without rows since the store's start (delisted before it)
        rows.append(_issue(kind, symbol, "empty", pd.NaT, "file without rows", "info"))
        summary = {"kind": kind, "symbol": symbol, "rows": 0, "first": pd.NaT, "last": pd.NaT}
        summary |= {"last_traded": pd.NaT, "zombie_rows": 0, "missing": 0, "issues": 1}
        return summary, _frame(rows)
    bad = _bad_prices(k)
    rows += [
        _issue(kind, symbol, "bad_ohlc", d, "impossible or missing price", "critical")
        for d in k.loc[bad, "date"]
    ]
    volume = k["quote_volume"] if "quote_volume" in k else k["volume"]
    traded = (volume > 0).to_numpy()
    dead = ~traded & (k["high"] == k["low"]).to_numpy()
    tail = len(k) - (np.flatnonzero(~dead)[-1] + 1) if (~dead).any() else len(k)
    if tail:
        first, last = k["date"].iloc[len(k) - tail], k["date"].iloc[-1]
        detail = f"{tail} zero-volume flat candles from {first} to {last}"
        rows.append(_issue(kind, symbol, "zombie_tail", first, detail, "warning"))
    gaps = _missing_runs(k["date"], step)
    rows += [
        _issue(kind, symbol, "gap", t, f"{n} missing {interval} candles", "warning")
        for t, n in gaps
    ]
    rows += _halts(k, traded, tail, step, kind, symbol)
    summary = {
        "kind": kind,
        "symbol": symbol,
        "rows": len(df),
        "first": k["date"].iloc[0] if len(k) else pd.NaT,
        "last": k["date"].iloc[-1] if len(k) else pd.NaT,
        "last_traded": k.loc[traded, "date"].max() if traded.any() else pd.NaT,
        "zombie_rows": int(tail),
        "missing": int(sum(n for _, n in gaps)),
        "issues": len(rows),
    }
    return summary, _frame(rows)


# ------------------------------------------------------------------ funding


def _funding_gaps(dates: pd.Series, symbol: str) -> list[dict]:
    """A print is missing when the time since the previous print is more than 1.5 times the
    longer of the neighbouring intervals, so Binance switching between 8h, 4h and 1h is not a
    gap. The reported date is the first missing settlement."""
    diff = dates.reset_index(drop=True).diff()
    expected = pd.concat([diff.shift(1), diff.shift(-1)], axis=1).max(axis=1)
    rows = []
    for i in np.flatnonzero((diff > 1.5 * expected).to_numpy()):
        n = round(diff.iloc[i] / expected.iloc[i]) - 1
        when = dates.iloc[i - 1] + expected.iloc[i]
        rows.append(_issue("funding", symbol, "gap", when, f"~{n} prints missing", "warning"))
    return rows


def scan_funding(
    df: pd.DataFrame, symbol: str = "", last_traded: pd.Timestamp | None = None
) -> tuple[dict, pd.DataFrame]:
    """Checks of one funding file. ``last_traded``: the coin's last traded day (daily candle
    date); prints after the settlement that closes it are reported once."""
    rows, f = _order(df, "funding", symbol)
    rate = f["funding_rate"]
    rows += [
        _issue("funding", symbol, "bad_value", d, "missing funding rate", "critical")
        for d in f.loc[rate.isna(), "date"]
    ]
    rows += [
        _issue("funding", symbol, "outlier", d, f"rate {r:.4%}", "warning")
        for d, r in zip(f["date"], rate, strict=True)
        if abs(r) > FUNDING_OUTLIER
    ]
    rows += _funding_gaps(f["date"], symbol)
    if last_traded is not None and pd.notna(last_traded):
        late = f.loc[f["date"] > last_traded + DAY, "date"]
        if len(late):
            detail = f"{len(late)} prints after the last traded day {last_traded.date()}"
            rows.append(_issue("funding", symbol, "after_delist", late.iloc[0], detail, "warning"))
    hours = (f["date"].diff().dt.total_seconds() / 3600).round()
    summary = {
        "kind": "funding",
        "symbol": symbol,
        "rows": len(df),
        "first": f["date"].iloc[0] if len(f) else pd.NaT,
        "last": f["date"].iloc[-1] if len(f) else pd.NaT,
        "interval_hours": float(hours.mode().iloc[0]) if hours.notna().any() else np.nan,
        "issues": len(rows),
    }
    return summary, _frame(rows)


# ------------------------------------------------------------------ positioning metrics


def scan_metrics(df: pd.DataFrame, symbol: str = "") -> tuple[dict, pd.DataFrame]:
    """Checks of one hourly snapshot file (minute ``SNAPSHOT_MINUTE``): missing hours, open
    interest <= 0 or missing, snapshots at another minute."""
    rows, m = _order(df, "metrics", symbol)
    on_time = m["date"].dt.minute == SNAPSHOT_MINUTE
    rows += [
        _issue("metrics", symbol, "wrong_minute", d, "snapshot not at minute 50", "warning")
        for d in m.loc[~on_time, "date"]
    ]
    zero = ~(m["oi_value"] > 0)
    rows += [
        _issue("metrics", symbol, "zero_oi", d, "open interest <= 0 or missing", "warning")
        for d in m.loc[zero, "date"]
    ]
    gaps = _missing_runs(m.loc[on_time, "date"].reset_index(drop=True), pd.Timedelta(hours=1))
    rows += [_issue("metrics", symbol, "gap", t, f"{n} missing hours", "warning") for t, n in gaps]
    summary = {
        "kind": "metrics",
        "symbol": symbol,
        "rows": len(df),
        "first": m["date"].iloc[0] if len(m) else pd.NaT,
        "last": m["date"].iloc[-1] if len(m) else pd.NaT,
        "missing_hours": int(sum(n for _, n in gaps)),
        "issues": len(rows),
    }
    return summary, _frame(rows)


# ------------------------------------------------------------------ daily vs 4h


def cross_check_daily(
    d1: pd.DataFrame, h4: pd.DataFrame, symbol: str = "", rtol: float = 1e-6
) -> pd.DataFrame:
    """Each daily candle against its six 4h candles (open, high, low, close, volume, quote
    volume). Days with fewer than six 4h candles are skipped."""
    h = h4.drop_duplicates("date").sort_values("date")
    g = h.groupby(h["date"].dt.floor("D"))
    agg = pd.DataFrame(
        {
            "open": g["open"].first(),
            "high": g["high"].max(),
            "low": g["low"].min(),
            "close": g["close"].last(),
            "volume": g["volume"].sum(),
            "quote_volume": g["quote_volume"].sum(),
        }
    )[g.size() == 6]
    d = d1.drop_duplicates("date").set_index("date")
    days = agg.index.intersection(d.index)
    cols = [c for c in agg.columns if c in d.columns]
    a, b = agg.loc[days, cols].to_numpy(float), d.loc[days, cols].to_numpy(float)
    bad = ~np.isclose(a, b, rtol=rtol, atol=1e-12, equal_nan=True)
    rows = []
    for i in np.flatnonzero(bad.any(axis=1)):
        which = ", ".join(c for c, x in zip(cols, bad[i], strict=True) if x)
        detail = f"daily candle differs from its 4h candles in {which}"
        rows.append(_issue("klines_1d", symbol, "d1_h4_mismatch", days[i], detail, "critical"))
    return _frame(rows)


# ------------------------------------------------------------------ comparison with a source


def compare_frames(
    stored: pd.DataFrame, source: pd.DataFrame, cols: list[str], rtol: float = 1e-9
) -> pd.DataFrame:
    """Rows of ``stored`` and ``source`` (same window) that differ: a value beyond ``rtol``
    (``value``), a value the store lacks but the source has (``missing_value``), or a date
    present on one side only."""
    a = stored.drop_duplicates("date").set_index("date")
    b = source.drop_duplicates("date").set_index("date")
    rows = [
        {"date": d, "column": "", "stored": np.nan, "source": np.nan, "status": "missing_in_source"}
        for d in a.index.difference(b.index)
    ]
    rows += [
        {"date": d, "column": "", "stored": np.nan, "source": np.nan, "status": "missing_in_store"}
        for d in b.index.difference(a.index)
    ]
    common = a.index.intersection(b.index)
    for c in (c for c in cols if c in a.columns and c in b.columns):
        x, y = a.loc[common, c].to_numpy(float), b.loc[common, c].to_numpy(float)
        bad = ~np.isclose(x, y, rtol=rtol, atol=0.0, equal_nan=True)
        status = np.where(np.isnan(x) & ~np.isnan(y), "missing_value", "value")
        rows += [
            {"date": common[i], "column": c, "stored": x[i], "source": y[i], "status": status[i]}
            for i in np.flatnonzero(bad)
        ]
    out = pd.DataFrame(rows, columns=DIFF_COLS)
    return out.sort_values(["date", "column"]).reset_index(drop=True)


def apply_known(issues: pd.DataFrame, known: pd.DataFrame) -> pd.DataFrame:
    """Issues listed in ``known`` (kind, symbol, check, date as ``YYYY-MM-DD``, reason: checked by
    hand against Binance) get severity ``known`` and the reason added to their detail, so that
    only new problems count as critical. Symbol ``*`` matches every symbol (an exchange-wide
    day)."""
    out = issues.copy()
    if known.empty or out.empty:
        return out

    def keys(df: pd.DataFrame, symbol: pd.Series) -> pd.Series:
        day = pd.to_datetime(df["date"], utc=True).dt.strftime("%Y-%m-%d")
        return df["kind"] + "|" + symbol + "|" + df["check"] + "|" + day

    listed = dict(zip(keys(known, known["symbol"]), known["reason"], strict=True))
    reason = keys(out, out["symbol"]).map(listed)
    reason = reason.fillna(keys(out, pd.Series("*", index=out.index)).map(listed))
    hit = reason.notna()
    out.loc[hit, "severity"] = "known"
    out.loc[hit, "detail"] = out.loc[hit, "detail"] + " | known: " + reason[hit]
    return out


def zombie_member_days(members: pd.DataFrame, quote_volume: pd.DataFrame) -> pd.DataFrame:
    """(symbol, date) of universe members on a day without any trade: fake candles that the
    point-in-time universe still counts as a member."""
    zero = quote_volume.reindex_like(members).fillna(0.0) <= 0
    rows, cols = np.nonzero((members & zero).to_numpy())
    out = pd.DataFrame({"symbol": members.columns[cols], "date": members.index[rows]})
    return out.sort_values(["symbol", "date"]).reset_index(drop=True)


# ------------------------------------------------------------------ sampled verification


class SourceError(Exception):
    """A source still failed after its retries (network, server): the unit is reported as a
    warning and the verification goes on."""


@dataclass(frozen=True)
class Sources:
    """Independent copies of the stored data. Readers return None when the source does not have
    the data (no archive file, a symbol the API rejects) and raise ``SourceError`` when it fails;
    ``archive_months`` lists the ``YYYY-MM`` monthly kline files of a symbol."""

    api_klines: Callable[[str, str, pd.Timestamp, pd.Timestamp], pd.DataFrame | None]
    archive_klines: Callable[[str, str, str], pd.DataFrame | None]
    api_funding: Callable[[str, pd.Timestamp, pd.Timestamp], pd.DataFrame | None]
    archive_funding: Callable[[str, str], pd.DataFrame | None]
    archive_metrics: Callable[[str, str], pd.DataFrame | None]
    archive_months: Callable[[str, str], list[str]]


class _Store:
    """Cached reads of one store and the coverage of each file (first and last date, last traded
    day from the daily candles)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._cache: dict[tuple[str, str], pd.DataFrame] = {}

    def symbols(self, kind: str) -> list[str]:
        """Symbols with at least one stored row of ``kind``."""
        names = sorted(p.stem for p in (self.path / kind).glob("*.parquet"))
        return [s for s in names if len(self.read(kind, s))]

    def read(self, kind: str, symbol: str) -> pd.DataFrame:
        key = (kind, symbol)
        if key not in self._cache:
            df = pd.read_parquet(self.path / kind / f"{symbol}.parquet")
            self._cache[key] = df.drop_duplicates("date").sort_values("date").reset_index(drop=True)
        return self._cache[key]

    def last_traded(self, symbol: str) -> pd.Timestamp:
        if not (self.path / "klines_1d" / f"{symbol}.parquet").exists():
            return pd.NaT
        d = self.read("klines_1d", symbol)
        return d.loc[d["quote_volume"] > 0, "date"].max()

    def start(self) -> pd.Timestamp:
        """The store's ``--start`` (rows before it were pruned), else its earliest daily candle."""
        meta = self.path / "symbols.json"
        if meta.exists():
            start = json.loads(meta.read_text(encoding="utf-8")).get("start")
            if start:
                return pd.Timestamp(start, tz="UTC")
        firsts = [self.read("klines_1d", s)["date"].iloc[0] for s in self.symbols("klines_1d")]
        return min(firsts)


def _units(store: _Store, kind: str, now: pd.Timestamp) -> list[tuple[str, str]]:
    """(symbol, period) that can be verified: completed months (days for metrics) with stored rows,
    up to the coin's last traded day."""
    if kind == "dates":
        return [(s, "") for s in store.symbols("klines_1d")]
    fmt, cutoff = (
        ("%Y-%m-%d", now.floor("D"))
        if kind == "metrics"
        else ("%Y-%m", now.floor("D").replace(day=1))
    )
    units = []
    for symbol in store.symbols(kind):
        dates = store.read(kind, symbol)["date"]
        last = store.last_traded(symbol)
        keep = dates < cutoff
        if pd.notna(last):
            keep &= dates < last + DAY
        units += [(symbol, p) for p in sorted(dates[keep].dt.strftime(fmt).unique())]
    return units


def _pick(units: list[tuple[str, str]], n: int, rng: np.random.Generator) -> list[tuple[str, str]]:
    """``n`` units spread evenly over the years of their periods (round robin), deterministic for
    a given generator state."""
    by_year: dict[str, list] = {}
    for u in sorted(units):
        by_year.setdefault(u[1][:4], []).append(u)
    queues = [[g[i] for i in rng.permutation(len(g))] for _, g in sorted(by_year.items())]
    picked: list[tuple[str, str]] = []
    while len(picked) < n and any(queues):
        for q in queues:
            if q and len(picked) < n:
                picked.append(q.pop(0))
    return picked


def _window(df: pd.DataFrame, lo: pd.Timestamp, hi: pd.Timestamp) -> pd.DataFrame:
    return df[(df["date"] >= lo) & (df["date"] < hi)].reset_index(drop=True)


def _period_window(kind: str, period: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    if kind == "metrics":
        lo = pd.Timestamp(period, tz="UTC")
        return lo, lo + DAY
    lo = pd.Timestamp(f"{period}-01", tz="UTC")
    return lo, lo + pd.offsets.MonthBegin(1)


def _describe(label: str, diff: pd.DataFrame) -> str:
    first = diff.iloc[0]
    return (
        f"{label}: {len(diff)} differences, first {first['date']} {first['column']} "
        f"stored {first['stored']} source {first['source']} ({first['status']})"
    )


def _classify(
    kind: str, symbol: str, when: pd.Timestamp, diffs: dict[str, pd.DataFrame]
) -> list[dict]:
    """Issues from the differences with each available source:
    - source_mismatch (critical): the store differs from every available source;
    - sources_disagree (warning): the store equals one Binance source, the other differs;
    - store_missing_value (warning): values the store lacks but a source has."""
    real = {k: d[d["status"] != "missing_value"] for k, d in diffs.items()}
    rows = []
    if real and all(len(d) for d in real.values()):
        text = "; ".join(_describe(k, d) for k, d in real.items())
        rows.append(_issue(kind, symbol, "source_mismatch", when, text, "critical"))
    elif any(len(d) for d in real.values()):
        text = "; ".join(_describe(k, d) for k, d in real.items() if len(d))
        same = ", ".join(k for k, d in real.items() if not len(d))
        rows.append(
            _issue(
                kind, symbol, "sources_disagree", when, f"{text}; store equals {same}", "warning"
            )
        )
    for label, d in diffs.items():
        gap = d[d["status"] == "missing_value"]
        if len(gap):
            cols = ", ".join(sorted(set(gap["column"])))
            detail = f"{len(gap)} values missing in the store ({cols}), present in {label}"
            rows.append(_issue(kind, symbol, "store_missing_value", when, detail, "warning"))
            break
    return rows


def _verify_unit(
    store: _Store, sources: Sources, kind: str, symbol: str, period: str
) -> tuple[dict, list[dict]]:
    stored_all = store.read(kind, symbol)
    lo, hi = _period_window(kind, period)
    lo = max(lo, stored_all["date"].iloc[0])
    last = store.last_traded(symbol)
    hi = min(hi, stored_all["date"].iloc[-1] + pd.Timedelta(minutes=1))
    if pd.notna(last):
        hi = min(hi, last + DAY)
    stored = _window(stored_all, lo, hi)
    if kind.startswith("klines_"):
        interval = kind.removeprefix("klines_")
        cols = KLINE_COMPARE
        got = {
            "api": sources.api_klines(symbol, interval, lo, hi),
            "archive": sources.archive_klines(symbol, interval, period),
        }
    elif kind == "funding":
        cols = ["funding_rate"]
        got = {
            "api": sources.api_funding(symbol, lo, hi),
            "archive": sources.archive_funding(symbol, period),
        }
    else:
        cols = [c for c in stored.columns if c != "date"]
        got = {"archive": sources.archive_metrics(symbol, period)}
    diffs = {
        label: compare_frames(stored, _window(src, lo, hi), cols)
        for label, src in got.items()
        if src is not None
    }
    sample = {"kind": kind, "symbol": symbol, "period": period, "rows": len(stored)}
    for label in got:
        d = diffs.get(label)
        sample[f"{label}_diff"] = (
            np.nan if d is None else int((d["status"] != "missing_value").sum())
        )
        sample[f"{label}_missing"] = (
            np.nan if d is None else int((d["status"] == "missing_value").sum())
        )
    return sample, _classify(kind, symbol, lo, diffs)


def _month(t: pd.Timestamp) -> str:
    return t.strftime("%Y-%m")


def _month_before(month: str) -> str:
    return _month(pd.Timestamp(f"{month}-01", tz="UTC") - DAY)


def _verify_dates(
    store: _Store, sources: Sources, symbol: str, now: pd.Timestamp
) -> tuple[dict, list[dict]]:
    """
    Months of the archive's monthly daily-candle files against the store:
    - history_mismatch: the store starts later than the archive (after the store's own start),
      e.g. a ticker reused by a new token whose old history the API no longer serves;
    - delist_mismatch: the store's last traded month is before the archive's last month;
    - trading_after_archive: the store trades more than a month after the archive's last file
      (files are published once a month ends, so one month of lag is allowed).
    """
    d = store.read("klines_1d", symbol)
    last_day = store.last_traded(symbol)
    first = _month(d["date"].iloc[0])
    last = _month(last_day) if pd.notna(last_day) else ""
    months = sorted(sources.archive_months(symbol, "1d"))
    sample = {"kind": "dates", "symbol": symbol, "period": "", "rows": len(d), "store_first": first}
    sample["last_traded"] = last
    t0, t1 = d["date"].iloc[0], d["date"].iloc[-1]
    if not months:
        return sample, [_issue("dates", symbol, "not_in_archive", t0, "no monthly file", "warning")]
    sample |= {"archive_first": months[0], "archive_last": months[-1]}
    rows = []
    expected = max(months[0], _month(store.start()))
    if first != expected:
        detail = f"store starts {first}, archive {months[0]} (expected {expected})"
        rows.append(_issue("dates", symbol, "history_mismatch", t0, detail, "warning"))
    if last and last < months[-1]:
        detail = f"last traded {last}, archive runs to {months[-1]}"
        rows.append(_issue("dates", symbol, "delist_mismatch", t1, detail, "warning"))
    published = min(last, _month_before(_month(now))) if last else ""
    if published and months[-1] < _month_before(published):
        detail = f"archive ends {months[-1]}, store trades until {last}"
        rows.append(_issue("dates", symbol, "trading_after_archive", t1, detail, "warning"))
    return sample, rows


def verify_sample(
    store_path: Path,
    sources: Sources,
    n: int,
    seed: int,
    kinds: tuple[str, ...] = VERIFY_KINDS,
    now: pd.Timestamp | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Compare ``n`` sampled units per kind with the sources. Returns (one row per sampled unit
    with the number of differences per source, issues)."""
    now = now if now is not None else pd.Timestamp.now(tz="UTC")
    store = _Store(store_path)
    rng = np.random.default_rng(seed)
    samples, issues = [], []
    for kind in kinds:
        if kind != "dates" and not (store_path / kind).exists():
            continue
        for symbol, period in _pick(_units(store, kind, now), n, rng):
            try:
                if kind == "dates":
                    sample, rows = _verify_dates(store, sources, symbol, now)
                else:
                    sample, rows = _verify_unit(store, sources, kind, symbol, period)
            except SourceError as e:
                sample = {"kind": kind, "symbol": symbol, "period": period, "error": str(e)}
                when = _period_window(kind, period)[0] if period else pd.NaT
                rows = [
                    _issue(kind, symbol, "source_error", when, f"source failed: {e}", "warning")
                ]
            samples.append(sample)
            issues += rows
    return pd.DataFrame(samples), _frame(issues)
