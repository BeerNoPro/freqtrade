"""
Recorder for Binance data the public archive (data.binance.vision) does not keep: liquidation
orders, best bid/ask, mark / index / predicted funding, order book snapshots, options marks
(IV, greeks), options tickers and open interest, and daily exchange metadata. Archived data
(klines, trades, funding, OI and long/short metrics, aggregated book depth) is downloaded
instead (``mce.archive``).

Storage (UTC): append-only JSON lines ``<outdir>/<stream>/<YYYY-MM-DD>/part-<HH>.jsonl``, so a
crash loses at most a line in flight; past days are compacted into
``<outdir>/<stream>/<YYYY-MM-DD>.parquet``. Interruptions (lost connection, failed poll) are
appended to ``<outdir>/_gaps.jsonl`` so studies know which periods are missing.
Public endpoints only (no API key).
"""

import asyncio
import gzip
import json
import logging
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

import aiohttp
import pandas as pd
import websockets

from mce.universe import is_candidate


logger = logging.getLogger(__name__)

FAPI = "https://fapi.binance.com"
EAPI = "https://eapi.binance.com"
FORCE_ORDER_WS = "wss://fstream.binance.com/market/ws/!forceOrder@arr"
EVERY = {  # seconds between snapshots
    "book_ticker": 60,
    "premium_index": 300,
    "depth": 300,
    "options_mark": 900,
    "options_ticker": 3600,
    "options_oi": 3600,
    "housekeeping": 3600,
    "heartbeat": 60,
}
POLL_OFFSET = 2.0  # seconds after the boundary, so exchange snapshots of the minute are settled
TOP_BOOK, TOP_DEPTH, DEPTH_LEVELS = 100, 20, 100
TIME_COLUMNS = ("received", "taken")
FORCE_ORDER_FIELDS = {
    "s": "symbol",
    "S": "side",
    "o": "order_type",
    "f": "time_in_force",
    "q": "qty",
    "p": "price",
    "ap": "avg_price",
    "X": "status",
    "l": "last_qty",
    "z": "filled_qty",
    "T": "trade_time",
}
META = {
    "futures_exchange_info": f"{FAPI}/fapi/v1/exchangeInfo",
    "futures_funding_info": f"{FAPI}/fapi/v1/fundingInfo",
    "futures_ticker_24h": f"{FAPI}/fapi/v1/ticker/24hr",
    "options_exchange_info": f"{EAPI}/eapi/v1/exchangeInfo",
}


def now_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------- rows


def force_order_row(msg: dict, received_ms: int) -> dict:
    """One liquidation order message -> flat row. Unknown order fields are kept as ``o_<key>``."""
    row = {"received": received_ms, "event_time": msg.get("E")}
    for key, value in msg.get("o", {}).items():
        row[FORCE_ORDER_FIELDS.get(key, f"o_{key}")] = value
    return row


def snapshot_rows(payload: list[dict], taken_ms: int, symbols: Iterable[str] | None = None):
    """REST list payload -> rows stamped with the snapshot time, optionally only ``symbols``."""
    keep = set(symbols) if symbols is not None else None
    return [
        {"taken": taken_ms, **item}
        for item in payload
        if keep is None or item.get("symbol") in keep
    ]


def depth_rows(symbol: str, payload: dict, taken_ms: int) -> list[dict]:
    """Order book snapshot -> one row per price level (level 0 = best)."""
    head = {
        "taken": taken_ms,
        "symbol": symbol,
        "last_update_id": payload.get("lastUpdateId"),
        "event_time": payload.get("E"),
        "transaction_time": payload.get("T"),
    }
    return [
        {**head, "side": side, "level": level, "price": price, "qty": qty}
        for side, key in (("bid", "bids"), ("ask", "asks"))
        for level, (price, qty) in enumerate(payload.get(key, []))
    ]


