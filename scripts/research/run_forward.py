"""
Forward-test runner. At every 4h close (+90 s) it updates the point-in-time store from public
Binance endpoints, asks the registered rules (``SPECS`` in ``user_data/research``) for setups,
writes new ones to the journal before any outcome exists, and scores the ones whose exit is
known. Daily: the trend setups and exits (00:00 UTC close) and a full store sync (01:30 UTC,
``download_pit_universe.py``). Closes missed while the machine slept are caught up and marked
``live_late``. Setups are not recorded when the rules' fingerprint differs from the registered
one. One instance at a time.

After each cycle the product layer (``mce.product``, configured by ``user_data/research/
product.json``) updates the risk book from the journal, writes the setup, close and weekly
messages to ``forward/messages.log``, checks operations (late cycles, recorder, reconciliation,
fingerprint) and, when ``telegram_send`` is on, delivers to Telegram (send only; token and chat
id in ``user_data/config.json`` ``telegram``). Its failures are logged and never stop the
journal.

    .venv/Scripts/python.exe scripts/research/run_forward.py            # until stopped
    .venv/Scripts/python.exe scripts/research/run_forward.py --once     # one cycle now
    .venv/Scripts/python.exe scripts/research/run_forward.py --status   # journal and book
    .venv/Scripts/python.exe scripts/research/run_forward.py --preview DIR  # product layer on a
        journal copy, no network ([--book-start ISO] [--now ISO])
    .venv/Scripts/python.exe scripts/research/run_forward.py --telegram-test  # one message

Windows: ``scripts/research/schedule_forward.ps1``. VPS: service ``forward`` in docker-compose.yml.
"""

import argparse
import importlib
import json
import logging
import sqlite3
import subprocess
import sys
import time
from dataclasses import replace
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import ModuleType

import ccxt
import pandas as pd
from filelock import FileLock, Timeout
from mce import journal, riskbook
from mce.archive import sync_funding, sync_klines
from mce.forward import make_setup
from mce.labels import funding_between
from mce.live import quote, sync_open_interest, with_retry
from mce.outbox import TelegramSender, credentials
from mce.pit import daily_members, read_store
from mce.product import Product, ProductConfig
from mce.universe import is_candidate


REPO = Path(__file__).resolve().parents[2]
RESEARCH = REPO / "user_data" / "research"
OUT = RESEARCH / "forward"
BAR, DAY = pd.Timedelta(hours=4), pd.Timedelta(days=1)
DECISION_DELAY = pd.Timedelta(seconds=90)
LIVE_WINDOW = pd.Timedelta(minutes=15)  # later than this after the close -> live_late
FULL_SYNC_AT = pd.Timedelta(hours=1, minutes=30)  # daily, after the archive publishes
WARMUP = pd.Timedelta(days=100)
SPECS = "forward_v1,forward_v2"  # registered rule modules, comma-separated
PRODUCT_CONFIG = RESEARCH / "product.json"
FREQTRADE_CONFIG = REPO / "user_data" / "config.json"  # telegram.token / chat_id
DOTENV = REPO / ".env"  # TELEGRAM_BOT_TOKEN, TELEGRAM_ADMIN_CHAT_IDS (git-ignored)
RECORDER = RESEARCH / "live"
logger = logging.getLogger("forward")


def load_spec(name: str) -> ModuleType:
    sys.path.insert(0, str(RESEARCH))
    return importlib.import_module(name)


def store_candles(pair: str, timeframe: str, end: pd.Timestamp) -> pd.DataFrame:
    """Closed candles of the store dated before ``end`` (for the image cards)."""
    k = read_store(RESEARCH / "pit", f"klines_{timeframe}", pair, end)
    cols = ["date", "open", "high", "low", "close"]
    return k[cols] if k is not None else pd.DataFrame(columns=cols)


def telegram_sender() -> TelegramSender | None:
    """Send-only client from the environment, the repo ``.env`` or the Freqtrade config."""
    found = credentials(FREQTRADE_CONFIG, dotenv=DOTENV)
    return TelegramSender(*found) if found else None


