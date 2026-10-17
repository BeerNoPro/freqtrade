"""Forward setup journal: append-only, scored once, reconciled against a replay."""

import sqlite3
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest
from mce import journal


BAR = pd.Timedelta(hours=4)


def _setup(**kw) -> journal.Setup:
    base = {
        "strategy": "x_4h@v1",
        "fingerprint": "abc",
        "source": "live",
        "pair": "AUSDT",
        "timeframe": "4h",
        "candle_date": "2026-01-01T00:00:00+00:00",
        "decided_at": "2026-01-01T04:00:00+00:00",
        "side": 1,
        "entry": 100.0,
        "atr": 2.0,
        "stop": 98.0,
        "target": 104.0,
        "max_bars": 3,
        "qty": 50.0,
        "notional": 5000.0,
        "leverage_max": 5,
        "cost_r": 0.06,
        "status": "setup",
        "reason": "test",
    }
    return journal.Setup(**{**base, **kw})


Rows = list[tuple[float, float, float]]


def _candles(rows: Rows | dict[str, Rows]) -> journal.CandleFetcher:
    """Candles (high, low, close) after the decision candle, the same for every pair or per pair."""

    def fetch(pair: str, tf: str, after: str, n: int) -> pd.DataFrame:
        own = rows[pair] if isinstance(rows, dict) else rows
        start = pd.Timestamp(after) + BAR
        dates = pd.date_range(start, periods=len(own), freq="4h")
        df = pd.DataFrame(own, columns=["high", "low", "close"]).assign(date=dates)
        return df.head(n)

    return fetch


def _no_funding(pair: str, start: pd.Timestamp, end: pd.Timestamp) -> float:
    return 0.0


def test_journal_is_append_only(tmp_path: Path) -> None:
    conn = journal.connect(tmp_path / "j.sqlite")
    assert journal.record_setup(conn, _setup()) is True
    assert journal.record_setup(conn, _setup(entry=101.0)) is False  # one per strategy/pair/candle
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE setups SET entry = 1")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM setups")
    with pytest.raises(ValueError):
        journal.record_setup(conn, _setup(candle_date="x", stop=101.0))  # stop above a long
    with pytest.raises(ValueError):
        journal.record_setup(conn, _setup(candle_date="y", target=99.0))  # target below a long


def test_barrier_scoring_waits_for_closed_candles_and_counts_stop_first(tmp_path: Path) -> None:
    conn = journal.connect(tmp_path / "j.sqlite")
    journal.record_setup(conn, _setup())
    # one quiet candle closed: neither barrier hit, time exit not reached -> not scored yet
    assert journal.score_barriers(conn, _candles([(101, 99, 100.5)]), _no_funding, BAR, 0.0) == 0
    # second candle touches both the stop and the target -> stop
    path = _candles([(101, 99, 100.5), (105, 97, 103), (106, 102, 105)])
    assert journal.score_barriers(conn, path, _no_funding, BAR, 0.0012) == 1
    assert journal.score_barriers(conn, path, _no_funding, BAR, 0.0012) == 0  # scored once
    row = journal.scored(conn).iloc[0]
    assert row["exit_reason"] == "stop" and row["gross_r"] == pytest.approx(-1.0)
    assert row["exit_date"] == "2026-01-01T12:00:00+00:00"  # close of the second candle
    assert row["fee_r"] == pytest.approx(0.0012 * 100 / 2)
    assert row["net_r"] == pytest.approx(-1.0 - 0.06)


def test_barrier_scoring_skips_a_setup_without_any_candle_after_it(tmp_path: Path) -> None:
    """Right after the decision no candle has closed after the entry yet (the cycle that records a
    setup also scores): the setup waits, the older ones are still scored."""
    conn = journal.connect(tmp_path / "j.sqlite")
    journal.record_setup(conn, _setup(pair="OLDUSDT"))
    journal.record_setup(conn, _setup(pair="NEWUSDT", candle_date="2026-01-02T00:00:00+00:00"))
    path = _candles({"OLDUSDT": [(105, 97, 103)], "NEWUSDT": []})
    assert journal.score_barriers(conn, path, _no_funding, BAR, 0.0) == 1
    assert journal.pending(conn)["pair"].tolist() == ["NEWUSDT"]


def test_short_target_time_exit_and_funding(tmp_path: Path) -> None:
    conn = journal.connect(tmp_path / "j.sqlite")
    short = _setup(side=-1, stop=102.0, target=96.0)
    journal.record_setup(conn, short)
    journal.record_setup(conn, _setup(pair="BUSDT"))  # long that runs out of time
    seen = []

    def funding(pair: str, start: pd.Timestamp, end: pd.Timestamp) -> float:
        seen.append((pair, start, end))
        return 0.001

    path = _candles(
        {
            "AUSDT": [(100.5, 99, 99.5), (100, 95.5, 96.5), (101, 99, 100.8)],
            "BUSDT": [(100.5, 99, 99.5), (101, 98.5, 100.2), (101, 99, 100.8)],
        }
    )
    assert journal.score_barriers(conn, path, funding, BAR, 0.0) == 2
    rows = journal.scored(conn).set_index("pair")
    a, b = rows.loc["AUSDT"], rows.loc["BUSDT"]
    assert a["exit_reason"] == "target" and a["gross_r"] == pytest.approx(2.0)
    assert a["funding_r"] == pytest.approx(-0.001 * 100 / 2)  # a short receives positive funding
    assert b["exit_reason"] == "time" and b["gross_r"] == pytest.approx(0.4)
    assert b["funding_r"] == pytest.approx(0.05)
    assert (
        "BUSDT",
        pd.Timestamp("2026-01-01 04:00", tz="UTC"),
        pd.Timestamp("2026-01-01 16:00", tz="UTC"),
    ) in seen


def test_skipped_and_rule_exit_setups_are_not_barrier_scored(tmp_path: Path) -> None:
    conn = journal.connect(tmp_path / "j.sqlite")
    journal.record_setup(conn, _setup(status="skipped_cost"))
    journal.record_setup(conn, _setup(pair="TREND", target=None, max_bars=None))
    path = _candles([(101, 99, 100.5)] * 3)
    assert journal.score_barriers(conn, path, _no_funding, BAR, 0.0) == 0
    trend_id = int(journal.pending(conn)["id"].iloc[0])
    o = journal.outcome(
        1, 100.0, 2.0, "rule", pd.Timestamp("2026-02-01", tz="UTC"), 110.0, 0.0, 0.0
    )
    assert journal.record_outcome(conn, trend_id, o) is True
    assert journal.record_outcome(conn, trend_id, o) is False
    assert journal.scored(conn)["gross_r"].iloc[0] == pytest.approx(5.0)


def test_reconcile_flags_every_disagreement() -> None:
    base = [_setup(pair=p) for p in ("A", "B", "C", "D")]
    live = pd.DataFrame([s.__dict__ for s in base[:3]])
    replay = pd.DataFrame(
        [replace(base[0]).__dict__, replace(base[1], stop=97.9).__dict__, base[3].__dict__]
    )
    issues = journal.reconcile(live, replay).set_index("pair")["issue"].to_dict()
    assert issues == {"B": "stop differs", "C": "only live", "D": "only replay"}
    assert journal.reconcile(live, live).empty
