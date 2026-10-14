"""
Product layer of the forward runner (docs/signal-bot-workflow.md, sections 3-7). After each
cycle: bring the risk book (``mce.riskbook``) up to date from the journal, turn the new
allocations and closures into messages (``mce.messages``), add the weekly summary once a week,
and queue everything in the outbox (``mce.outbox``); every message is also appended to
``messages.log``. The journal is only read.

Operational checks after each cycle (``checks``) post alerts: a cycle run late, the recorder's
heartbeat stale, a past day of the recorder below 90%, the journal disagreeing with its replay,
an unregistered rules fingerprint; ``cycle_failed`` for an exception. Repeated alerts share a key
per day or per 6-hour period, so each is sent once. ``flush`` delivers the outbox.

Every message sent to Telegram is one image card (``mce.cards``, saved under ``media/``) with a
short caption; one message per setup, its caption naming the other coins of its bet. A card that
fails to render (no candles, matplotlib missing) leaves the text message.

Configuration: ``user_data/research/product.json`` (private): ``capital``, ``risk`` (caps of
``RiskPolicy``), ``book_start``, ``telegram_send``, ``send_kinds``, and per rule its display
``name``, ``validated`` flag (drops the experimental label), published ``evidence`` lines and
``milestone``.
"""

import json
import logging
import re
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path

import pandas as pd

from mce import journal, messages, outbox, recorder, riskbook
from mce.riskbook import TAKEN, RiskPolicy


logger = logging.getLogger(__name__)


KINDS = ("setup", "close", "weekly", "alert")  # sent when Telegram is on; others stay in the log
LIVE_WINDOW = pd.Timedelta(minutes=15)  # a cycle run later than this after its close is late
HEARTBEAT_AGE = pd.Timedelta(minutes=15)  # the recorder beats every minute
GOOD = 0.90  # share of a day the recorder must cover
CYCLES = """
CREATE TABLE IF NOT EXISTS cycles (
    decision TEXT NOT NULL,
    ran_at TEXT NOT NULL,
    PRIMARY KEY (decision, ran_at)
);
"""
MEDIA_KEEP = pd.Timedelta(days=30)  # card files older than this are deleted
Coverage = Callable[[Path, list[str]], pd.DataFrame]
# (pair, timeframe, end) -> closed candles dated before ``end`` (date, open, high, low, close)
Candles = Callable[[str, str, pd.Timestamp], pd.DataFrame]


def _coverage(outdir: Path, days: list[str]) -> pd.DataFrame:
    return recorder.coverage(outdir, days=days)


def _cards():
    """``mce.cards``, imported when a card is drawn: without matplotlib the messages stay text."""
    from mce import cards

    return cards


@dataclass(frozen=True)
class ProductConfig:
    policy: RiskPolicy
    book_start: pd.Timestamp
    rules: dict
    telegram_send: bool = False
    send_kinds: tuple[str, ...] = KINDS

    @classmethod
    def load(cls, path: Path) -> "ProductConfig":
        raw = json.loads(path.read_text(encoding="utf-8"))
        start = pd.Timestamp(raw["book_start"])
        return cls(
            policy=RiskPolicy(capital=float(raw["capital"]), **raw["risk"]),
            book_start=start if start.tzinfo else start.tz_localize("UTC"),
            rules=raw.get("rules", {}),
            telegram_send=bool(raw.get("telegram_send", False)),
            send_kinds=tuple(raw.get("send_kinds", KINDS)),
        )


def _hedge(a: pd.Series, open_: pd.DataFrame) -> str | None:
    """An open position on the same coin in the other direction, if any."""
    other = open_[(open_["pair"] == a["pair"]) & (open_["side"] == -a["side"])]
    if other.empty:
        return None
    o = other.iloc[0]
    where = "trend" if o["sleeve"] == "trend" else "sự kiện"
    return f"{messages.SIDE[int(o['side'])]} {o['pair']} ({where})"


