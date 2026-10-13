"""
Outbox of the product messages: one row per message key (a setup, a close, a week, an alert per
period), so no message is produced twice. A message meant for Telegram waits as ``pending``
until delivered (``sent``); one that is not (Telegram off, or a kind kept for the log) is stored
as ``local`` and never sent later, so turning Telegram on does not flush old messages. Delivery
(``flush``) goes oldest first and stops at the first failure, so the order holds; a message
still waiting after ``MAX_AGE`` expires (it stays in ``messages.log``).

``TelegramSender`` only calls ``sendMessage`` (plain text, one chat): it never reads incoming
updates, so nobody can command the bot. The token is kept out of every error and log line
(``requests`` errors carry the URL, which holds it).
"""

import json
import os
import sqlite3
import time
from collections.abc import Callable, Mapping
from pathlib import Path

import pandas as pd
import requests


SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
    key TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    state TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    sent_at TEXT,
    last_error TEXT
);
"""
STATES = ("local", "pending", "sent", "expired")
MAX_AGE = pd.Timedelta(hours=6)  # a message waiting longer is stale: expire it
LIMIT = 4000  # characters per Telegram message (the API takes 4096)
PAUSE = 1.0  # seconds between messages to one chat
API = "https://api.telegram.org/bot{token}/sendMessage"


class SendError(Exception):
    """Delivery failed; the message carries no secret."""


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def enqueue(
    conn: sqlite3.Connection, key: str, kind: str, text: str, send: bool, now: pd.Timestamp
) -> bool:
    """Add a message once per key; False if the key exists. ``send``: wait for delivery."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO outbox (key, created_at, kind, text, state) VALUES (?, ?, ?, ?, ?)",
        (key, pd.Timestamp(now).isoformat(), kind, text, "pending" if send else "local"),
    )
    conn.commit()
    return cur.rowcount == 1


def has(conn: sqlite3.Connection, key: str) -> bool:
    return conn.execute("SELECT 1 FROM outbox WHERE key = ?", (key,)).fetchone() is not None


def rows(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query("SELECT * FROM outbox ORDER BY created_at, rowid", conn)


def split_text(text: str, limit: int = LIMIT) -> list[str]:
    """Pieces of at most ``limit`` characters, cut at line ends (a longer line is cut hard)."""
    out: list[str] = []
    lines: list[str] = []
    size = 0
    for line in text.split("\n"):
        for piece in [line[i : i + limit] for i in range(0, len(line), limit)] or [""]:
            add = len(piece) + (1 if lines else 0)
            if lines and size + add > limit:
                out.append("\n".join(lines))
                lines, add = [], len(piece)
                size = 0
            lines.append(piece)
            size += add
    out.append("\n".join(lines))
    return out


def _mark(conn: sqlite3.Connection, key: str, **values) -> None:
    sets = ", ".join(f"{k} = ?" for k in values)
    conn.execute(f"UPDATE outbox SET {sets} WHERE key = ?", (*values.values(), key))
    conn.commit()


def flush(
    conn: sqlite3.Connection,
    sender,
    now: pd.Timestamp,
    max_age: pd.Timedelta = MAX_AGE,
    pause: float = PAUSE,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, int]:
    """Deliver the pending messages, oldest first, with ``sender.send(text)``. The first failure
    stops the round (the message stays pending with its error, retried next time); a message
    older than ``max_age`` expires instead."""
    done = {"sent": 0, "expired": 0, "failed": 0}
    waiting = pd.read_sql_query(
        "SELECT key, created_at, text, attempts FROM outbox WHERE state = 'pending' "
        "ORDER BY created_at, rowid",
        conn,
    )
    calls = 0
    for m in waiting.itertuples():
        if now - pd.Timestamp(m.created_at) > max_age:
            _mark(conn, m.key, state="expired")
            done["expired"] += 1
            continue
        try:
            for piece in split_text(m.text):
                if calls:
                    sleep(pause)
                calls += 1
                sender.send(piece)
        except SendError as e:
            _mark(conn, m.key, attempts=int(m.attempts) + 1, last_error=str(e))
            done["failed"] += 1
            break
        _mark(
            conn,
            m.key,
            state="sent",
            sent_at=pd.Timestamp(now).isoformat(),
            attempts=int(m.attempts) + 1,
        )
        done["sent"] += 1
    return done


class TelegramSender:
    """Send-only Telegram client for one chat (``sendMessage``, plain text)."""

    def __init__(
        self, token: str, chat_id: str, post: Callable = requests.post, timeout: float = 15
    ) -> None:
        self._token, self.chat_id, self._post, self.timeout = token, chat_id, post, timeout

    def __repr__(self) -> str:
        return f"TelegramSender(chat_id={self.chat_id!r})"

    def _clean(self, text: str) -> str:
        return text.replace(self._token, "***") if self._token else text

    def send(self, text: str) -> None:
        body = {"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True}
        try:
            r = self._post(API.format(token=self._token), json=body, timeout=self.timeout)
        except Exception as e:  # requests errors quote the URL, which holds the token
            raise SendError(self._clean(f"{type(e).__name__}: {e}")) from None
        try:
            reply = r.json()
        except ValueError:
            reply = {}
        if r.status_code != 200 or not reply.get("ok"):
            raise SendError(self._clean(f"HTTP {r.status_code}: {reply.get('description', '')}"))


def read_dotenv(path: Path) -> dict[str, str]:
    """``KEY=value`` lines of a .env file (``export`` prefix and quotes allowed, ``#`` comments
    skipped); empty without the file."""
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        out[key.strip()] = value.strip().strip("\"'")
    return out


def credentials(
    config: Path, env: Mapping[str, str] = os.environ, dotenv: Path | None = None
) -> tuple[str, str] | None:
    """Telegram token and chat id. Looked up in the process environment, then the ``dotenv``
    file, then the ``telegram`` section of the Freqtrade config (whose ``enabled`` flag switches
    Freqtrade's own interactive bot and is ignored here). Token: ``FREQTRADE__TELEGRAM__TOKEN``
    or ``TELEGRAM_BOT_TOKEN``; chat: ``FREQTRADE__TELEGRAM__CHAT_ID``, ``TELEGRAM_CHAT_ID`` or
    the first id of ``TELEGRAM_ADMIN_CHAT_IDS``. None when either is missing."""
    values = {**(read_dotenv(dotenv) if dotenv else {}), **env}
    section: dict = {}
    if config.exists():
        section = json.loads(config.read_text(encoding="utf-8")).get("telegram") or {}
    admins = values.get("TELEGRAM_ADMIN_CHAT_IDS", "").replace(",", " ").split()
    token = (
        values.get("FREQTRADE__TELEGRAM__TOKEN")
        or values.get("TELEGRAM_BOT_TOKEN")
        or str(section.get("token") or "")
    )
    chat = (
        values.get("FREQTRADE__TELEGRAM__CHAT_ID")
        or values.get("TELEGRAM_CHAT_ID")
        or (admins[0] if admins else "")
        or str(section.get("chat_id") or "")
    )
    return (token, chat) if token and chat else None
