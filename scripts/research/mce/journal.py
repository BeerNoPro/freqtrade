"""
Forward journal of trade setups. A setup is written BEFORE its outcome is known and can never be
changed: SQLite triggers reject UPDATE and DELETE on both tables. Each setup gets at most one
outcome, recorded once its exit is known.

R = |entry - stop| (the risk taken). Barrier setups (``max_bars`` set) exit at the stop, the
target or the close of the last candle, whichever comes first; a candle touching both is
counted as a stop. Rule-exit setups (``max_bars`` NULL, e.g. a trailing trend position) are
closed by the strategy with ``record_outcome``.
"""

import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd


SCHEMA = """
CREATE TABLE IF NOT EXISTS setups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    strategy TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    source TEXT NOT NULL,
    pair TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    candle_date TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    side INTEGER NOT NULL,
    entry REAL NOT NULL,
    atr REAL NOT NULL,
    stop REAL NOT NULL,
    target REAL,
    max_bars INTEGER,
    qty REAL NOT NULL,
    notional REAL NOT NULL,
    leverage_max INTEGER NOT NULL,
    cost_r REAL NOT NULL,
    status TEXT NOT NULL,
    bid REAL,
    ask REAL,
    slip_bps REAL,
    reason TEXT NOT NULL,
    UNIQUE (strategy, pair, candle_date)
);
CREATE TABLE IF NOT EXISTS outcomes (
    setup_id INTEGER PRIMARY KEY REFERENCES setups(id),
    scored_at TEXT NOT NULL,
    exit_reason TEXT NOT NULL,
    exit_date TEXT NOT NULL,
    exit_price REAL NOT NULL,
    gross_r REAL NOT NULL,
    fee_r REAL NOT NULL,
    funding_r REAL NOT NULL,
    net_r REAL NOT NULL
);
CREATE TRIGGER IF NOT EXISTS setups_no_update BEFORE UPDATE ON setups
BEGIN SELECT RAISE(ABORT, 'journal is append-only'); END;
CREATE TRIGGER IF NOT EXISTS setups_no_delete BEFORE DELETE ON setups
BEGIN SELECT RAISE(ABORT, 'journal is append-only'); END;
CREATE TRIGGER IF NOT EXISTS outcomes_no_update BEFORE UPDATE ON outcomes
BEGIN SELECT RAISE(ABORT, 'journal is append-only'); END;
CREATE TRIGGER IF NOT EXISTS outcomes_no_delete BEFORE DELETE ON outcomes
BEGIN SELECT RAISE(ABORT, 'journal is append-only'); END;
"""

SOURCES = ("live", "live_late")
STATUSES = ("setup", "skipped_cost")

# (pair, timeframe, candle_date ISO, n) -> the next n closed candles (date, high, low, close)
CandleFetcher = Callable[[str, str, str, int], pd.DataFrame]
# (pair, start, end) -> sum of funding rates printed in (start, end]
FundingFetcher = Callable[[str, pd.Timestamp, pd.Timestamp], float]


@dataclass(frozen=True)
class Setup:
    strategy: str
    fingerprint: str
    source: str  # live: written within the decision window; live_late: caught up after it
    pair: str
    timeframe: str
    candle_date: str  # open time of the decision candle, UTC ISO
    decided_at: str  # its close
    side: int
    entry: float
    atr: float
    stop: float
    target: float | None
    max_bars: int | None
    qty: float
    notional: float
    leverage_max: int
    cost_r: float
    status: str
    reason: str
    bid: float | None = None
    ask: float | None = None
    slip_bps: float | None = None

    @property
    def risk(self) -> float:
        return abs(self.entry - self.stop)


@dataclass(frozen=True)
class Outcome:
    exit_reason: str  # target | stop | time | rule
    exit_date: str  # close time of the exit candle, UTC ISO
    exit_price: float
    gross_r: float
    fee_r: float
    funding_r: float

    @property
    def net_r(self) -> float:
        return self.gross_r - self.fee_r - self.funding_r


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def record_setup(conn: sqlite3.Connection, s: Setup) -> bool:
    """Insert one setup; False if the strategy already has one on that pair and candle."""
    if s.side not in (-1, 1) or s.source not in SOURCES or s.status not in STATUSES:
        raise ValueError("side must be +-1, source and status known")
    if not s.risk > 0 or (s.target is not None and s.side * (s.target - s.entry) <= 0):
        raise ValueError("stop must differ from entry and the target lie in the trade direction")
    if s.side * (s.entry - s.stop) <= 0:
        raise ValueError("stop must lie against the trade direction")
    data = {"created_at": _now(), **asdict(s)}
    cols = ", ".join(data)
    marks = ", ".join("?" for _ in data)
    cur = conn.execute(
        f"INSERT OR IGNORE INTO setups ({cols}) VALUES ({marks})", tuple(data.values())
    )
    conn.commit()
    return cur.rowcount == 1