class Product:
    def __init__(
        self,
        journal_conn: sqlite3.Connection,
        db_path: Path,
        cfg: ProductConfig,
        log_path: Path,
        sender=None,
        recorder_dir: Path | None = None,
        coverage: Coverage = _coverage,
        candles: Candles | None = None,
    ) -> None:
        self.j, self.cfg, self.log_path, self.sender = journal_conn, cfg, log_path, sender
        self.recorder_dir, self.coverage, self.candles = recorder_dir, coverage, candles
        self.media = db_path.parent / "media"
        self.db = riskbook.connect(db_path)
        outbox.init(self.db)
        self.db.executescript(CYCLES)

    def post(
        self,
        key: str,
        kind: str,
        text: str,
        now: pd.Timestamp,
        caption: str | None = None,
        image: bytes | None = None,
    ) -> bool:
        """Queue a message once per key and append it to the log. It waits for Telegram only if
        a sender exists, sending is on and its kind is one of ``send_kinds``; ``image`` (a card)
        is sent as a photo under ``caption``, ``text`` otherwise."""
        send = self.sender is not None and self.cfg.telegram_send and kind in self.cfg.send_kinds
        if outbox.has(self.db, key):
            return False
        path = None
        if image is not None:
            self.media.mkdir(parents=True, exist_ok=True)
            path = self.media / f"{re.sub(r'[^A-Za-z0-9_.-]+', '_', key)}.png"
            path.write_bytes(image)
        outbox.enqueue(self.db, key, kind, text, send, now, caption, str(path) if path else None)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8", newline="\n") as f:
            note = f" [ảnh {path.name}]" if path else ""
            f.write(
                f"[{pd.Timestamp(now).isoformat(timespec='seconds')}] {kind} {key}{note}\n"
                f"{text}\n\n"
            )
        return True

    def _card(self, what: str, draw: Callable[[], bytes]) -> bytes | None:
        """A rendered card, or None (logged) when it fails: the message then goes as text."""
        try:
            return draw()
        except Exception:
            logger.exception("card %s failed: text message instead", what)
            return None

    def _setup_card(self, s, a, rule, evidence, progress) -> bytes:
        bars = self._bars(s["pair"], s["timeframe"], pd.Timestamp(s["decided_at"]))
        return _cards().setup_card(s, a, rule, evidence, progress, self.cfg.policy, bars)

    def _close_card(self, s, a, o, rule) -> bytes:
        bars = self._bars(s["pair"], s["timeframe"], pd.Timestamp(o["exit_date"]))
        return _cards().close_card(s, a, o, rule, self.cfg.policy, bars)

    def _bars(self, pair: str, timeframe: str, end: pd.Timestamp) -> pd.DataFrame:
        if self.candles is None:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close"])
        return self.candles(pair, timeframe, end)

    def step(
        self, now: pd.Timestamp, no_price: dict[int, pd.Timestamp] | None = None
    ) -> dict[str, int]:
        """Book, setup and close messages, weekly summary. ``no_price``: trend setups the rules
        closed without a price (setup id -> exit time)."""
        setups, scored = journal.setups(self.j), journal.scored(self.j)
        allocs, closes = riskbook.update(
            self.db, setups, scored, self.cfg.policy, self.cfg.book_start, now, no_price
        )
        n = self._setups(allocs, setups, scored, now)
        n += self._closes(closes, setups, scored, now)
        n += self._weekly(now, setups, scored)
        return {"allocated": len(allocs), "closed": len(closes), "messages": n}

    def _rule(self, strategy: str) -> dict:
        return self.cfg.rules.get(strategy, {})

    def _setups(self, allocs, setups, scored, now) -> int:
        if allocs.empty:
            return 0
        by_id = setups.set_index("id")
        open_ = riskbook.open_positions(self.db)
        n = 0
        for _, a in allocs.iterrows():
            if a["state"] == "pre_book":
                continue
            s, rule, key = (
                by_id.loc[a["setup_id"]],
                self._rule(a["strategy"]),
                f"setup:{a['setup_id']}",
            )
            if a["state"] not in TAKEN:
                text = messages.not_taken_text(s, a, rule, self.cfg.policy)
                n += self.post(key, "setup_info", text, now)
                continue
            mine = setups[setups["strategy"] == a["strategy"]]
            evidence = messages.evidence_lines(
                rule, mine, scored[scored["strategy"] == a["strategy"]], now
            )
            text = messages.setup_text(s, a, rule, evidence, self.cfg.policy, _hedge(a, open_))
            bet = allocs[(allocs["bet"] == a["bet"]) & allocs["state"].isin(TAKEN)]
            partners = [p for p in bet["pair"] if p != a["pair"]] if a["sleeve"] == "event" else []
            caption = messages.setup_caption(s, a, rule, self.cfg.policy, partners)
            progress = messages.progress_value(rule, mine, now)
            image = self._card(key, partial(self._setup_card, s, a, rule, evidence, progress))
            n += self.post(key, "setup", text, now, caption, image)
        return n

    def _closes(self, closes, setups, scored, now) -> int:
        if closes.empty:
            return 0
        by_id, outcome = setups.set_index("id"), scored.set_index("id")
        alloc = riskbook.book(self.db).set_index("setup_id")
        n = 0
        for _, c in closes.iterrows():
            sid = int(c["setup_id"])
            o = c.copy()
            if sid in outcome.index:
                for col in ("gross_r", "fee_r", "funding_r"):
                    o[col] = outcome.loc[sid, col]
            s, a, rule = by_id.loc[sid], alloc.loc[sid], self._rule(by_id.loc[sid, "strategy"])
            key = f"close:{sid}"
            text = messages.close_text(s, a, o, rule, self.cfg.policy)
            caption = messages.close_caption(s, o, rule, self.cfg.policy)
            image = self._card(key, partial(self._close_card, s, a, o, rule))
            n += self.post(key, "close", text, now, caption, image)
        return n

    def _weekly(self, now: pd.Timestamp, setups: pd.DataFrame, scored: pd.DataFrame) -> int:
        """The summary of the last full week, once, at the first step from Monday 00:00 UTC."""
        end = now.normalize() - pd.Timedelta(days=now.weekday())
        start = end - pd.Timedelta(days=7)
        iso = start.isocalendar()
        key = f"weekly:{iso.year}-W{iso.week:02d}"
        if self.cfg.book_start >= end or outbox.has(self.db, key):
            return 0
        book, ops, rules = riskbook.book(self.db), self.ops(start, end), self.cfg.rules
        text = messages.weekly_text((start, end), book, setups, scored, rules, self.cfg.policy, ops)
        summary = messages.weekly_summary((start, end), book, setups, scored, rules, ops)
        caption = messages.weekly_caption(summary, self.cfg.policy)
        image = self._card(key, lambda: _cards().weekly_card(summary, self.cfg.policy))
        return int(self.post(key, "weekly", text, now, caption, image))

    def ops(self, start: pd.Timestamp, end: pd.Timestamp) -> dict:
        """Operations of [start, end) for the weekly summary, from the book start on: 4h closes
        whose first cycle ran within ``LIVE_WINDOW``, and recorder days covered >= ``GOOD`` in
        every stream (the worst stream-day named)."""
        first = max(start, self.cfg.book_start)
        grid = pd.date_range(first.ceil("4h"), end, freq="4h", inclusive="left")
        runs = pd.read_sql_query(
            "SELECT decision, MIN(ran_at) AS ran_at FROM cycles GROUP BY decision", self.db
        )
        lag = pd.to_datetime(runs["ran_at"], utc=True) - pd.to_datetime(runs["decision"], utc=True)
        on_time = set(pd.to_datetime(runs["decision"], utc=True)[lag <= LIVE_WINDOW])
        out: dict = {"on_time": sum(t in on_time for t in grid), "expected": len(grid)}
        days = [
            d.strftime("%Y-%m-%d")
            for d in pd.date_range(first.ceil("D"), end, freq="D", inclusive="left")
        ]
        if self.recorder_dir is None or not days:
            return out
        cov = self.coverage(self.recorder_dir, days)
        if cov.empty:
            return out
        per_day = cov.groupby("day")["coverage"].min()
        worst = cov.loc[cov["coverage"].idxmin()]
        out |= {
            "recorder_good": int((per_day.reindex(days).fillna(0) >= GOOD).sum()),
            "recorder_days": len(days),
            "recorder_worst": f"{worst['stream']} {worst['coverage']:.0%} ngày {worst['day']}",
        }
        return out

    # ------------------------------------------------------------ operations

    def alert(self, key: str, text: str, now: pd.Timestamp) -> int:
        full = messages.alert_text(text)
        if outbox.has(self.db, f"alert:{key}"):
            return 0
        image = self._card(f"alert:{key}", lambda: _cards().alert_card(text, now))
        return int(self.post(f"alert:{key}", "alert", full, now, messages.cap(full), image))

    def cycle_failed(self, now: pd.Timestamp, error: BaseException) -> int:
        """Alert for a failed cycle, once per error type and 6-hour period."""
        name = type(error).__name__
        text = (
            f"chu kỳ lúc {messages.utc(now)} lỗi: {name}: {str(error)[:300]}. Runner thử lại ở "
            "chu kỳ sau; xem user_data/logs/forward.log"
        )
        return self.alert(f"cycle:{name}:{now.floor('6h'):%Y-%m-%dT%H}", text, now)

    def checks(
        self, now: pd.Timestamp, decision: pd.Timestamp, issues: int = 0, registered: bool = True
    ) -> int:
        """Operational alerts of the cycle of ``decision`` run at ``now`` (recorded in
        ``cycles``); returns the number posted."""
        n = self._late(now, decision) + self._recorder(now)
        day = f"{now:%Y-%m-%d}"
        if issues:
            text = f"nhật ký lệch bản chạy lại ở {issues} setup (xem forward/reconcile_issues.csv)"
            n += self.alert(f"reconcile:{day}", text, now)
        if not registered:
            text = "fingerprint luật khác bản đã đăng ký: runner không ghi setup mới"
            n += self.alert(f"fingerprint:{day}", text, now)
        return n

    def _late(self, now: pd.Timestamp, decision: pd.Timestamp) -> int:
        """A cycle first run more than ``LIVE_WINDOW`` after its close (a restart after an
        on-time run is not late); nothing on the very first cycle recorded."""
        key = decision.isoformat()
        before = [
            r[0] for r in self.db.execute("SELECT ran_at FROM cycles WHERE decision = ?", (key,))
        ]
        last = self.db.execute("SELECT MAX(ran_at) FROM cycles").fetchone()[0]
        self.db.execute("INSERT OR IGNORE INTO cycles VALUES (?, ?)", (key, now.isoformat()))
        self.db.commit()
        lag = now - decision
        if last is None or lag <= LIVE_WINDOW:
            return 0
        if any(pd.Timestamp(t) - decision <= LIVE_WINDOW for t in before):
            return 0
        text = (
            f"runner chạy chu kỳ {messages.utc(decision)} trễ {int(lag.total_seconds() // 60)} "
            f"phút (máy ngủ, mất mạng hoặc runner dừng); chu kỳ trước chạy lúc "
            f"{messages.utc(last)}. Setup của nến bị lỡ được ghi bù, đánh dấu trễ, không vào lệnh"
        )
        return self.alert(f"late:{key}", text, now)

    def _recorder(self, now: pd.Timestamp) -> int:
        """Stale heartbeat (once per 6 hours) and the coverage of yesterday (once per day, only
        for days starting at or after the book start)."""
        if self.recorder_dir is None:
            return 0
        n = 0
        beat = self.recorder_dir / ".heartbeat"
        last = None
        if beat.exists():
            text = beat.read_text(encoding="utf-8").strip()
            last = pd.Timestamp(int(text), unit="ms", tz="UTC") if text.isdigit() else None
        if last is None or now - last > HEARTBEAT_AGE:
            seen = (
                f"nhịp tim cuối {messages.utc(last)}" if last is not None else "không có nhịp tim"
            )
            text = f"bộ ghi dữ liệu không chạy ({seen}). Kiểm tra task freqtrade-market-recorder"
            n += self.alert(f"recorder_down:{now.floor('6h'):%Y-%m-%dT%H}", text, now)
        day = now.floor("D") - pd.Timedelta(days=1)
        key = f"coverage:{day:%Y-%m-%d}"
        if day < self.cfg.book_start or outbox.has(self.db, f"alert:{key}"):
            return n
        cov = self.coverage(self.recorder_dir, [f"{day:%Y-%m-%d}"])
        low = cov[cov["coverage"] < GOOD] if len(cov) else cov
        if len(low):
            streams = ", ".join(f"{r.stream} {r.coverage:.0%}" for r in low.itertuples())
            text = f"bộ ghi thiếu dữ liệu ngày {day:%Y-%m-%d}: {streams} (cần ≥ 90%)"
            n += self.alert(key, text, now)
        return n

    def flush(self, now: pd.Timestamp, sleep: Callable[[float], None] = time.sleep) -> dict:
        """Deliver the outbox through Telegram (nothing without a sender); delete card files
        older than ``MEDIA_KEEP``."""
        if self.media.exists():
            cutoff = (now - MEDIA_KEEP).timestamp()
            for f in self.media.glob("*.png"):
                if f.stat().st_mtime < cutoff:
                    f.unlink()
        if self.sender is None:
            return {}
        return outbox.flush(self.db, self.sender, now, sleep=sleep)

    def status(self) -> str:
        """Book summary for ``run_forward.py --status``."""
        b = riskbook.book(self.db)
        if b.empty:
            return "product book empty"
        taken = b[b["state"].isin(TAKEN)]
        open_ = riskbook.open_risk(riskbook.open_positions(self.db))
        pnl = taken["pnl"].sum()
        states = b["state"].value_counts().to_dict()
        return (
            f"book: {states}; open {open_['total']:.2%} (long {open_['long']:.2%}, short "
            f"{open_['short']:.2%}); closed P&L {pnl:+.2%} of {self.cfg.policy.capital:,.0f} USDT"
        )
