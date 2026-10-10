"""
Forward journal: every assessment is written BEFORE its outcome is known, then scored later.

``prob`` is the stated probability that price moves in ``side`` direction over ``horizon``
candles (close-to-close). Scoring is append-only: an assessment is scored once.
"""

import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd


SCHEMA = """
CREATE TABLE IF NOT EXISTS assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    candle_date TEXT NOT NULL,
    pair TEXT NOT NULL,
    hook_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    regime TEXT,
    side INTEGER NOT NULL,
    prob REAL NOT NULL,
    horizon INTEGER NOT NULL,
    timeframe TEXT NOT NULL,
    entry REAL NOT NULL,
    atr REAL NOT NULL,
    invalidation REAL,
    UNIQUE (candle_date, pair, hook_id)
);
CREATE TABLE IF NOT EXISTS outcomes (
    assessment_id INTEGER PRIMARY KEY REFERENCES assessments(id),
    scored_at TEXT NOT NULL,
    ret_r REAL NOT NULL,
    hit INTEGER NOT NULL,
    invalidated INTEGER NOT NULL,
    brier REAL NOT NULL
);
"""

# (pair, timeframe, after_candle_date, n) -> next n closed candles (date, high, low, close)
CandleFetcher = Callable[[str, str, str, int], pd.DataFrame]


@dataclass(frozen=True)
class Assessment:
    candle_date: str
    pair: str
    hook_id: str
    fingerprint: str
    regime: str | None
    side: int
    prob: float
    horizon: int
    timeframe: str
    entry: float
    atr: float
    invalidation: float | None = None


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def record(conn: sqlite3.Connection, a: Assessment) -> bool:
    """Insert one assessment; returns False if it already exists (idempotent per candle)."""
    if a.side not in (-1, 1) or not 0 <= a.prob <= 1:
        raise ValueError("side must be +-1 and prob within [0, 1]")
    data = {"created_at": _now(), **asdict(a)}
    cols = ", ".join(data)
    marks = ", ".join("?" for _ in data)
    cur = conn.execute(
        f"INSERT OR IGNORE INTO assessments ({cols}) VALUES ({marks})", tuple(data.values())
    )
    conn.commit()
    return cur.rowcount == 1


def pending(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT a.* FROM assessments a LEFT JOIN outcomes o ON o.assessment_id = a.id "
        "WHERE o.assessment_id IS NULL ORDER BY a.candle_date",
        conn,
    )


def score_pending(conn: sqlite3.Connection, fetch: CandleFetcher) -> int:
    """Score every assessment whose horizon has fully closed. Returns the number scored."""
    scored = 0
    for a in pending(conn).itertuples():
        path = fetch(a.pair, a.timeframe, a.candle_date, a.horizon)
        if len(path) < a.horizon:
            continue
        path = path.iloc[: a.horizon]
        ret_r = a.side * (path["close"].iloc[-1] - a.entry) / a.atr
        hit = int(ret_r > 0)
        invalidated = 0
        if a.invalidation is not None and pd.notna(a.invalidation):
            breach = path["low"] <= a.invalidation if a.side > 0 else path["high"] >= a.invalidation
            invalidated = int(breach.any())
        conn.execute(
            "INSERT INTO outcomes VALUES (?, ?, ?, ?, ?, ?)",
            (a.id, _now(), float(ret_r), hit, invalidated, float((a.prob - hit) ** 2)),
        )
        scored += 1
    conn.commit()
    return scored


def scored(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT a.*, o.scored_at, o.ret_r, o.hit, o.invalidated, o.brier "
        "FROM assessments a JOIN outcomes o ON o.assessment_id = a.id ORDER BY a.candle_date",
        conn,
    )
