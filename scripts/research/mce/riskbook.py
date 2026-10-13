"""
Portfolio risk budget of the product layer (docs/signal-bot-workflow.md, section 4): which setups
of the forward journal a follower of the bot takes, and with how much risk. The journal is only
read. The book (``allocations``, ``closures``) is a separate sqlite file and, like the journal,
append-only: every journal setup gets one allocation, every position taken one closure.

Risk = the share of the sample capital lost at the stop. At each decision time:
1. not taken: decided before the book start (``pre_book``), skipped by the journal for its cost
   (``skipped_cost``), recorded after the decision window (``late``), a trend setup on a coin
   that already holds a trend position of any rule or direction (``duplicate``);
2. trend setups (daily rules), by rule then pair, get ``trend_slot`` each;
3. the event setups (4h rules) of one direction are one bet, one market shock: ``event_bet`` in
   total, split equally; the long bet first, then the short one;
4. a trend setup or a bet that would take the open risk of its direction above ``max_side`` or
   the total above ``max_total`` is scaled down to fit (``reduced``), and blocked when a setup
   would get less than ``min_setup`` (``blocked``).
Size = min(capital x risk granted / |entry - stop|, capital x leverage cap / entry); the book
holds the risk actually taken. A position frees its risk at the journal exit; one whose coin
stopped trading closes without a price (``no_price``, no P&L).
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class RiskPolicy:
    """Risk budget of the product (chosen by the user on 2026-10-13, never optimised)."""

    capital: float = 10_000.0  # sample capital, USDT
    event_bet: float = 0.01  # one bet: the 4h setups of one decision time and direction
    trend_slot: float = 0.0025  # one trend position (1/20 of a 5% trend budget)
    max_side: float = 0.05  # open risk in one direction
    max_total: float = 0.08  # open risk in total
    min_setup: float = 0.001  # a setup granted less than this is blocked


TAKEN = ("sent", "reduced")
NOT_TAKEN = ("blocked", "pre_book", "skipped_cost", "late", "duplicate")
BARRIER_GRACE = pd.Timedelta(days=2)  # no outcome this long after the time exit: no price
EPS = 1e-12

SCHEMA = """
CREATE TABLE IF NOT EXISTS allocations (
    setup_id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    strategy TEXT NOT NULL,
    pair TEXT NOT NULL,
    side INTEGER NOT NULL,
    sleeve TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    bet TEXT NOT NULL,
    bet_size INTEGER NOT NULL,
    state TEXT NOT NULL,
    limit_hit TEXT NOT NULL,
    base_risk REAL NOT NULL,
    granted_risk REAL NOT NULL,
    risk REAL NOT NULL,
    qty REAL NOT NULL,
    notional REAL NOT NULL,
    capital REAL NOT NULL,
    open_long REAL NOT NULL,
    open_short REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS closures (
    setup_id INTEGER PRIMARY KEY REFERENCES allocations(setup_id),
    created_at TEXT NOT NULL,
    exit_reason TEXT NOT NULL,
    exit_date TEXT NOT NULL,
    exit_price REAL,
    net_r REAL,
    pnl REAL
);
CREATE TRIGGER IF NOT EXISTS allocations_no_update BEFORE UPDATE ON allocations
BEGIN SELECT RAISE(ABORT, 'book is append-only'); END;
CREATE TRIGGER IF NOT EXISTS allocations_no_delete BEFORE DELETE ON allocations
BEGIN SELECT RAISE(ABORT, 'book is append-only'); END;
CREATE TRIGGER IF NOT EXISTS closures_no_update BEFORE UPDATE ON closures
BEGIN SELECT RAISE(ABORT, 'book is append-only'); END;
CREATE TRIGGER IF NOT EXISTS closures_no_delete BEFORE DELETE ON closures
BEGIN SELECT RAISE(ABORT, 'book is append-only'); END;
"""

ALLOC_COLS = [
    "setup_id",
    "strategy",
    "pair",
    "side",
    "sleeve",
    "decided_at",
    "bet",
    "bet_size",
    "state",
    "limit_hit",
    "base_risk",
    "granted_risk",
    "risk",
    "qty",
    "notional",
    "open_long",
    "open_short",
]
CLOSE_COLS = ["setup_id", "exit_reason", "exit_date", "exit_price", "net_r", "pnl"]


def sleeve(timeframe: str) -> str:
    """``trend`` for daily rules, ``event`` for the others."""
    return "trend" if timeframe == "1d" else "event"


class _Pass:
    """One allocation pass: the open risk by direction and the coins holding a trend position,
    updated as setups are taken."""

    def __init__(self, open_: pd.DataFrame, policy: RiskPolicy, book_start: pd.Timestamp):
        self.policy, self.book_start = policy, book_start
        self.side_risk = {s: float(open_.loc[open_["side"] == s, "risk"].sum()) for s in (1, -1)}
        self.held = set(open_.loc[open_["sleeve"] == "trend", "pair"])
        self.rows: list[dict] = []

    def room(self, side: int) -> tuple[float, str]:
        """Risk still allowed in ``side`` and the cap that sets it."""
        by_side = self.policy.max_side - self.side_risk[side]
        by_total = self.policy.max_total - self.side_risk[1] - self.side_risk[-1]
        return (max(by_side, 0.0), "side") if by_side <= by_total else (max(by_total, 0.0), "total")

    def budget(self, want: float, n: int, side: int) -> tuple[str, float, str]:
        """(state, risk per setup, cap that bound) of a bet worth ``want`` split over ``n``."""
        avail, limit = self.room(side)
        scale = min(1.0, avail / want)
        each = want * scale / n
        if each < self.policy.min_setup - EPS:
            return "blocked", 0.0, limit
        return ("sent", each, "") if scale >= 1 - EPS else ("reduced", each, limit)

    def skip(self, s) -> str | None:
        if pd.Timestamp(s.decided_at) < self.book_start:
            return "pre_book"
        if s.status == "skipped_cost":
            return "skipped_cost"
        return "late" if s.source == "live_late" else None

    def add(self, s, state: str, bet: str, n: int, base: float, granted: float = 0.0, limit=""):
        cap = self.policy.capital
        qty = risk = 0.0
        if state in TAKEN:
            dist = abs(s.entry - s.stop)
            qty = min(cap * granted / dist, cap * s.leverage_max / s.entry)
            risk = qty * dist / cap
            self.side_risk[int(s.side)] += risk
        self.rows.append(
            {
                "setup_id": int(s.id),
                "strategy": s.strategy,
                "pair": s.pair,
                "side": int(s.side),
                "sleeve": sleeve(s.timeframe),
                "decided_at": s.decided_at,
                "bet": bet,
                "bet_size": n,
                "state": state,
                "limit_hit": limit,
                "base_risk": base,
                "granted_risk": granted,
                "risk": risk,
                "qty": qty,
                "notional": qty * s.entry,
            }
        )

    def trend(self, rows: pd.DataFrame) -> None:
        for s in rows.itertuples():
            bet = f"{s.decided_at}|{s.pair}"
            state = self.skip(s) or ("duplicate" if s.pair in self.held else None)
            if state:
                self.add(s, state, bet, 1, 0.0)
                continue
            state, each, limit = self.budget(self.policy.trend_slot, 1, int(s.side))
            self.add(s, state, bet, 1, self.policy.trend_slot, each, limit)
            if state in TAKEN:
                self.held.add(s.pair)

    def bet(self, rows: list, side: int) -> None:
        """The event setups of one direction: one bet, split over the setups taken."""
        bet = f"{rows[0].decided_at}|{side:+d}"
        ok = [s for s in rows if self.skip(s) is None]
        for s in rows:
            if (state := self.skip(s)) is not None:
                self.add(s, state, bet, len(ok), 0.0)
        if ok:
            state, each, limit = self.budget(self.policy.event_bet, len(ok), side)
            for s in ok:
                self.add(s, state, bet, len(ok), self.policy.event_bet / len(ok), each, limit)

    def result(self) -> pd.DataFrame:
        out = pd.DataFrame(self.rows, columns=ALLOC_COLS[:-2])
        out["open_long"], out["open_short"] = self.side_risk[1], self.side_risk[-1]
        return out


def allocate(
    batch: pd.DataFrame, open_: pd.DataFrame, policy: RiskPolicy, book_start: pd.Timestamp
) -> pd.DataFrame:
    """Allocation of the journal setups of one decision time (journal columns ``id, strategy,
    pair, timeframe, decided_at, side, entry, stop, leverage_max, status, source``) given the open
    positions (``pair, side, sleeve, risk``). One row per setup, trend setups first;
    ``open_long`` / ``open_short`` are the open risk after the whole batch."""
    p = _Pass(open_, policy, book_start)
    kind = np.array([sleeve(tf) for tf in batch["timeframe"]])
    p.trend(batch[kind == "trend"].sort_values(["strategy", "pair"]))
    events = batch[kind == "event"].sort_values(["strategy", "pair"])
    for side in (1, -1):
        rows = list(events[events["side"] == side].itertuples())
        if rows:
            p.bet(rows, side)
    return p.result()


# ---------------------------------------------------------------- book


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def _py(v):
    """A sqlite-bindable Python value: numpy scalars unwrapped, NaN as NULL."""
    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, float) and not np.isfinite(v):
        return None
    return v


def _insert(conn: sqlite3.Connection, table: str, data: pd.DataFrame, required: list[str]) -> int:
    for col in required:
        if data[col].isna().any():
            raise ValueError(f"{table}: missing value in {col}")
    cols, marks = ", ".join(data.columns), ", ".join("?" for _ in data.columns)
    rows = [tuple(_py(v) for v in r) for r in data.itertuples(index=False, name=None)]
    cur = conn.executemany(f"INSERT OR IGNORE INTO {table} ({cols}) VALUES ({marks})", rows)
    conn.commit()
    return cur.rowcount


def record_allocations(
    conn: sqlite3.Connection, rows: pd.DataFrame, capital: float, created: pd.Timestamp
) -> int:
    """Insert allocation rows (one per setup, never replaced); returns rows added. A missing value
    raises: ``INSERT OR IGNORE`` would drop the row silently on a NOT NULL violation."""
    if rows.empty:
        return 0
    data = rows[ALLOC_COLS].copy()
    data.insert(1, "created_at", pd.Timestamp(created).isoformat())
    data["capital"] = capital
    return _insert(conn, "allocations", data, list(data.columns))


def record_closures(conn: sqlite3.Connection, rows: pd.DataFrame, created: pd.Timestamp) -> int:
    """Insert closures (``exit_price``, ``net_r``, ``pnl`` may be missing: no price)."""
    if rows.empty:
        return 0
    data = rows[CLOSE_COLS].copy()
    data.insert(1, "created_at", pd.Timestamp(created).isoformat())
    return _insert(conn, "closures", data, ["setup_id", "created_at", "exit_reason", "exit_date"])


def book(conn: sqlite3.Connection) -> pd.DataFrame:
    """Every allocation with its closure (if any), in decision order."""
    return pd.read_sql_query(
        "SELECT a.*, c.created_at AS closed_at, c.exit_reason, c.exit_date, c.exit_price, "
        "c.net_r, c.pnl FROM allocations a LEFT JOIN closures c ON c.setup_id = a.setup_id "
        "ORDER BY a.decided_at, a.setup_id",
        conn,
    )


def open_positions(conn: sqlite3.Connection) -> pd.DataFrame:
    """Positions taken (sent or reduced) and not closed yet."""
    return pd.read_sql_query(
        "SELECT a.* FROM allocations a LEFT JOIN closures c ON c.setup_id = a.setup_id "
        "WHERE c.setup_id IS NULL AND a.state IN ('sent', 'reduced') "
        "ORDER BY a.decided_at, a.pair",
        conn,
    )


def _exits(
    setups: pd.DataFrame,
    scored: pd.DataFrame,
    no_price: dict[int, pd.Timestamp],
    now: pd.Timestamp,
) -> pd.DataFrame:
    """Known exits per setup id: the journal outcome; else no price for a trend setup the rules
    closed without one, or a barrier setup still without outcome ``BARRIER_GRACE`` after its
    time exit (its coin stopped trading)."""
    rows = {
        int(s.id): (pd.Timestamp(s.exit_date), s.exit_reason, s.exit_price, s.net_r)
        for s in scored.itertuples()
    }
    for sid, t in no_price.items():
        rows.setdefault(int(sid), (pd.Timestamp(t), "no_price", np.nan, np.nan))
    barrier = setups[setups["max_bars"].notna() & (setups["status"] == "setup")]
    for s in barrier.itertuples():
        end = pd.Timestamp(s.decided_at) + int(s.max_bars) * pd.Timedelta(s.timeframe)
        if int(s.id) not in rows and now >= end + BARRIER_GRACE:
            rows[int(s.id)] = (end, "no_price", np.nan, np.nan)
    return pd.DataFrame.from_dict(
        rows, orient="index", columns=["exit_date", "exit_reason", "exit_price", "net_r"]
    )


def _close(
    conn: sqlite3.Connection, exits: pd.DataFrame, until: pd.Timestamp, now: pd.Timestamp
) -> pd.DataFrame:
    """Close the open positions whose exit came at or before ``until``."""
    pos = open_positions(conn).set_index("setup_id")
    due = exits[exits.index.isin(pos.index)]
    due = due[due["exit_date"] <= until].sort_values("exit_date", kind="stable")
    if due.empty:
        return pd.DataFrame(columns=CLOSE_COLS)
    rows = pd.DataFrame(
        {
            "setup_id": due.index.astype(int),
            "exit_reason": due["exit_reason"].to_numpy(),
            "exit_date": [t.isoformat() for t in due["exit_date"]],
            "exit_price": due["exit_price"].to_numpy(dtype=float),
            "net_r": due["net_r"].to_numpy(dtype=float),
            "pnl": pos.loc[due.index, "risk"].to_numpy(dtype=float)
            * due["net_r"].to_numpy(dtype=float),
        }
    )
    record_closures(conn, rows, now)
    return rows


def update(
    conn: sqlite3.Connection,
    setups: pd.DataFrame,
    scored: pd.DataFrame,
    policy: RiskPolicy,
    book_start: pd.Timestamp,
    now: pd.Timestamp,
    no_price: dict[int, pd.Timestamp] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Bring the book up to ``now`` from the journal (``journal.setups`` and ``journal.scored``):
    the setups it has not seen, one decision time after the other, each after closing the
    positions whose exit came at or before it; then every exit known by ``now``. ``no_price``:
    trend setups the rules closed without a price (setup id -> exit time). Returns the new
    allocations and the new closures. The same journal always gives the same book."""
    exits = _exits(setups, scored, no_price or {}, now)
    seen = set(pd.read_sql_query("SELECT setup_id FROM allocations", conn)["setup_id"])
    new = setups[~setups["id"].isin(seen)]
    allocs, closes = [], []
    times = pd.to_datetime(new["decided_at"], utc=True)
    for t, batch in new.groupby(times, sort=True):
        closes.append(_close(conn, exits, pd.Timestamp(t), now))
        rows = allocate(batch, open_positions(conn), policy, book_start)
        record_allocations(conn, rows, policy.capital, now)
        allocs.append(rows)
    closes.append(_close(conn, exits, now, now))
    allocs = [a for a in allocs if len(a)]
    closes = [c for c in closes if len(c)]
    return (
        pd.concat(allocs, ignore_index=True) if allocs else pd.DataFrame(columns=ALLOC_COLS),
        pd.concat(closes, ignore_index=True) if closes else pd.DataFrame(columns=CLOSE_COLS),
    )


def open_risk(open_: pd.DataFrame) -> dict[str, float]:
    """Open risk by direction and in total."""
    long_ = float(open_.loc[open_["side"] > 0, "risk"].sum())
    short = float(open_.loc[open_["side"] < 0, "risk"].sum())
    return {"long": long_, "short": short, "total": long_ + short}