def top_symbols(tickers_24h: list[dict], exchange_info: dict, n: int) -> list[str]:
    """The ``n`` crypto USDT perpetuals in trading with the highest 24h quote volume."""
    trading = {
        s["symbol"]
        for s in exchange_info.get("symbols", [])
        if s.get("status") == "TRADING"
        and s.get("contractType") == "PERPETUAL"
        and is_candidate(s["symbol"], s.get("underlyingType"), s.get("underlyingSubType") or [])
    }
    ranked = sorted(
        (t for t in tickers_24h if t.get("symbol") in trading),
        key=lambda t: float(t.get("quoteVolume") or 0.0),
        reverse=True,
    )
    return [t["symbol"] for t in ranked[:n]]


def option_series(options_info: dict) -> list[tuple[str, str]]:
    """(underlying asset, expiration YYMMDD) pairs listed in the options exchange info."""
    pairs = set()
    for s in options_info.get("optionSymbols", []):
        parts = s.get("symbol", "").split("-")
        if len(parts) == 4:
            pairs.add((parts[0], parts[1]))
    return sorted(pairs)


def next_run(now_s: float, every_s: float, offset_s: float = POLL_OFFSET) -> float:
    """First time strictly after ``now_s`` of the form k * every_s + offset_s."""
    return ((now_s - offset_s) // every_s + 1) * every_s + offset_s


# ---------------------------------------------------------------- storage


class PartWriter:
    """Append rows as JSON lines to the hourly part file of their timestamp (UTC)."""

    def __init__(self, outdir: Path) -> None:
        self.outdir = outdir

    def append(self, stream: str, rows: list[dict], when_ms: int) -> int:
        if not rows:
            return 0
        t = pd.Timestamp(when_ms, unit="ms", tz="UTC")
        path = self.outdir / stream / f"{t:%Y-%m-%d}" / f"part-{t:%H}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        text = "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows)
        with path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(text)
        return len(rows)

    def gap(self, stream: str, start_ms: int, end_ms: int, reason: str) -> None:
        self.outdir.mkdir(parents=True, exist_ok=True)
        row = {"stream": stream, "start": start_ms, "end": end_ms, "reason": reason[:300]}
        with (self.outdir / "_gaps.jsonl").open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(row) + "\n")


def _read_parts(parts: list[Path]) -> tuple[list[dict], int]:
    rows, bad = [], 0
    for part in parts:
        for line in part.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:  # a line cut by a crash
                bad += 1
    return rows, bad


def _typed(df: pd.DataFrame) -> pd.DataFrame:
    """Numeric strings -> numbers, column by column, only where nothing would be lost."""
    for col in df.columns:
        if not (pd.api.types.is_object_dtype(df[col]) or pd.api.types.is_string_dtype(df[col])):
            continue
        num = pd.to_numeric(df[col], errors="coerce")
        if num.notna().sum() == df[col].notna().sum():
            df[col] = num
    return df


def compact_day(stream_dir: Path, day: str) -> int:
    """Merge the part files of ``day`` into ``<day>.parquet`` and delete them. Returns rows."""
    day_dir = stream_dir / day
    parts = sorted(day_dir.glob("part-*.jsonl"))
    if not parts:
        return 0
    rows, bad = _read_parts(parts)
    if bad:
        logger.warning("%s %s: skipped %d unreadable lines", stream_dir.name, day, bad)
    df = _typed(pd.DataFrame(rows))
    out = stream_dir / f"{day}.parquet"
    if out.exists():
        df = pd.concat([pd.read_parquet(out), df], ignore_index=True)
    sort = [c for c in TIME_COLUMNS if c in df.columns]
    if sort:
        df = df.sort_values(sort, kind="stable").reset_index(drop=True)
    tmp = out.with_suffix(".tmp")
    df.to_parquet(tmp, compression="zstd", index=False)
    if len(pd.read_parquet(tmp)) != len(df):
        raise OSError(f"compaction check failed for {tmp}")
    tmp.replace(out)
    for part in parts:
        part.unlink()
    if not any(day_dir.iterdir()):
        day_dir.rmdir()
    return len(df)