def load_product(
    conn: sqlite3.Connection,
    out: Path = OUT,
    live: bool = False,
    book_start: pd.Timestamp | None = None,
) -> Product | None:
    """The product layer on journal ``conn`` (book and outbox in ``out/product.sqlite``), or None
    without ``product.json``. ``live``: the runner's own, with the recorder checks and Telegram
    when the config turns it on."""
    if not PRODUCT_CONFIG.exists():
        logger.warning("no %s: product layer off", PRODUCT_CONFIG)
        return None
    cfg = ProductConfig.load(PRODUCT_CONFIG)
    if book_start is not None:
        cfg = replace(cfg, book_start=book_start)
    sender = telegram_sender() if live and cfg.telegram_send else None
    if live and cfg.telegram_send and sender is None:
        logger.warning("telegram_send is on but no token/chat_id: messages stay local")
    return Product(
        conn,
        out / "product.sqlite",
        cfg,
        out / "messages.log",
        sender=sender,
        recorder_dir=RECORDER if live else None,
        candles=store_candles,
    )


def spec_strategies(spec: ModuleType) -> set[str]:
    """Strategy ids a spec writes to the journal (forward_v1 predates ``STRATEGIES``)."""
    if hasattr(spec, "STRATEGIES"):
        return set(spec.STRATEGIES)
    return {h.id for h in spec.REGISTRY} | {spec.STRATEGY_TREND}


def load_specs(names: str) -> list[ModuleType]:
    """Rule modules by comma-separated name; a missing file is skipped with a warning (a
    machine without the newer spec keeps running the older ones)."""
    specs = []
    for name in names.split(","):
        if (RESEARCH / f"{name}.py").exists():
            specs.append(load_spec(name))
        else:
            logger.warning("no %s.py in %s: spec skipped", name, RESEARCH)
    if not specs or len({s.PIT for s in specs}) > 1:
        raise SystemExit(f"specs {names}: none found, or they read different stores")
    return specs


