"""
Outbox of the product messages: one row per message key (a setup, a close, a week, an alert per
period), so no message is produced twice. A message meant for Telegram waits as ``pending``
until delivered (``sent``); one that is not (Telegram off, or a kind kept for the log) is stored
as ``local`` and never sent later, so turning Telegram on does not flush old messages.
"""

import sqlite3

import pandas as pd


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
