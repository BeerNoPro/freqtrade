"""
Daily candles and funding for every Binance USDT-M perpetual that ever traded, delisted included.

The public archive (data.binance.vision) lists every USDT-M symbol, and the fapi ``klines`` /
``fundingRate`` endpoints still serve history for symbols already removed from exchangeInfo
(LUNA, MATIC, EOS, SRM...). Together they give a candidate list without survivorship bias.
The few symbols the API rejects (SXP, BTCST...) are rebuilt from the archive's monthly files.
Public endpoints (no API key). Files are appended idempotently to
``<outdir>/<kind>/<SYMBOL>.parquet`` with ``kind`` in ``klines_1d`` / ``funding``.
"""

import io
import logging
import re
import time
import zipfile
from pathlib import Path

import ccxt
import pandas as pd
import requests


logger = logging.getLogger(__name__)

S3_LIST = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
ARCHIVE = "https://data.binance.vision/"
KLINES_PREFIX = "data/futures/um/monthly/klines/"
FUNDING_PREFIX = "data/futures/um/monthly/fundingRate/"
DAY_MS = 86_400_000
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
    "taker_quote",
    "ignore",
]
PRICE_COLS = ["open", "high", "low", "close", "volume", "quote_volume"]


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
    """Raw fapi klines -> date (open time, UTC) + prices + base and quote volume.
    Candles still open at ``now_ms`` are dropped."""
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
    """Fallback for symbols the API rejects: rebuild ``kind`` from monthly archive files
    (``<SYMBOL>-<kind>-YYYY-MM.zip``) of months from ``start`` on."""
    if kind == "klines_1d":
        prefix, parse = f"{KLINES_PREFIX}{symbol}/1d/", parse_archive_klines
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


def sync_klines(exchange: ccxt.Exchange, symbol: str, outdir: Path, start_ms: int) -> int:
    """Append closed daily candles of raw ``symbol`` (e.g. ``LUNAUSDT``). Returns rows added."""
    path = _path(outdir, "klines_1d", symbol)
    old = pd.read_parquet(path) if path.exists() else pd.DataFrame()
    since, now = _since(old, start_ms, DAY_MS), exchange.milliseconds()
    rows: list[list] = []
    while since < now:
        params = {"symbol": symbol, "interval": "1d", "startTime": since, "limit": PAGE_LIMIT}
        page = exchange.fapiPublicGetKlines(params)
        if not page:
            break
        rows += page
        since = int(page[-1][0]) + DAY_MS
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


def load_wide(outdir: Path, kind: str, field: str, symbols: list[str]) -> pd.DataFrame:
    """One column per symbol (missing files skipped), rows = dates (UTC), sorted."""
    cols = {}
    for symbol in symbols:
        path = _path(outdir, kind, symbol)
        if path.exists():
            df = pd.read_parquet(path, columns=["date", field])
            cols[symbol] = df.set_index("date")[field]
    if not cols:
        return pd.DataFrame()
    return pd.DataFrame(cols).sort_index()