class Runner:
    def __init__(self, specs: list[ModuleType], exchange: ccxt.Exchange) -> None:
        self.specs = specs
        self.spec = specs[0]  # the 4h universe and the barrier fees come from the first spec
        self.pit: Path = self.spec.PIT
        self.ex = exchange
        self.conn = journal.connect(OUT / "journal.sqlite")
        self.fee = self.spec.POLICY.round_trip_fee
        self.product: Product | None = None
        # trend setups the rules closed without a price (the coin stopped trading)
        self.no_price: dict[int, pd.Timestamp] = {}

    # ------------------------------------------------------------ store

    def trading_candidates(self) -> list[str]:
        meta = json.loads((self.pit / "symbols.json").read_text(encoding="utf-8"))
        info = with_retry(self.ex.fapiPublicGetExchangeInfo)
        trading = {
            s["symbol"]
            for s in info["symbols"]
            if s.get("status") == "TRADING"
            and s.get("contractType") == "PERPETUAL"
            and is_candidate(s["symbol"], s.get("underlyingType"), s.get("underlyingSubType") or [])
        }
        return [s for s in meta["candidates"] if s in trading]

    def _last(self, kind: str, symbol: str) -> pd.Timestamp | None:
        path = self.pit / kind / f"{symbol}.parquet"
        if not path.exists():
            return None
        d = pd.read_parquet(path, columns=["date"])["date"]
        return d.max() if len(d) else None

    def sync(self, now: pd.Timestamp, decision: pd.Timestamp) -> list[str]:
        """Bring the store up to ``decision`` for the coins it needs; returns the members."""
        day = decision.floor("D") - DAY  # the daily ranking the decision uses
        stale = [
            s
            for s in self.trading_candidates()
            if (last := self._last("klines_1d", s)) is not None and last < day
        ]
        for s in stale:
            with_retry(sync_klines, self.ex, s, self.pit, 0, "1d")
        members = daily_members(self.pit, decision, self.spec.TOP_N)
        no_one = pd.Series(False, index=members.columns)
        today = members.loc[day] if day in members.index else no_one
        need = sorted(set(today[today].index) | set(journal.pending(self.conn)["pair"]))
        if any(self._last("klines_4h", s) is None for s in need):
            logger.info("new coins without history: full store sync first")
            self.full_sync()
        start = int((now - WARMUP).timestamp() * 1000)
        for s in need:
            with_retry(sync_klines, self.ex, s, self.pit, start, "4h")
            with_retry(sync_open_interest, self.ex, s, self.pit, now)
            with_retry(sync_funding, self.ex, s, self.pit, start)
        logger.info("store synced: %d daily candles, %d coins at 4h", len(stale), len(need))
        return list(today[today].index)

    def full_sync_due(self, now: pd.Timestamp) -> bool:
        """The daily full sync runs once per UTC day, after FULL_SYNC_AT (the day it last
        succeeded is kept in ``forward/last_full_sync``, so restarts do not repeat it)."""
        path = OUT / "last_full_sync"
        done = path.read_text(encoding="utf-8").strip() if path.exists() else ""
        return now - now.floor("D") >= FULL_SYNC_AT and done != f"{now:%Y-%m-%d}"

    def full_sync(self) -> None:
        cmd = [sys.executable, str(Path(__file__).with_name("download_pit_universe.py"))]
        cmd += ["--outdir", str(self.pit)]
        done = subprocess.run(
            cmd, cwd=REPO, capture_output=True, text=True, timeout=7200, check=False
        )
        logger.info("full store sync exit %s: %s", done.returncode, done.stdout.strip()[-300:])
        if done.returncode == 0:
            day = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d")
            (OUT / "last_full_sync").write_text(day, encoding="utf-8")

    # ------------------------------------------------------------ setups

    def _record(
        self, spec: ModuleType, row: pd.Series, decided: pd.Timestamp, now: pd.Timestamp, bar
    ) -> None:
        live = now - decided <= LIVE_WINDOW
        kw = {
            "strategy": row["strategy"],
            "fingerprint": spec.fingerprint(),
            "source": "live" if live else "live_late",
            "pair": row["pair"],
            "timeframe": row["timeframe"],
            "candle_date": row["candle_date"],
            "bar": bar,
            "side": int(row["side"]),
            "entry": float(row["entry"]),
            "atr": float(row["atr"]),
            "stop": float(row["stop"]),
            "target": None if pd.isna(row["target"]) else float(row["target"]),
            "max_bars": None if pd.isna(row["max_bars"]) else int(row["max_bars"]),
            "reason": row["reason"],
        }
        s = make_setup(spec.POLICY, **kw)
        if live:
            try:
                q = quote(self.ex, s.pair, s.side, s.notional)
                s = make_setup(spec.POLICY, **kw, quote=q)
            except ccxt.BaseError as e:
                logger.warning("quote %s failed: %r", s.pair, e)
        if journal.record_setup(self.conn, s):
            side = "long" if s.side > 0 else "short"
            logger.info(
                "setup recorded: %s %s %s %s (%s)", s.strategy, side, s.pair, s.status, s.source
            )

    def record_new(
        self, spec: ModuleType, rows: pd.DataFrame, bar: pd.Timedelta, now: pd.Timestamp
    ) -> int:
        if rows.empty:
            return 0
        known = journal.setups(self.conn)
        seen = set(zip(known["strategy"], known["pair"], known["candle_date"], strict=True))
        first = spec.LIVE_START - (DAY if bar == DAY else pd.Timedelta(0))
        n = 0
        for _, row in rows[rows["candle_date"] >= first].iterrows():
            key = (row["strategy"], row["pair"], pd.Timestamp(row["candle_date"]).isoformat())
            if key not in seen:
                self._record(spec, row, pd.Timestamp(row["candle_date"]) + bar, now, bar)
                n += 1
        return n

    # ------------------------------------------------------------ scoring

    def candles(self, pair: str, tf: str, after: str, n: int) -> pd.DataFrame:
        k = read_store(self.pit, f"klines_{tf}", pair, pd.Timestamp.now(tz="UTC"))
        if k is None:
            return pd.DataFrame(columns=["date", "high", "low", "close"])
        return k[k["date"] > pd.Timestamp(after)].head(n).reset_index(drop=True)

    def funding(self, pair: str, start: pd.Timestamp, end: pd.Timestamp) -> float:
        f = read_store(self.pit, "funding", pair, end + pd.Timedelta(minutes=1))
        if f is None or f.empty:
            return 0.0
        prints = f.set_index("date")["funding_rate"]
        return float(funding_between(prints, pd.Series([start]), pd.Series([end]))[0])

    def close_trend(self, trades: pd.DataFrame, strategies: set[str] | None = None) -> int:
        """Record the exits of pending trend setups (of ``strategies``, all when None) that the
        rules have closed."""
        pend = journal.pending(self.conn)
        pend = pend[pend["max_bars"].isna()]
        if strategies is not None:
            pend = pend[pend["strategy"].isin(strategies)]
        if pend.empty or trades.empty:
            return 0
        done = trades[trades["exit_date"].notna()].copy()
        done["key"] = [pd.Timestamp(d).isoformat() for d in done["candle_date"]]
        n = 0
        for s in pend.itertuples():
            hit = done[(done["pair"] == s.pair) & (done["key"] == s.candle_date)]
            if hit.empty:
                continue
            t = hit.iloc[0]
            exit_close = pd.Timestamp(t["exit_date"]) + DAY
            if pd.isna(t["exit_price"]):  # the coin stopped trading: no exit price, no outcome
                logger.info("trend setup %s %s: trading ended, no outcome", s.pair, s.candle_date)
                self.no_price[int(s.id)] = exit_close
                continue
            paid = self.funding(s.pair, pd.Timestamp(s.decided_at), exit_close)
            o = journal.outcome(
                s.side,
                s.entry,
                abs(s.entry - s.stop),
                "rule",
                exit_close,
                t["exit_price"],
                paid,
                self.fee,
            )
            n += journal.record_outcome(self.conn, int(s.id), o)
        return n

    # ------------------------------------------------------------ cycle

    def cycle(self, now: pd.Timestamp) -> None:
        decision = now.floor("4h")
        self.sync(now, decision)
        found, registered = [], True
        for spec in self.specs:
            ok = spec.registered_fingerprint() == spec.fingerprint()
            if not ok:
                registered = False
                logger.error(
                    "%s fingerprint %s is not registered: no setup recorded",
                    spec.__name__,
                    spec.fingerprint(),
                )
            rows4, trades = spec.setups_4h(self.pit, decision), spec.setups_1d(self.pit, decision)
            if ok:
                n4 = self.record_new(spec, rows4, BAR, now)
                n1 = self.record_new(spec, trades, DAY, now)
                logger.info(
                    "%s decision %s: %d new 4h setups, %d new 1d setups",
                    spec.__name__,
                    decision,
                    n4,
                    n1,
                )
            found.append((spec, rows4, trades))
        scored = journal.score_barriers(self.conn, self.candles, self.funding, BAR, self.fee)
        closed = sum(self.close_trend(t, spec_strategies(s)) for s, _, t in found)
        logger.info("scored %d barrier setups, closed %d trend setups", scored, closed)
        issues = sum(self.reconcile(s, pd.concat([r4, t], ignore_index=True)) for s, r4, t in found)
        self.product_step(now, decision, issues, registered)

    def product_step(
        self,
        now: pd.Timestamp,
        decision: pd.Timestamp | None = None,
        issues: int = 0,
        registered: bool = True,
    ) -> None:
        """The product layer after the journal is up to date: book and messages, operational
        checks, delivery. A failure is logged, never raised."""
        if self.product is None:
            return
        try:
            done = self.product.step(now, self.no_price)
            alerts = self.product.checks(now, decision or now.floor("4h"), issues, registered)
            sent = self.product.flush(now)
            logger.info("product: %s, %d alerts, delivery %s", done, alerts, sent)
        except Exception:
            logger.exception("product step failed")

    def cycle_failed(self, now: pd.Timestamp, error: BaseException) -> None:
        """Alert for a failed cycle (best effort: the product layer may be what is down)."""
        if self.product is None:
            return
        try:
            self.product.cycle_failed(now, error)
            self.product.flush(now)
        except Exception:
            logger.exception("cycle failure alert failed")

    def reconcile(self, spec: ModuleType, replay: pd.DataFrame) -> int:
        """Every journal setup of ``spec`` against its rules replayed on the store as it is now;
        returns the number of disagreements."""
        live = journal.setups(self.conn)
        live = live[live["strategy"].isin(spec_strategies(spec))]
        if live.empty or replay.empty:
            return 0
        first = {"4h": spec.LIVE_START, "1d": spec.LIVE_START - DAY}
        keep = [
            pd.Timestamp(d) >= first[tf]
            for d, tf in zip(replay["candle_date"], replay["timeframe"], strict=True)
        ]
        replay = replay[keep].copy()
        replay["candle_date"] = [pd.Timestamp(d).isoformat() for d in replay["candle_date"]]
        issues = journal.reconcile(live, replay)
        if len(issues):
            logger.error("live vs replay: %d disagreements\n%s", len(issues), issues.to_string())
            issues.to_csv(OUT / "reconcile_issues.csv", index=False)
        else:
            logger.info("live vs replay: %d setups agree", len(live))
        return len(issues)