def beat(path: Path, writer: PartWriter, now: int, max_silence_ms: int) -> None:
    """Store the heartbeat time; a longer silence than ``max_silence_ms`` since the previous
    beat (recorder stopped, machine asleep) is logged as a gap of every stream."""
    if path.exists():
        last = int(path.read_text(encoding="utf-8") or 0)
        if now - last > max_silence_ms:
            writer.gap("all", last, now, "recorder not running or machine asleep")
    path.write_text(str(now), encoding="utf-8")


def save_meta(folder: Path, meta: dict[str, object]) -> None:
    """Keep the first copy of each metadata document of the day, gzip-compressed JSON."""
    folder.mkdir(parents=True, exist_ok=True)
    for name, payload in meta.items():
        path = folder / f"{name}.json.gz"
        if not path.exists():
            path.write_bytes(gzip.compress(json.dumps(payload).encode()))


def compact_all(outdir: Path, before_day: str) -> dict[str, int]:
    """Compact every stream day older than ``before_day`` (YYYY-MM-DD). Returns rows per stream."""
    done: dict[str, int] = {}
    if not outdir.exists():
        return done
    for stream_dir in sorted(p for p in outdir.iterdir() if p.is_dir() and p.name != "meta"):
        for day_dir in sorted(p for p in stream_dir.iterdir() if p.is_dir()):
            if day_dir.name < before_day:
                done[stream_dir.name] = done.get(stream_dir.name, 0) + compact_day(
                    stream_dir, day_dir.name
                )
    return done


# ---------------------------------------------------------------- coverage

DAY_MS = 86_400_000
SNAPSHOT_STREAMS = tuple(k for k in EVERY if k not in ("housekeeping", "heartbeat"))


def _day_times(stream_dir: Path, day: str) -> tuple[pd.Series, str]:
    """Row times (ms) of one stream day, from its parquet and/or part files, and its state:
    ``compacted`` (parquet only), ``open`` (part files only) or ``mixed``."""
    col = "taken" if stream_dir.name in SNAPSHOT_STREAMS else "received"
    parts, out = [], stream_dir / f"{day}.parquet"
    if out.exists():
        parts.append(pd.read_parquet(out, columns=[col])[col])
    day_dir = stream_dir / day
    files = sorted(day_dir.glob("part-*.jsonl")) if day_dir.is_dir() else []
    if files:
        rows, _ = _read_parts(files)
        parts.append(pd.Series([r.get(col) for r in rows], dtype="float64"))
    state = "mixed" if out.exists() and files else "compacted" if out.exists() else "open"
    times = pd.concat(parts, ignore_index=True) if parts else pd.Series(dtype="float64")
    return times.dropna().astype("int64"), state