def record_outcome(conn: sqlite3.Connection, setup_id: int, o: Outcome) -> bool:
    """Record the single outcome of a setup; False if it already has one."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO outcomes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            setup_id,
            _now(),
            o.exit_reason,
            o.exit_date,
            o.exit_price,
            o.gross_r,
            o.fee_r,
            o.funding_r,
            o.net_r,
        ),
    )
    conn.commit()
    return cur.rowcount == 1


def setups(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query("SELECT * FROM setups ORDER BY candle_date, pair", conn)


def pending(conn: sqlite3.Connection) -> pd.DataFrame:
    """Taken setups (not skipped) without an outcome yet."""
    return pd.read_sql_query(
        "SELECT s.* FROM setups s LEFT JOIN outcomes o ON o.setup_id = s.id "
        "WHERE o.setup_id IS NULL AND s.status = 'setup' ORDER BY s.candle_date",
        conn,
    )


def scored(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT s.*, o.scored_at, o.exit_reason, o.exit_date, o.exit_price, o.gross_r, o.fee_r, "
        "o.funding_r, o.net_r FROM setups s JOIN outcomes o ON o.setup_id = s.id "
        "ORDER BY s.candle_date, s.pair",
        conn,
    )


def barrier_exit(
    side: int, stop: float, target: float | None, path: pd.DataFrame
) -> tuple[str, int, float]:
    """(reason, position in ``path``, exit price) of a barrier setup over the candles after the
    decision. A candle touching both the stop and the target counts as a stop."""
    high, low = path["high"].to_numpy(dtype=float), path["low"].to_numpy(dtype=float)
    for i in range(len(path)):
        hit_stop = low[i] <= stop if side > 0 else high[i] >= stop
        if hit_stop:
            return "stop", i, stop
        if target is not None and (high[i] >= target if side > 0 else low[i] <= target):
            return "target", i, target
    return "time", len(path) - 1, float(path["close"].iloc[-1])


def outcome(
    side: int,
    entry: float,
    risk: float,
    exit_reason: str,
    exit_date: pd.Timestamp,
    exit_price: float,
    funding_sum: float,
    round_trip_fee: float,
) -> Outcome:
    """Outcome in R. Fees on the entry notional; funding paid by a long, received by a short."""
    return Outcome(
        exit_reason=exit_reason,
        exit_date=pd.Timestamp(exit_date).isoformat(),
        exit_price=float(exit_price),
        gross_r=float(side * (exit_price - entry) / risk),
        fee_r=float(round_trip_fee * entry / risk),
        funding_r=float(side * funding_sum * entry / risk),
    )


def score_barriers(
    conn: sqlite3.Connection,
    candles: CandleFetcher,
    funding: FundingFetcher,
    bar: pd.Timedelta,
    round_trip_fee: float,
) -> int:
    """Score every pending barrier setup whose candles up to the exit are closed."""
    done = 0
    for s in pending(conn).itertuples():
        if pd.isna(s.max_bars):
            continue
        n = int(s.max_bars)
        path = candles(s.pair, s.timeframe, s.candle_date, n).reset_index(drop=True)
        target = None if pd.isna(s.target) else float(s.target)
        reason, i, price = barrier_exit(s.side, s.stop, target, path)
        if reason == "time" and len(path) < n:
            continue  # neither barrier touched yet and the time exit is still open
        exit_date = pd.Timestamp(path["date"].iloc[i]) + bar
        paid = funding(s.pair, pd.Timestamp(s.decided_at), exit_date)
        risk = abs(s.entry - s.stop)
        o = outcome(s.side, s.entry, risk, reason, exit_date, price, paid, round_trip_fee)
        done += record_outcome(conn, int(s.id), o)
    return done


def reconcile(live: pd.DataFrame, replay: pd.DataFrame, rtol: float = 1e-9) -> pd.DataFrame:
    """Setups of the live journal against the same rules replayed on archived data, keyed by
    (strategy, pair, candle_date). Returns one row per disagreement: missing on either side,
    another side, entry or stop. Both frames need those key columns plus side, entry, stop."""
    key = ["strategy", "pair", "candle_date"]
    cols = [*key, "side", "entry", "stop"]
    both = live[cols].merge(replay[cols], on=key, how="outer", suffixes=("_live", "_replay"))
    both["issue"] = ""
    both.loc[both["side_replay"].isna(), "issue"] = "only live"
    both.loc[both["side_live"].isna(), "issue"] = "only replay"
    matched = both["issue"] == ""
    for col in ("entry", "stop"):
        a, b = both[f"{col}_live"], both[f"{col}_replay"]
        off = matched & ~np.isclose(a, b, rtol=rtol, atol=0)
        both.loc[off, "issue"] = f"{col} differs"
    both.loc[matched & (both["side_live"] != both["side_replay"]), "issue"] = "side differs"
    return both[both["issue"] != ""].reset_index(drop=True)