def status() -> None:
    conn = journal.connect(OUT / "journal.sqlite")
    s, sc = journal.setups(conn), journal.scored(conn)
    if s.empty:
        print("journal empty")
        return
    print(s.groupby(["strategy", "source", "status"]).size().to_string())
    if len(sc):
        g = sc.groupby("strategy")["net_r"]
        print(pd.DataFrame({"n": g.size(), "mean_net_r": g.mean(), "sum_net_r": g.sum()}))
    if (OUT / "product.sqlite").exists() and (product := load_product(conn)) is not None:
        print(product.status())


def copy_journal(dest: Path) -> sqlite3.Connection:
    """Consistent copy of the live journal (sqlite backup of a read-only connection)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(f"file:{(OUT / 'journal.sqlite').as_posix()}?mode=ro", uri=True)
    copy = sqlite3.connect(dest)
    src.backup(copy)
    src.close()
    return copy


def preview(out: Path, book_start: pd.Timestamp, now: pd.Timestamp) -> None:
    """The product layer on a copy of the journal, without network or Telegram: a fresh book,
    outbox and ``messages.log`` in ``out``; prints the messages and the book."""
    for name in ("product.sqlite", "messages.log"):
        (out / name).unlink(missing_ok=True)
    conn = copy_journal(out / "journal.sqlite")
    product = load_product(conn, out, book_start=book_start) or Product(
        conn,
        out / "product.sqlite",
        ProductConfig(riskbook.RiskPolicy(), book_start, {}),
        out / "messages.log",
    )
    print(product.step(now))
    log = out / "messages.log"
    print(log.read_text(encoding="utf-8") if log.exists() else "no message")
    print(product.status())


def telegram_test() -> None:
    """One message to the configured chat, whatever ``telegram_send`` says."""
    sender = telegram_sender()
    if sender is None:
        raise SystemExit(f"no Telegram token / chat id in the environment, {DOTENV} or the config")
    now = pd.Timestamp.now(tz="UTC")
    sender.send(
        f"THỬ NGHIỆM · kiểm tra kết nối bot tín hiệu lúc {now:%Y-%m-%d %H:%M} UTC. Bot chỉ gửi "
        "tin (setup, đóng lệnh, tổng kết tuần, cảnh báo vận hành), không nhận lệnh điều khiển."
    )
    print(f"sent to chat ...{sender.chat_id[-3:]}")


def wait_until(t: pd.Timestamp) -> None:
    while (left := (t - pd.Timestamp.now(tz="UTC")).total_seconds()) > 0:
        time.sleep(min(left, 60))  # short naps: a wake-up from machine sleep is noticed quickly


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument(
        "--spec", default=SPECS, help="rules modules in user_data/research, comma-separated"
    )
    parser.add_argument("--once", action="store_true", help="run one cycle now and exit")
    parser.add_argument("--status", action="store_true", help="print the journal and the book")
    parser.add_argument("--log", type=Path, default=REPO / "user_data" / "logs" / "forward.log")
    parser.add_argument("--preview", type=Path, help="run the product layer on a journal copy")
    parser.add_argument("--book-start", help="preview: first decision time of the book (ISO)")
    parser.add_argument("--now", help="preview: the time it runs at (ISO, default now)")
    parser.add_argument("--telegram-test", action="store_true", help="send one test message")
    args = parser.parse_args()
    if args.status:
        status()
        return
    if args.telegram_test:
        telegram_test()
        return
    if args.preview:
        spec = load_specs(args.spec)[0]
        start = pd.Timestamp(args.book_start or spec.LIVE_START)
        now = pd.Timestamp(args.now) if args.now else pd.Timestamp.now(tz="UTC")
        preview(args.preview, start, now)
        return
    args.log.parent.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        RotatingFileHandler(args.log, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    ]
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers
    )
    OUT.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(OUT / ".runner.lock"))
    try:
        lock.acquire(timeout=0)
    except Timeout:
        logger.info("another runner holds %s, exiting", lock.lock_file)
        return
    try:
        runner = Runner(
            load_specs(args.spec), ccxt.binanceusdm({"enableRateLimit": True, "timeout": 30_000})
        )
        runner.product = load_product(runner.conn, live=True)
        while True:
            now = pd.Timestamp.now(tz="UTC")
            try:
                if runner.full_sync_due(now):
                    runner.full_sync()
                runner.cycle(now)
            except Exception as e:  # network, exchange or data hiccup: retry at the next close
                logger.exception("cycle failed")
                runner.cycle_failed(now, e)
                if args.once:
                    raise
            if args.once:
                return
            nxt = now.floor("4h") + BAR + DECISION_DELAY
            if runner.full_sync_due(now.floor("D") + FULL_SYNC_AT):
                nxt = min(nxt, now.floor("D") + FULL_SYNC_AT)
            wait_until(max(nxt, now + pd.Timedelta(seconds=30)))
    finally:
        lock.release()


if __name__ == "__main__":
    main()
