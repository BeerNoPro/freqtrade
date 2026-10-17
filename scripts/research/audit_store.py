"""
Audit a research data store against itself and against Binance.

    .venv/Scripts/python.exe scripts/research/audit_store.py                  # offline scan of pit
    .venv/Scripts/python.exe scripts/research/audit_store.py --verify 200     # + check with Binance
    .venv/Scripts/python.exe scripts/research/audit_store.py --store user_data/research/pit_hist

Offline: every stored file (checks of ``mce.audit``), each daily candle against its six 4h
candles, and the members of the top-N point-in-time universe on days without any trade (fake
candles served after a delisting; ``mce.universe.point_in_time_members`` with the parameters of
``mce.pit.daily_members``). ``--verify N``: N sampled units per data kind compared with two
independent sources, the fapi endpoints and the data.binance.vision archive, plus the listing and
delisting months of N symbols against the archive's monthly files (``--dates-all``: of every
symbol). Ticker changes (``mce.listings``): marked renames, reused and relisted tickers measured
on the store, unmarked pairs that look like a rename (warning ``rename_suspect``).

Writes ``<out>/<store>-<YYYYMMDD>/`` (summary.md, issues.csv, symbols.csv, zombie_members.csv,
listings.csv, verify.csv) and a copy in ``<out>/latest/<store>/``. Exit code 1 when a critical
issue is found.
"""

import argparse
import json
import logging
import shutil
import time
from collections.abc import Callable
from pathlib import Path

import ccxt
import pandas as pd
import requests
from mce.archive import (
    FUNDING_PAUSE,
    FUNDING_PREFIX,
    KLINES_PREFIX,
    METRICS_PREFIX,
    _zip_text,
    archive_zip_keys,
    funding_to_frame,
    klines_to_frame,
    parse_archive_funding,
    parse_archive_klines,
    parse_metrics_csv,
)
from mce.audit import (
    ISSUE_COLS,
    VERIFY_KINDS,
    SourceError,
    Sources,
    apply_known,
    cross_check_daily,
    scan_funding,
    scan_klines,
    scan_metrics,
    verify_sample,
    zombie_member_days,
)
from mce.listings import RELISTED, RENAMES, REUSED, listing_table, rename_candidates
from mce.live import with_retry
from mce.universe import point_in_time_members


REPO = Path(__file__).resolve().parents[2]
KINDS = ("klines_1d", "klines_4h", "funding", "metrics")  # daily first: it gives last traded days
SEVERITIES = ("critical", "warning", "info", "known")
logger = logging.getLogger("audit_store")


# ------------------------------------------------------------------ offline scan


def scan_store(
    store: Path, kinds: list[str]
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, pd.DataFrame]]:
    """(one summary row per file, issues, daily candles per symbol)."""
    summaries, parts = [], []
    last_traded: dict[str, pd.Timestamp] = {}
    daily: dict[str, pd.DataFrame] = {}
    h4: dict[str, pd.DataFrame] = {}
    for kind in kinds:
        paths = sorted((store / kind).glob("*.parquet"))
        for path in paths:
            symbol, df = path.stem, pd.read_parquet(path)
            if kind.startswith("klines_"):
                summary, issues = scan_klines(df, kind.removeprefix("klines_"), symbol, kind)
                if kind == "klines_1d":
                    daily[symbol] = df
                    if summary["zombie_rows"]:  # delisted: trading stopped before the last row
                        last_traded[symbol] = summary["last_traded"]
                elif kind == "klines_4h":
                    h4[symbol] = df
            elif kind == "funding":
                summary, issues = scan_funding(df, symbol, last_traded.get(symbol))
            else:
                summary, issues = scan_metrics(df, symbol)
            summaries.append(summary)
            parts.append(issues)
        logger.info("%s: %d files scanned", kind, len(paths))
    parts += [cross_check_daily(daily[s], h, s) for s, h in h4.items() if s in daily]
    parts = [p for p in parts if len(p)]
    issues = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=ISSUE_COLS)
    return pd.DataFrame(summaries), issues, daily


def zombie_members(store: Path, daily: dict[str, pd.DataFrame], top: int) -> pd.DataFrame:
    """Top-``top`` point-in-time members (30-day median quote volume, 90 daily closes, month-end
    ranking) on days without any trade, when the raw candles are taken at face value. Research
    reads drop those candles (``mce.archive.read_store_file``); this measures the raw store."""
    meta = store / "symbols.json"
    names = list(daily)
    if meta.exists():
        names = [
            s for s in json.loads(meta.read_text(encoding="utf-8"))["candidates"] if s in daily
        ]
    frames = {s: daily[s].drop_duplicates("date").set_index("date") for s in names}
    close = pd.DataFrame({s: f["close"] for s, f in frames.items()}).sort_index()
    days = pd.date_range(close.index.min(), close.index.max(), freq="D")
    volume = pd.DataFrame({s: f["quote_volume"] for s, f in frames.items()}).reindex(days)
    members = point_in_time_members(close.reindex(days), volume, top, 30, 90)
    return zombie_member_days(members, volume)


