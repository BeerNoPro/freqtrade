"""
Forward-test runner. At every 4h close (+90 s) it updates the point-in-time store from public
Binance endpoints, asks the registered rules (``user_data/research/forward_v1.py``) for setups,
writes new ones to the journal before any outcome exists, and scores the ones whose exit is
known. Daily: the trend setups and exits (00:00 UTC close) and a full store sync (01:30 UTC,
``download_pit_universe.py``). Closes missed while the machine slept are caught up and marked
``live_late``. Setups are not recorded when the rules' fingerprint differs from the registered
one. Telegram is off: messages go to ``forward/messages.log`` with the label of an untested
experiment. One instance at a time.

    .venv/Scripts/python.exe scripts/research/run_forward.py            # until stopped
    .venv/Scripts/python.exe scripts/research/run_forward.py --once     # one cycle now
    .venv/Scripts/python.exe scripts/research/run_forward.py --status   # journal summary

Windows: ``scripts/research/schedule_forward.ps1``. VPS: service ``forward`` in docker-compose.yml.
"""

import argparse
import importlib
import json
import logging
import subprocess
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import ModuleType

import ccxt
import pandas as pd
from filelock import FileLock, Timeout
from mce import journal
from mce.archive import sync_funding, sync_klines
from mce.forward import make_setup
from mce.labels import funding_between
from mce.live import quote, sync_open_interest, with_retry
from mce.pit import daily_members, read_store
from mce.universe import is_candidate


REPO = Path(__file__).resolve().parents[2]
RESEARCH = REPO / "user_data" / "research"
OUT = RESEARCH / "forward"
BAR, DAY = pd.Timedelta(hours=4), pd.Timedelta(days=1)
DECISION_DELAY = pd.Timedelta(seconds=90)
LIVE_WINDOW = pd.Timedelta(minutes=15)  # later than this after the close -> live_late
FULL_SYNC_AT = pd.Timedelta(hours=1, minutes=30)  # daily, after the archive publishes
WARMUP = pd.Timedelta(days=100)
LABEL = "THỬ NGHIỆM - chưa kiểm định, không giao dịch theo"
logger = logging.getLogger("forward")


def load_spec(name: str) -> ModuleType:
    sys.path.insert(0, str(RESEARCH))
    return importlib.import_module(name)


def message(s: journal.Setup) -> str:
    """Product message of a setup (Telegram format, sent only once the user approves)."""
    side = "LONG" if s.side > 0 else "SHORT"
    pct = (s.stop / s.entry - 1) * 100
    exit_rule = (
        f"TP {s.target:.6g} ({(s.target / s.entry - 1) * 100:+.1f}%), thoát sau "
        f"{int(s.max_bars) * 4}h nếu chưa chạm"
        if s.target is not None and s.max_bars is not None
        else "không TP, thoát theo trailing stop của mô hình"
    )
    skipped = " [BỎ QUA: chi phí quá lớn so với 1R]" if s.status == "skipped_cost" else ""
    return "\n".join(
        [
            f"{LABEL}{skipped}",
            f"{side} {s.pair} ({s.strategy}) - nến {s.timeframe} đóng {s.decided_at}",
            f"Vào: {s.entry:.6g} (giá đóng nến) · SL {s.stop:.6g} ({pct:+.1f}%) · {exit_rule}",
            (
                f"Khối lượng: {s.qty:.6g} ({s.notional:,.0f} USDT) cho rủi ro 1% vốn mẫu · "
                f"đòn bẩy tối đa {s.leverage_max}x isolated"
            ),
            f"Chi phí ước tính {s.cost_r:.2f}R · Kỳ vọng: chưa kiểm định",
            f"Lý do: {s.reason}",
        ]
    )


