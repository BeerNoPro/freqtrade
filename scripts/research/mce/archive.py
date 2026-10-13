"""
Daily candles and funding for every Binance USDT-M perpetual that ever traded, delisted included.

The public archive (data.binance.vision) lists every USDT-M symbol, and the fapi ``klines`` /
``fundingRate`` endpoints still serve history for symbols already removed from exchangeInfo
(LUNA, MATIC, EOS, SRM...). Together they give a candidate list without survivorship bias.
The few symbols the API rejects (SXP, BTCST...) are rebuilt from the archive's monthly files.
Positioning metrics (open interest, long/short ratios) only live ~30 days in the API, so their
history comes from the archive's daily ``metrics`` files.
Public endpoints (no API key). Files are appended idempotently to
``<outdir>/<kind>/<SYMBOL>.parquet`` with ``kind`` in ``klines_<interval>`` / ``funding`` /
``metrics``. The files keep what the exchange serves; research reads (``read_store_file``,
``load_wide``) drop the fake rows it keeps serving after a delisting.
"""

import io
import logging
import re
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import ccxt
import numpy as np
import pandas as pd
import requests


logger = logging.getLogger(__name__)

S3_LIST = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
ARCHIVE = "https://data.binance.vision/"
KLINES_PREFIX = "data/futures/um/monthly/klines/"
FUNDING_PREFIX = "data/futures/um/monthly/fundingRate/"
METRICS_PREFIX = "data/futures/um/daily/metrics/"
DAY_MS = 86_400_000
INTERVAL_MS = {"1h": 3_600_000, "4h": 14_400_000, "1d": DAY_MS}
PAGE_LIMIT = 1000
FUNDING_PAUSE = 0.65  # fundingRate shares 500 requests / 5 min / IP
KLINE_COLS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trades",
    "taker_base",
    "taker_buy_quote",
    "ignore",
]
PRICE_COLS = ["open", "high", "low", "close", "volume", "quote_volume", "taker_buy_quote"]
# archive metrics column -> stored name (5-minute snapshots)
METRICS_COLS = {
    "sum_open_interest_value": "oi_value",
    "sum_toptrader_long_short_ratio": "top_ls_position",
    "count_toptrader_long_short_ratio": "top_ls_account",
    "count_long_short_ratio": "global_ls_account",
    "sum_taker_long_short_vol_ratio": "taker_ls",
}


def parse_s3_prefixes(xml: str, prefix: str) -> tuple[list[str], bool]:
    """Folder names directly under ``prefix`` in one S3 listing page, and whether more follow."""
    names = re.findall(rf"<Prefix>{re.escape(prefix)}([^/<]+)/</Prefix>", xml)
    return names, "<IsTruncated>true</IsTruncated>" in xml


def list_um_usdt_symbols(timeout: float = 30) -> list[str]:
    """Every USDT-margined perpetual in the archive (quarterly ``_YYMMDD`` contracts excluded)."""
    names: list[str] = []
    marker = ""
    while True:
        params = {"delimiter": "/", "prefix": KLINES_PREFIX}
        if marker:
            params["marker"] = f"{KLINES_PREFIX}{marker}/"
        resp = requests.get(S3_LIST, params=params, timeout=timeout)
        resp.raise_for_status()
        page, truncated = parse_s3_prefixes(resp.text, KLINES_PREFIX)
        names += page
        if not truncated or not page:
            break
        marker = page[-1]
    return sorted({s for s in names if s.endswith("USDT") and "_" not in s})


def klines_to_frame(rows: list[list], now_ms: int | None = None) -> pd.DataFrame:
    """Raw fapi klines -> date (open time, UTC) + prices, base / quote volume and taker buy
    quote volume. Candles still open at ``now_ms`` are dropped."""
    if not rows:
        return pd.DataFrame(columns=["date", *PRICE_COLS])
    raw = pd.DataFrame(rows, columns=KLINE_COLS)
    if now_ms is not None:
        raw = raw[raw["close_time"].astype("int64") < now_ms]
    out = raw[PRICE_COLS].astype(float)
    out.insert(0, "date", pd.to_datetime(raw["open_time"].astype("int64"), unit="ms", utc=True))
    return out.reset_index(drop=True)