def listing_issues(listings: pd.DataFrame) -> pd.DataFrame:
    """Warnings of ticker changes: an unmarked pair that looks like a rename
    (``rename_suspect``) and rows of a reused or relisted ticker dated before its current token
    or contract (``rows_before_current``)."""
    rows = [
        {
            "kind": "dates",
            "symbol": c.new,
            "check": "rename_suspect",
            "date": c.new_first_traded,
            "detail": (
                f"first trade {c.gap_days} days after {c.old} stopped, first close / last close "
                f"{c.price_ratio:.3f}: mark it in mce.listings.RENAMES if Binance renamed it"
            ),
            "severity": "warning",
        }
        for c in rename_candidates(listings).itertuples()
    ]
    rows += [
        {
            "kind": "dates",
            "symbol": r.symbol,
            "check": "rows_before_current",
            "date": r.current_from,
            "detail": f"{r.rows_before_current} rows before the {r.ticker_change} ticker's start",
            "severity": "warning",
        }
        for r in listings[listings["rows_before_current"] > 0].itertuples()
    ]
    return pd.DataFrame(rows, columns=ISSUE_COLS)


# ------------------------------------------------------------------ Binance sources


def _ms(t: pd.Timestamp) -> int:
    return int(t.timestamp() * 1000)


def _archive(key: str, parse: Callable[[str], pd.DataFrame], tries: int = 3) -> pd.DataFrame | None:
    """One archive file parsed, None when it does not exist (HTTP 404). Other network errors are
    retried, the pause doubling each time, then raised as ``SourceError``."""
    for attempt in range(tries):
        try:
            return parse(_zip_text(key))
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return None
            if attempt == tries - 1:
                raise SourceError(f"archive {key}: {e}") from e
        except requests.RequestException as e:
            if attempt == tries - 1:
                raise SourceError(f"archive {key}: {e}") from e
        time.sleep(2**attempt)
    return None


def _api(call: Callable, params: dict):
    """A fapi call (network errors retried): None when the exchange rejects the symbol (some
    delisted contracts are no longer served; the store rebuilt them from the archive),
    ``SourceError`` when it still fails."""
    try:
        return with_retry(call, params)
    except ccxt.BadSymbol:
        return None
    except ccxt.BaseError as e:
        raise SourceError(f"{getattr(call, '__name__', 'fapi')} {params.get('symbol')}: {e}") from e


def _counted(fn: Callable, label: str, every: int = 50) -> Callable:
    """``fn`` that logs how many calls it has served, so a long verification shows progress."""
    calls = [0]

    def wrapper(*args):
        calls[0] += 1
        if calls[0] % every == 0:
            logger.info("verify: %d %s requests", calls[0], label)
        return fn(*args)

    return wrapper


def binance_sources(exchange: ccxt.Exchange) -> Sources:
    """The fapi endpoints (``exchange`` is a ccxt ``binanceusdm``) and the public archive."""

    def api_klines(symbol: str, interval: str, lo: pd.Timestamp, hi: pd.Timestamp):
        params = {"symbol": symbol, "interval": interval, "limit": 1500}
        params |= {"startTime": _ms(lo), "endTime": _ms(hi) - 1}
        rows = _api(exchange.fapiPublicGetKlines, params)
        return None if rows is None else klines_to_frame(rows, exchange.milliseconds())

    def api_funding(symbol: str, lo: pd.Timestamp, hi: pd.Timestamp):
        params = {"symbol": symbol, "startTime": _ms(lo), "endTime": _ms(hi) - 1, "limit": 1000}
        rows = _api(exchange.fapiPublicGetFundingRate, params)
        time.sleep(FUNDING_PAUSE)
        return None if rows is None else funding_to_frame(rows)

    def archive_klines(symbol: str, interval: str, month: str):
        key = f"{KLINES_PREFIX}{symbol}/{interval}/{symbol}-{interval}-{month}.zip"
        return _archive(key, parse_archive_klines)

    def archive_funding(symbol: str, month: str):
        return _archive(
            f"{FUNDING_PREFIX}{symbol}/{symbol}-fundingRate-{month}.zip", parse_archive_funding
        )

    def archive_metrics(symbol: str, day: str):
        return _archive(f"{METRICS_PREFIX}{symbol}/{symbol}-metrics-{day}.zip", parse_metrics_csv)

    def archive_months(symbol: str, interval: str) -> list[str]:
        try:
            keys = archive_zip_keys(f"{KLINES_PREFIX}{symbol}/{interval}/")
        except requests.RequestException as e:
            raise SourceError(f"archive listing {symbol}: {e}") from e
        return sorted(k[-11:-4] for k in keys)

    return Sources(
        api_klines=_counted(api_klines, "api klines"),
        archive_klines=_counted(archive_klines, "archive klines"),
        api_funding=_counted(api_funding, "api funding"),
        archive_funding=_counted(archive_funding, "archive funding"),
        archive_metrics=_counted(archive_metrics, "archive metrics"),
        archive_months=_counted(archive_months, "archive listings"),
    )