class Runner:
    def __init__(self, spec: ModuleType, exchange: ccxt.Exchange) -> None:
        self.spec = spec
        self.pit: Path = spec.PIT
        self.ex = exchange
        self.conn = journal.connect(OUT / "journal.sqlite")
        self.fee = spec.POLICY.round_trip_fee

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

    def _record(self, row: pd.Series, decided: pd.Timestamp, now: pd.Timestamp, bar) -> None:
        live = now - decided <= LIVE_WINDOW
        kw = {
            "strategy": row["strategy"],
            "fingerprint": self.spec.fingerprint(),
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
        s = make_setup(self.spec.POLICY, **kw)
        if live:
            try:
                q = quote(self.ex, s.pair, s.side, s.notional)
                s = make_setup(self.spec.POLICY, **kw, quote=q)
            except ccxt.BaseError as e:
                logger.warning("quote %s failed: %r", s.pair, e)
        if journal.record_setup(self.conn, s):
            text = message(s)
            logger.info("setup recorded:\n%s", text)
            with (OUT / "messages.log").open("a", encoding="utf-8", newline="\n") as f:
                f.write(f"[{now.isoformat(timespec='seconds')}] {s.source}\n{text}\n\n")

    def record_new(self, rows: pd.DataFrame, bar: pd.Timedelta, now: pd.Timestamp) -> int:
        if rows.empty:
            return 0
        known = journal.setups(self.conn)
        seen = set(zip(known["strategy"], known["pair"], known["candle_date"], strict=True))
        first = self.spec.LIVE_START - (DAY if bar == DAY else pd.Timedelta(0))
        n = 0
        for _, row in rows[rows["candle_date"] >= first].iterrows():
            key = (row["strategy"], row["pair"], pd.Timestamp(row["candle_date"]).isoformat())
            if key not in seen:
                self._record(row, pd.Timestamp(row["candle_date"]) + bar, now, bar)
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

    def close_trend(self, trades: pd.DataFrame) -> int:
        """Record the exits of pending trend setups that the rules have closed."""
        pend = journal.pending(self.conn)
        pend = pend[pend["max_bars"].isna()]
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
        registered = self.spec.registered_fingerprint() == self.spec.fingerprint()
        if not registered:
            logger.error(
                "rules fingerprint %s is not registered: no setup recorded", self.spec.fingerprint()
            )
        self.sync(now, decision)
        rows4 = self.spec.setups_4h(self.pit, decision)
        trades = self.spec.setups_1d(self.pit, decision)
        if registered:
            n4, n1 = self.record_new(rows4, BAR, now), self.record_new(trades, DAY, now)
            logger.info("decision %s: %d new 4h setups, %d new 1d setups", decision, n4, n1)
        scored = journal.score_barriers(self.conn, self.candles, self.funding, BAR, self.fee)
        closed = self.close_trend(trades)
        logger.info("scored %d barrier setups, closed %d trend setups", scored, closed)
        self.reconcile(pd.concat([rows4, trades], ignore_index=True))

    def reconcile(self, replay: pd.DataFrame) -> None:
        """Every journal setup against the same rules replayed on the store as it is now."""
        live = journal.setups(self.conn)
        if live.empty or replay.empty:
            return
        first = {"4h": self.spec.LIVE_START, "1d": self.spec.LIVE_START - DAY}
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


def wait_until(t: pd.Timestamp) -> None:
    while (left := (t - pd.Timestamp.now(tz="UTC")).total_seconds()) > 0:
        time.sleep(min(left, 60))  # short naps: a wake-up from machine sleep is noticed quickly


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--spec", default="forward_v1", help="rules module in user_data/research")
    parser.add_argument("--once", action="store_true", help="run one cycle now and exit")
    parser.add_argument("--status", action="store_true", help="print the journal summary")
    parser.add_argument("--log", type=Path, default=REPO / "user_data" / "logs" / "forward.log")
    args = parser.parse_args()
    if args.status:
        status()
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
            load_spec(args.spec), ccxt.binanceusdm({"enableRateLimit": True, "timeout": 30_000})
        )
        while True:
            now = pd.Timestamp.now(tz="UTC")
            try:
                if runner.full_sync_due(now):
                    runner.full_sync()
                runner.cycle(now)
            except Exception:  # network, exchange or data hiccup: retry at the next close
                logger.exception("cycle failed")
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