def funding_to_frame(rows: list[dict]) -> pd.DataFrame:
    """Raw fapi funding prints -> date (settlement time floored to the minute) + funding_rate.
    Binance stamps some prints a few ms after the hour; flooring keeps day boundaries exact."""
    if not rows:
        return pd.DataFrame(columns=["date", "funding_rate"])
    raw = pd.DataFrame(rows)
    date = pd.to_datetime(raw["fundingTime"].astype("int64"), unit="ms", utc=True).dt.floor("min")
    rate = pd.to_numeric(raw["fundingRate"], errors="coerce")
    return pd.DataFrame({"date": date, "funding_rate": rate})


def underlying_types(exchange: ccxt.Exchange) -> dict[str, list]:
    """Raw symbol -> [underlyingType, underlyingSubType] for USDT-settled swaps in exchangeInfo."""
    return {
        m["id"]: [m["info"].get("underlyingType"), m["info"].get("underlyingSubType") or []]
        for m in exchange.load_markets().values()
        if m.get("swap") and m.get("settle") == "USDT"
    }


def parse_archive_klines(text: str) -> pd.DataFrame:
    """Monthly archive kline CSV (header optional, open time in ms or us) -> klines_to_frame."""
    raw = pd.read_csv(io.StringIO(text), header=None, names=KLINE_COLS, dtype=str)
    raw = raw[raw["open_time"].str.isdigit()].copy()
    t = raw["open_time"].astype("int64")
    raw["open_time"] = t.where(t < 10**14, t // 1000)
    return klines_to_frame(raw.to_numpy().tolist())


def parse_archive_funding(text: str) -> pd.DataFrame:
    """Monthly archive funding CSV (calc_time, funding_interval_hours, last_funding_rate)."""
    cols = ["calc_time", "funding_interval_hours", "last_funding_rate"]
    raw = pd.read_csv(io.StringIO(text), header=None, names=cols, dtype=str)
    raw = raw[raw["calc_time"].str.isdigit()]
    rows = raw.rename(columns={"calc_time": "fundingTime", "last_funding_rate": "fundingRate"})
    return funding_to_frame(rows.to_dict("records"))


def parse_metrics_csv(text: str, minute: int = 50) -> pd.DataFrame:
    """Daily archive metrics CSV -> hourly snapshots taken at ``minute`` past each hour (date =
    snapshot time, UTC). Keeping one row per hour is enough for 4h decisions and keeps files small;
    minute 50 leaves a 10-minute margin before a candle closes on the hour."""
    raw = pd.read_csv(io.StringIO(text))
    date = pd.to_datetime(raw["create_time"], utc=True)
    out = raw[list(METRICS_COLS)].rename(columns=METRICS_COLS).astype(float)
    out.insert(0, "date", date)
    return out[date.dt.minute == minute].reset_index(drop=True)


def archive_zip_keys(prefix: str, timeout: float = 30) -> list[str]:
    """All ``.zip`` object keys under ``prefix`` in the public archive."""
    keys: list[str] = []
    marker = ""
    while True:
        params = {"prefix": prefix, "marker": marker} if marker else {"prefix": prefix}
        resp = requests.get(S3_LIST, params=params, timeout=timeout)
        resp.raise_for_status()
        page = re.findall(r"<Key>([^<]+\.zip)</Key>", resp.text)
        keys += page
        if "<IsTruncated>true</IsTruncated>" not in resp.text or not page:
            break
        marker = page[-1]
    return keys


def _zip_text(key: str, timeout: float = 30) -> str:
    resp = requests.get(ARCHIVE + key, timeout=timeout)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        return z.read(z.namelist()[0]).decode()


def _path(outdir: Path, kind: str, symbol: str) -> Path:
    return outdir / kind / f"{symbol}.parquet"


def prune(outdir: Path, kind: str, keep: list[str], start: pd.Timestamp) -> int:
    """Delete files of symbols not in ``keep`` and rows dated before ``start``. Returns files
    deleted. Keeps the research store limited to the current candidates and data window."""
    deleted = 0
    for path in sorted((outdir / kind).glob("*.parquet")):
        if path.stem not in keep:
            path.unlink()
            deleted += 1
            continue
        df = pd.read_parquet(path)
        if len(df) and df["date"].min() < start:
            df[df["date"] >= start].to_parquet(path, index=False)
    return deleted


def missing(outdir: Path, kind: str, symbols: list[str]) -> list[str]:
    """Symbols without a stored file of ``kind``."""
    return [s for s in symbols if not _path(outdir, kind, s).exists()]


def sync_from_archive(symbol: str, outdir: Path, kind: str, start: pd.Timestamp) -> int:
    """Fallback for symbols the API rejects: rebuild ``kind`` (``klines_<interval>`` or
    ``funding``) from monthly archive files (``<SYMBOL>-<x>-YYYY-MM.zip``) from ``start`` on."""
    if kind.startswith("klines_"):
        interval = kind.removeprefix("klines_")
        prefix, parse = f"{KLINES_PREFIX}{symbol}/{interval}/", parse_archive_klines
    else:
        prefix, parse = f"{FUNDING_PREFIX}{symbol}/", parse_archive_funding
    path = _path(outdir, kind, symbol)
    old = pd.read_parquet(path) if path.exists() else pd.DataFrame()
    keys = [k for k in archive_zip_keys(prefix) if k[-11:-4] >= start.strftime("%Y-%m")]
    frames = [parse(_zip_text(key)) for key in keys]
    new = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return _append(path, old, new[new["date"] >= start] if len(new) else new)


def _append(path: Path, old: pd.DataFrame, new: pd.DataFrame) -> int:
    if new.empty:
        return 0
    merged = pd.concat([old, new], ignore_index=True) if len(old) else new
    merged = merged.drop_duplicates("date", keep="last").sort_values("date")
    path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(path, index=False)
    return len(merged) - len(old)


def _since(old: pd.DataFrame, start_ms: int, step_ms: int) -> int:
    if not len(old):
        return start_ms
    return int(old["date"].max().timestamp() * 1000) + step_ms


def sync_klines(
    exchange: ccxt.Exchange, symbol: str, outdir: Path, start_ms: int, interval: str = "1d"
) -> int:
    """Append closed candles of raw ``symbol`` (e.g. ``LUNAUSDT``). Returns rows added."""
    path = _path(outdir, f"klines_{interval}", symbol)
    step = INTERVAL_MS[interval]
    old = pd.read_parquet(path) if path.exists() else pd.DataFrame()
    since, now = _since(old, start_ms, step), exchange.milliseconds()
    rows: list[list] = []
    while since < now:
        params = {"symbol": symbol, "interval": interval, "startTime": since, "limit": PAGE_LIMIT}
        page = exchange.fapiPublicGetKlines(params)
        if not page:
            break
        rows += page
        since = int(page[-1][0]) + step
        if len(page) < PAGE_LIMIT:
            break
    return _append(path, old, klines_to_frame(rows, now))


def sync_funding(exchange: ccxt.Exchange, symbol: str, outdir: Path, start_ms: int) -> int:
    """Append funding prints of raw ``symbol``. Returns rows added."""
    path = _path(outdir, "funding", symbol)
    old = pd.read_parquet(path) if path.exists() else pd.DataFrame()
    since, now = _since(old, start_ms, 60_000), exchange.milliseconds()
    rows: list[dict] = []
    while since < now:
        page = exchange.fapiPublicGetFundingRate(
            {"symbol": symbol, "startTime": since, "limit": PAGE_LIMIT}
        )
        time.sleep(FUNDING_PAUSE)
        if not page:
            break
        rows += page
        since = int(page[-1]["fundingTime"]) + 1
        if len(page) < PAGE_LIMIT:
            break
    return _append(path, old, funding_to_frame(rows))


def sync_metrics(
    symbol: str, outdir: Path, start: pd.Timestamp, end: pd.Timestamp, workers: int = 16
) -> int:
    """Append hourly positioning snapshots for days in [start, end] from the archive's daily
    metrics files, skipping days already stored. Returns rows added."""
    path = _path(outdir, "metrics", symbol)
    old = pd.read_parquet(path) if path.exists() else pd.DataFrame()
    have = set(old["date"].dt.strftime("%Y-%m-%d")) if len(old) else set()
    lo, hi = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    keys = [
        k
        for k in archive_zip_keys(f"{METRICS_PREFIX}{symbol}/")
        if lo <= k[-14:-4] <= hi and k[-14:-4] not in have
    ]
    with ThreadPoolExecutor(workers) as pool:
        frames = list(pool.map(lambda key: parse_metrics_csv(_zip_text(key)), keys))
    new = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return _append(path, old, new)


def traded(k: pd.DataFrame) -> pd.Series:
    """Candles with at least one trade (quote volume > 0)."""
    return k["quote_volume"] > 0


def drop_dead_tail(k: pd.DataFrame) -> pd.DataFrame:
    """Candles up to the last one with a trade. After a delisting the klines endpoint keeps
    serving flat zero-volume candles up to today; idle candles inside the series stay."""
    hit = np.flatnonzero(traded(k).to_numpy())
    return k.iloc[: hit[-1] + 1 if hit.size else 0].reset_index(drop=True)


def trading_span(
    outdir: Path, symbol: str, end: pd.Timestamp | None = None
) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    """(open of the coin's first candle with a trade, close of its last one) before ``end``. The
    start comes from the daily candles (kept for every candidate from the store's start; the 4h
    ones only from shortly before a coin first enters the universe), the end from the 4h candles
    (finer); each falls back on the other interval when it has no candle before ``end``. (NaT,
    NaT) when none traded, None without any candle (nothing to judge by). Funding outside it
    belongs to an older contract on the ticker (the endpoint serves it) or follows the delisting."""
    bounds = {}
    for interval in ("1d", "4h"):
        k = read_store_file(outdir, f"klines_{interval}", symbol, end, raw=True)
        if k is None or k.empty:
            continue
        hit = np.flatnonzero(traded(k).to_numpy())
        if not hit.size:
            return pd.NaT, pd.NaT
        bar = pd.Timedelta(INTERVAL_MS[interval], "ms")
        bounds[interval] = (k["date"].iloc[hit[0]], k["date"].iloc[hit[-1]] + bar)
    if not bounds:
        return None
    start = bounds.get("1d") or bounds["4h"]
    stop = bounds.get("4h") or bounds["1d"]
    return start[0], stop[1]


def read_store_file(
    outdir: Path, kind: str, symbol: str, end: pd.Timestamp | None = None, raw: bool = False
) -> pd.DataFrame | None:
    """One stored file, rows dated strictly before ``end`` (every row without it), sorted; unless
    ``raw``, without the exchange's fake rows: candles after the last one with a trade
    (``drop_dead_tail``) and prints of any ``funding*`` kind outside the coin's ``trading_span``.
    Every research read goes through it; the downloads and the audit read the raw files."""
    path = _path(outdir, kind, symbol)
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    if end is not None:
        df = df[df["date"] < end]
    df = df.sort_values("date").reset_index(drop=True)
    if raw:
        return df
    if kind.startswith("klines_"):
        return drop_dead_tail(df)
    if kind.startswith("funding"):
        span = trading_span(outdir, symbol, end)
        if span is not None:  # NaT bounds (no trade at all) keep no print
            df = df[(df["date"] >= span[0]) & (df["date"] <= span[1])].reset_index(drop=True)
    return df


def load_wide(
    outdir: Path,
    kind: str,
    field: str,
    symbols: list[str],
    end: pd.Timestamp | None = None,
    raw: bool = False,
) -> pd.DataFrame:
    """One column per symbol (missing files skipped), rows = dates (UTC) before ``end``, sorted,
    read with ``read_store_file`` (without fake rows unless ``raw``)."""
    cols = {}
    for symbol in symbols:
        df = read_store_file(outdir, kind, symbol, end, raw)
        if df is not None:
            cols[symbol] = df.set_index("date")[field]
    if not cols:
        return pd.DataFrame()
    return pd.DataFrame(cols).sort_index()