# ------------------------------------------------------------------ report


def _table(df: pd.DataFrame) -> str:
    return df.to_markdown() if len(df) else "_(không có)_"


def _by_year(issues: pd.DataFrame) -> pd.DataFrame:
    """Issue counts per kind x check x severity (rows) and year of the issue date (columns)."""
    if issues.empty:
        return pd.DataFrame()
    year = pd.to_datetime(issues["date"], utc=True).dt.year
    return issues.assign(year=year).pivot_table(
        index=["kind", "check", "severity"],
        columns="year",
        values="symbol",
        aggfunc="count",
        fill_value=0,
    )


def summary_text(
    store: Path,
    now: pd.Timestamp,
    symbols: pd.DataFrame,
    issues: pd.DataFrame,
    zombies: pd.DataFrame,
    samples: pd.DataFrame,
    listings: pd.DataFrame | None = None,
) -> str:
    count = issues["severity"].value_counts()
    files = symbols["kind"].value_counts().to_dict()
    daily = symbols[symbols["kind"] == "klines_1d"]
    dead = daily[pd.to_datetime(daily["last_traded"], utc=True) < now - pd.Timedelta(days=7)]
    zyear = pd.to_datetime(zombies["date"], utc=True).dt.year.value_counts().sort_index()
    by_year = ", ".join(f"{y}: {n}" for y, n in zyear.items()) or "không có"
    lines = [
        f"# Kiểm toán kho dữ liệu `{store.name}`",
        "",
        f"- Thời điểm: {now:%Y-%m-%d %H:%M} UTC. Đường dẫn: `{store}`.",
        f"- Số file đã quét: {files}.",
        "- Lỗi: " + ", ".join(f"{s} {int(count.get(s, 0))}" for s in SEVERITIES) + ".",
        (
            f"- Mã đã ngừng giao dịch (ngày cuối có giao dịch cách đây > 7 ngày): {len(dead)}; "
            f"trong đó có đuôi nến giả: {int((dead['zombie_rows'] > 0).sum())}."
        ),
        (
            f"- Ngày-thành viên của danh mục rơi vào ngày không có giao dịch: {len(zombies)} "
            f"({by_year})."
        ),
    ]
    if listings is not None:
        lines.append(_listing_line(listings, issues))
    lines += ["", "## Lỗi theo loại kiểm tra và năm", _table(_by_year(issues))]
    if len(samples):
        lines += ["", "## Đối chiếu với Binance (mẫu)", _verify_table(samples)]
    return "\n".join(lines) + "\n"


def _listing_line(listings: pd.DataFrame, issues: pd.DataFrame) -> str:
    t = listings.set_index("symbol")
    renames = [
        f"{old}→{new} ({int(t.at[new, 'rename_gap_days'])} ngày, tỷ lệ giá "
        f"{t.at[new, 'rename_price_ratio']:.3f})"
        for old, new in RENAMES.items()
        if new in t.index and pd.notna(t.at[new, "rename_gap_days"])
    ]
    reused = [f"{s} từ {d}" for s, d in REUSED.items() if s in t.index]
    relisted = [f"{s} từ {d}" for s, d in RELISTED.items() if s in t.index]
    suspects = int((issues["check"] == "rename_suspect").sum()) if len(issues) else 0
    return (
        f"- Mã đổi tên đã đánh dấu: {', '.join(renames) or 'không có trong kho'}; mã dùng lại "
        f"tên: {', '.join(reused) or 'không có trong kho'}; mã niêm yết lại (kho thiếu hợp đồng "
        f"cũ): {', '.join(relisted) or 'không có trong kho'}; cặp nghi đổi tên chưa đánh dấu: "
        f"{suspects} (`rename_suspect`, `listings.csv`)."
    )


