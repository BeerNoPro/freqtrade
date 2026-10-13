"""
Product layer of the forward runner (docs/signal-bot-workflow.md, sections 3-7). After each
cycle: bring the risk book (``mce.riskbook``) up to date from the journal, turn the new
allocations and closures into messages (``mce.messages``), add the weekly summary once a week,
and queue everything in the outbox (``mce.outbox``); every message is also appended to
``messages.log``. The journal is only read.

Configuration: ``user_data/research/product.json`` (private): ``capital``, ``risk`` (caps of
``RiskPolicy``), ``book_start``, ``telegram_send``, ``send_kinds``, and per rule its display
``name``, ``validated`` flag (drops the experimental label), published ``evidence`` lines and
``milestone``.
"""

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from mce import journal, messages, outbox, riskbook
from mce.riskbook import TAKEN, RiskPolicy


KINDS = ("setup", "close", "weekly", "alert")  # sent when Telegram is on; others stay in the log


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
    ) -> None:
        self.j, self.cfg, self.log_path, self.sender = journal_conn, cfg, log_path, sender
        self.db = riskbook.connect(db_path)
        outbox.init(self.db)

    def post(self, key: str, kind: str, text: str, now: pd.Timestamp) -> bool:
        """Queue a message once per key and append it to the log. It waits for Telegram only if
        a sender exists, sending is on and its kind is one of ``send_kinds``."""
        send = self.sender is not None and self.cfg.telegram_send and kind in self.cfg.send_kinds
        if not outbox.enqueue(self.db, key, kind, text, send, now):
            return False
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(f"[{pd.Timestamp(now).isoformat(timespec='seconds')}] {kind} {key}\n{text}\n\n")
        return True

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
            mine = setups["strategy"] == a["strategy"]
            evidence = messages.evidence_lines(
                rule, setups[mine], scored[scored["strategy"] == a["strategy"]], now
            )
            text = messages.setup_text(s, a, rule, evidence, self.cfg.policy, _hedge(a, open_))
            n += self.post(key, "setup", text, now)
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
            s = by_id.loc[sid]
            text = messages.close_text(
                s, alloc.loc[sid], o, self._rule(s["strategy"]), self.cfg.policy
            )
            n += self.post(f"close:{sid}", "close", text, now)
        return n

    def _weekly(self, now: pd.Timestamp, setups: pd.DataFrame, scored: pd.DataFrame) -> int:
        """The summary of the last full week, once, at the first step from Monday 00:00 UTC."""
        end = now.normalize() - pd.Timedelta(days=now.weekday())
        start = end - pd.Timedelta(days=7)
        iso = start.isocalendar()
        key = f"weekly:{iso.year}-W{iso.week:02d}"
        if self.cfg.book_start >= end or outbox.has(self.db, key):
            return 0
        text = messages.weekly_text(
            (start, end),
            riskbook.book(self.db),
            setups,
            scored,
            self.cfg.rules,
            self.cfg.policy,
            self.ops(start, end),
        )
        return int(self.post(key, "weekly", text, now))

    def ops(self, start: pd.Timestamp, end: pd.Timestamp) -> dict:
        """Operations of a period for the weekly summary (cycles on time, recorder coverage)."""
        return {}

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