def gap_minutes(outdir: Path) -> dict[tuple[str, str], float]:
    """Minutes of logged gaps per (stream, UTC day); stream ``all`` covers every stream."""
    path = outdir / "_gaps.jsonl"
    out: dict[tuple[str, str], float] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        g = json.loads(line)
        start, end = int(g["start"]), int(g["end"])
        while start < end:
            day_end = (start // DAY_MS + 1) * DAY_MS
            stop = min(end, day_end)
            day = pd.Timestamp(start, unit="ms", tz="UTC").strftime("%Y-%m-%d")
            out[(g["stream"], day)] = out.get((g["stream"], day), 0.0) + (stop - start) / 60_000
            start = stop
    return out


def coverage(outdir: Path, at_ms: int | None = None) -> pd.DataFrame:
    """One row per stream and UTC day: rows, distinct snapshots against the clock grid
    (``EVERY``), hours with data, logged gap minutes, size and compaction state. ``coverage`` is
    the share of the day (up to ``at_ms`` today) that was recorded: snapshots / expected for
    snapshot streams, 1 - logged gap minutes / day for liquidations (an event stream)."""
    at_ms = at_ms if at_ms is not None else now_ms()
    today = pd.Timestamp(at_ms, unit="ms", tz="UTC").strftime("%Y-%m-%d")
    gaps = gap_minutes(outdir)
    rows = []
    streams = sorted(p for p in outdir.iterdir() if p.is_dir() and p.name != "meta")
    for stream_dir in streams:
        days = {p.stem if p.suffix == ".parquet" else p.name for p in stream_dir.iterdir()}
        for i, day in enumerate(sorted(d for d in days if len(d) == 10)):
            times, state = _day_times(stream_dir, day)
            start = int(pd.Timestamp(day, tz="UTC").timestamp() * 1000)
            span = min(at_ms, start + DAY_MS) - start  # today: the part of the day elapsed
            gap = gaps.get(("all", day), 0.0) + gaps.get((stream_dir.name, day), 0.0)
            if stream_dir.name in SNAPSHOT_STREAMS:
                every = EVERY[stream_dir.name] * 1000
                snaps = float(times.nunique())
                cov = snaps / max(1, span // every)
            else:
                snaps = float("nan")
                # before the first row ever recorded the recorder was not running (no gap logged)
                before = (times.min() - start) / 60_000 if i == 0 and len(times) else 0.0
                cov = max(0.0, 1 - (gap + before) / (span / 60_000))
            size = sum(
                f.stat().st_size
                for f in [stream_dir / f"{day}.parquet", *(stream_dir / day).glob("*")]
                if f.is_file()
            )
            rows.append(
                {
                    "stream": stream_dir.name,
                    "day": day,
                    "rows": len(times),
                    "snapshots": snaps,
                    "coverage": min(1.0, cov),
                    "hours": int(((times - start) // 3_600_000).nunique()),
                    "gap_min": round(gap, 1),
                    "size_mb": round(size / 1e6, 2),
                    "state": state,
                    "late_compaction": day < today and state != "compacted",
                }
            )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- recorder


@dataclass
class Lists:
    book: list[str] = field(default_factory=list)  # symbols for best bid/ask
    depth: list[str] = field(default_factory=list)  # symbols for order book snapshots
    series: list[tuple[str, str]] = field(default_factory=list)  # option (asset, expiry)


async def get_json(session: aiohttp.ClientSession, url: str, params: dict | None = None):
    async with session.get(url, params=params) as resp:
        resp.raise_for_status()
        return await resp.json(content_type=None)


async def record_ws(
    url: str, stream: str, writer: PartWriter, backoff: tuple[float, float] = (1.0, 60.0)
) -> None:
    """Record every WebSocket message forever; reconnect with exponential backoff and log the
    time without a connection as a gap. Binance closes each connection after 24 hours."""
    delay, down_since = backoff[0], None
    while True:
        try:
            async with websockets.connect(url, max_size=2**22) as ws:
                if down_since is not None:
                    writer.gap(stream, down_since, now_ms(), "reconnected")
                    down_since = None
                delay = backoff[0]
                async for raw in ws:
                    received = now_ms()
                    msg = json.loads(raw)
                    items = msg if isinstance(msg, list) else [msg]
                    writer.append(stream, [force_order_row(m, received) for m in items], received)
        except (OSError, websockets.WebSocketException, json.JSONDecodeError) as e:
            logger.warning("%s: connection lost: %r", stream, e)
        if down_since is None:
            down_since = now_ms()
        await asyncio.sleep(delay)
        delay = min(delay * 2, backoff[1])


async def sleep_until(t: float, step: float = 30.0) -> None:
    """Sleep until wall-clock time ``t`` in steps of at most ``step`` seconds. asyncio timers
    follow a monotonic clock that stops while Windows sleeps, so one long sleep would end as
    late as the machine slept; short steps notice the wake-up within ``step``."""
    while (left := t - time.time()) > 0:  # noqa: ASYNC110 - polling the wall clock is the point
        await asyncio.sleep(min(left, step))


async def poll(
    name: str, job: Callable[[int], Awaitable[int]], writer: PartWriter, run_now: bool = True
) -> None:
    """Run ``job(taken_ms)`` once now (unless ``run_now`` is False), then on the wall-clock grid
    of EVERY[name]; failures become gaps."""
    every = EVERY[name]
    while True:
        if not run_now:
            await sleep_until(next_run(time.time(), every))
        run_now = False
        taken = now_ms()
        try:
            await job(taken)
        except Exception as e:  # one failing stream must not stop the others
            logger.warning("%s failed: %r", name, e)
            writer.gap(name, taken, now_ms(), repr(e))


class Recorder:
    def __init__(self, outdir: Path, session: aiohttp.ClientSession) -> None:
        self.outdir = outdir
        self.session = session
        self.writer = PartWriter(outdir)
        self.lists = Lists()

    async def _rows(self, stream: str, url: str, taken: int, symbols=None) -> int:
        payload = await get_json(self.session, url)
        return self.writer.append(stream, snapshot_rows(payload, taken, symbols), taken)

    async def book_ticker(self, taken: int) -> int:
        url = f"{FAPI}/fapi/v1/ticker/bookTicker"
        return await self._rows("book_ticker", url, taken, self.lists.book)

    async def premium_index(self, taken: int) -> int:
        return await self._rows("premium_index", f"{FAPI}/fapi/v1/premiumIndex", taken)

    async def options_mark(self, taken: int) -> int:
        return await self._rows("options_mark", f"{EAPI}/eapi/v1/mark", taken)

    async def options_ticker(self, taken: int) -> int:
        return await self._rows("options_ticker", f"{EAPI}/eapi/v1/ticker", taken)

    async def depth(self, taken: int) -> int:
        rows = []
        for symbol in self.lists.depth:
            params = {"symbol": symbol, "limit": DEPTH_LEVELS}
            payload = await get_json(self.session, f"{FAPI}/fapi/v1/depth", params)
            rows += depth_rows(symbol, payload, taken)
        return self.writer.append("depth", rows, taken)

    async def options_oi(self, taken: int) -> int:
        rows = []
        for asset, expiry in self.lists.series:
            params = {"underlyingAsset": asset, "expiration": expiry}
            payload = await get_json(self.session, f"{EAPI}/eapi/v1/openInterest", params)
            for row in snapshot_rows(payload, taken):
                rows.append({**row, "underlying_asset": asset, "expiration": expiry})
        return self.writer.append("options_oi", rows, taken)

    async def heartbeat(self, taken: int) -> int:
        beat(self.outdir / ".heartbeat", self.writer, taken, 2 * EVERY["heartbeat"] * 1000)
        return 0

    async def housekeeping(self, taken: int) -> int:
        """Save today's exchange metadata once, refresh the symbol lists, compact past days."""
        day = pd.Timestamp(taken, unit="ms", tz="UTC").strftime("%Y-%m-%d")
        meta = {name: await get_json(self.session, url) for name, url in META.items()}
        await asyncio.to_thread(save_meta, self.outdir / "meta" / day, meta)
        tickers, info = meta["futures_ticker_24h"], meta["futures_exchange_info"]
        self.lists = Lists(
            book=top_symbols(tickers, info, TOP_BOOK),
            depth=top_symbols(tickers, info, TOP_DEPTH),
            series=option_series(meta["options_exchange_info"]),
        )
        done = await asyncio.to_thread(compact_all, self.outdir, day)
        if done:
            logger.info("compacted %s", done)
        return len(meta)

    async def run(self) -> None:
        await self.housekeeping(now_ms())  # lists must exist before the first snapshot
        logger.info(
            "recording: %d book symbols, %d depth symbols, %d option series",
            len(self.lists.book),
            len(self.lists.depth),
            len(self.lists.series),
        )
        jobs = {
            "book_ticker": self.book_ticker,
            "premium_index": self.premium_index,
            "depth": self.depth,
            "options_mark": self.options_mark,
            "options_ticker": self.options_ticker,
            "options_oi": self.options_oi,
            "heartbeat": self.heartbeat,
        }
        tasks = [poll(name, job, self.writer) for name, job in jobs.items()]
        tasks.append(poll("housekeeping", self.housekeeping, self.writer, run_now=False))
        tasks.append(record_ws(FORCE_ORDER_WS, "liquidations", self.writer))
        await asyncio.gather(*tasks)