def _verify_table(samples: pd.DataFrame) -> str:
    rows = []
    for kind, g in samples.groupby("kind", sort=False):
        row = {"kind": kind, "samples": len(g)}
        for src in ("api", "archive"):
            col = f"{src}_diff"
            if col in g:
                row[f"{src}_checked"] = int(g[col].notna().sum())
                row[f"{src}_with_diff"] = int((g[col] > 0).sum())
        rows.append(row)
    return pd.DataFrame(rows).set_index("kind").to_markdown()


def write_outputs(
    out: Path, store: Path, now: pd.Timestamp, tables: dict[str, pd.DataFrame], text: str
) -> Path:
    run = out / f"{store.name}-{now:%Y%m%d}"
    run.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.to_csv(run / f"{name}.csv", index=False)
    (run / "summary.md").write_text(text, encoding="utf-8")
    latest = out / "latest" / store.name
    if latest.exists():
        shutil.rmtree(latest)
    shutil.copytree(run, latest)
    return run


# ------------------------------------------------------------------ main


def _logging(path: Path | None) -> list[logging.Handler]:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(path, encoding="utf-8"))
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    for h in handlers:
        h.setFormatter(fmt)
        logger.addHandler(h)
    logger.setLevel(logging.INFO)
    return handlers


def run(args: argparse.Namespace) -> int:
    now = pd.Timestamp.now(tz="UTC")
    store = args.store.resolve()
    kinds = [k for k in KINDS if k in args.kinds]
    logger.info("audit of %s (%s), verify %d", store, ", ".join(kinds), args.verify)
    symbols, issues, daily = scan_store(store, kinds)
    zombies = (
        zombie_members(store, daily, args.top)
        if daily
        else pd.DataFrame(columns=["symbol", "date"])
    )
    listings = listing_table(store) if daily else None
    found_parts = [listing_issues(listings)] if listings is not None else []
    samples = pd.DataFrame()
    dates_all = args.dates_all and "klines_1d" in kinds
    if args.verify or dates_all:
        sources = binance_sources(ccxt.binanceusdm({"enableRateLimit": True}))
        runs = []
        if args.verify:
            vkinds = tuple(
                k
                for k in VERIFY_KINDS
                if k in kinds or (k == "dates" and "klines_1d" in kinds and not dates_all)
            )
            runs.append((args.verify, vkinds))
        if dates_all:  # every symbol, not a sample: the complete list of reused tickers
            runs.append((len(daily), ("dates",)))
        parts = []
        for n, vkinds in runs:
            got, found = verify_sample(store, sources, n, args.seed, vkinds, now)
            parts.append(got)
            found_parts.append(found)
        samples = pd.concat(parts, ignore_index=True)
    issues = pd.concat([p for p in (issues, *found_parts) if len(p)] or [issues], ignore_index=True)
    if args.known.exists():
        issues = apply_known(issues, pd.read_csv(args.known))
    tables = {"issues": issues, "symbols": symbols, "zombie_members": zombies}
    if listings is not None:
        tables["listings"] = listings
    if len(samples):
        tables["verify"] = samples
    text = summary_text(store, now, symbols, issues, zombies, samples, listings)
    path = write_outputs(args.out, store, now, tables, text)
    critical = int((issues["severity"] == "critical").sum())
    logger.info("report %s: %d issues, %d critical", path, len(issues), critical)
    return 1 if critical else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--store", type=Path, default=REPO / "user_data/research/pit")
    parser.add_argument("--kinds", nargs="+", default=list(KINDS), choices=KINDS)
    parser.add_argument("--out", type=Path, default=REPO / "user_data/reports/audit")
    parser.add_argument("--log", type=Path, default=REPO / "user_data/logs/audit.log")
    parser.add_argument("--top", type=int, default=20, help="universe size for the member check")
    parser.add_argument("--verify", type=int, default=0, help="sampled units per kind (0: offline)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--dates-all",
        action="store_true",
        help="listing months of every symbol against the archive (network), not a sample",
    )
    parser.add_argument(
        "--known",
        type=Path,
        default=REPO / "user_data/research/audit_known.csv",
        help="issues checked by hand (kind, symbol, check, date, reason): severity 'known'",
    )
    args = parser.parse_args(argv)
    handlers = _logging(args.log)
    try:
        return run(args)
    finally:
        for h in handlers:
            logger.removeHandler(h)
            h.close()


if __name__ == "__main__":
    raise SystemExit(main())
