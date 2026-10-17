"""Portfolio risk budget of the product layer: bets, caps, trend slots, release at the exit."""

import hashlib
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from mce import journal, riskbook
from mce.riskbook import RiskPolicy


T0 = pd.Timestamp("2026-10-13 00:00", tz="UTC")
START = pd.Timestamp("2026-10-12", tz="UTC")
POLICY = RiskPolicy()
NO_OPEN = pd.DataFrame(columns=["setup_id", "pair", "side", "sleeve", "risk"])


def _row(i: int, **kw) -> dict:
    base = {
        "id": i,
        "strategy": "shock_4h@v1",
        "pair": f"C{i}USDT",
        "timeframe": "4h",
        "decided_at": T0.isoformat(),
        "side": -1,
        "entry": 100.0,
        "stop": 102.0,
        "leverage_max": 5,
        "status": "setup",
        "source": "live",
    }
    return {**base, **kw}


def _batch(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame(list(rows))


def _open(*rows: tuple[str, int, str, float]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"setup_id": 900 + k, "pair": p, "side": s, "sleeve": sl, "risk": r}
            for k, (p, s, sl, r) in enumerate(rows)
        ]
    )


def _by_pair(out: pd.DataFrame) -> pd.DataFrame:
    return out.set_index("pair")


def test_setups_of_one_close_and_direction_are_one_bet() -> None:
    """Four shorts on the same 4h close share one 1% bet; the long of that close is its own bet."""
    rows = [_row(i) for i in range(1, 5)] + [_row(5, side=1, stop=98.0, strategy="liq_4h@v1")]
    out = _by_pair(riskbook.allocate(_batch(*rows), NO_OPEN, POLICY, START))
    shorts = out.loc[[f"C{i}USDT" for i in range(1, 5)]]
    assert (shorts["state"] == "sent").all() and (shorts["bet_size"] == 4).all()
    assert shorts["risk"].to_numpy() == pytest.approx([0.0025] * 4)
    assert shorts["bet"].nunique() == 1
    long_ = out.loc["C5USDT"]
    assert long_["state"] == "sent" and long_["risk"] == pytest.approx(0.01)
    # size = capital x risk / |entry - stop|; open risk after the batch on every row
    assert shorts["qty"].iloc[0] == pytest.approx(10_000 * 0.0025 / 2.0)
    assert shorts["notional"].iloc[0] == pytest.approx(shorts["qty"].iloc[0] * 100.0)
    assert np.allclose(out["open_long"], 0.01) and np.allclose(out["open_short"], 0.01)


def test_side_cap_scales_a_bet_down_then_blocks_it() -> None:
    open_ = _open(("X1USDT", -1, "event", 0.046))
    out = riskbook.allocate(_batch(_row(1), _row(2)), open_, POLICY, START)
    assert (out["state"] == "reduced").all() and (out["limit_hit"] == "side").all()
    assert out["risk"].to_numpy() == pytest.approx([0.002, 0.002])
    full = _open(("X1USDT", -1, "event", 0.0495))
    out = riskbook.allocate(_batch(_row(1), _row(2)), full, POLICY, START)
    assert (out["state"] == "blocked").all() and (out["risk"] == 0).all()
    assert (out["qty"] == 0).all() and np.allclose(out["open_short"], 0.0495)


def test_total_cap_binds_before_the_side_cap() -> None:
    open_ = _open(("L1USDT", 1, "event", 0.04), ("S1USDT", -1, "event", 0.035))
    out = riskbook.allocate(_batch(_row(1, side=1, stop=98.0)), open_, POLICY, START).iloc[0]
    assert out["state"] == "reduced" and out["limit_hit"] == "total"
    assert out["risk"] == pytest.approx(0.005)


def test_trend_slot_one_trend_position_per_coin_and_trend_first() -> None:
    trend = {"timeframe": "1d", "side": 1, "stop": 80.0}
    rows = [
        _row(1, pair="ETHUSDT", strategy="donchian_ls_1d@v1", **trend),
        _row(2, pair="ETHUSDT", strategy="donchian_long_1d@v1", **trend),
        _row(3, pair="BTCUSDT", strategy="donchian_long_1d@v1", **trend),
        _row(4, pair="ETHUSDT", side=1, stop=98.0),  # event long on the same coin: allowed
    ]
    open_ = _open(("BTCUSDT", -1, "trend", 0.0025))
    out = riskbook.allocate(_batch(*rows), open_, POLICY, START).set_index("setup_id")
    # rules in name order: donchian_long_1d@v1 takes ETH, the other rule's ETH is a duplicate
    assert out.loc[2, "state"] == "sent" and out.loc[2, "risk"] == pytest.approx(0.0025)
    assert out.loc[1, "state"] == "duplicate" and out.loc[1, "risk"] == 0
    assert out.loc[3, "state"] == "duplicate"  # BTC already holds a trend position (short)
    assert out.loc[4, "state"] == "sent" and out.loc[4, "sleeve"] == "event"
    assert out.loc[4, "open_long"] == pytest.approx(0.0125)


def test_setups_not_taken_get_no_risk() -> None:
    rows = [
        _row(1, decided_at=(START - pd.Timedelta(hours=4)).isoformat()),
        _row(2, status="skipped_cost"),
        _row(3, source="live_late"),
        _row(4),
    ]
    out = riskbook.allocate(_batch(*rows), NO_OPEN, POLICY, START).set_index("setup_id")
    assert out.loc[[1, 2, 3], "state"].tolist() == ["pre_book", "skipped_cost", "late"]
    assert (out.loc[[1, 2, 3], "risk"] == 0).all()
    # the bet holds only the setup taken: it gets the whole 1%
    assert out.loc[4, "state"] == "sent" and out.loc[4, "bet_size"] == 1
    assert out.loc[4, "risk"] == pytest.approx(0.01)


def test_leverage_cap_limits_the_size_and_the_risk_taken() -> None:
    """Stop 0.1% away: 1% risk would need 10x; the journal's leverage cap (5x) bounds it."""
    out = riskbook.allocate(_batch(_row(1, stop=100.1)), NO_OPEN, POLICY, START).iloc[0]
    assert out["state"] == "sent" and out["granted_risk"] == pytest.approx(0.01)
    assert out["notional"] == pytest.approx(5 * 10_000)
    assert out["risk"] == pytest.approx(0.005)


def _setup(**kw) -> journal.Setup:
    base = {
        "strategy": "shock_4h@v1",
        "fingerprint": "f",
        "source": "live",
        "pair": "AUSDT",
        "timeframe": "4h",
        "candle_date": (T0 - pd.Timedelta(hours=4)).isoformat(),
        "decided_at": T0.isoformat(),
        "side": -1,
        "entry": 100.0,
        "atr": 2.0,
        "stop": 102.0,
        "target": 96.0,
        "max_bars": 3,
        "qty": 50.0,
        "notional": 5000.0,
        "leverage_max": 5,
        "cost_r": 0.06,
        "status": "setup",
        "reason": "test",
    }
    return journal.Setup(**{**base, **kw})


def _at(hours: int) -> dict:
    t = T0 + pd.Timedelta(hours=hours)
    return {"candle_date": (t - pd.Timedelta(hours=4)).isoformat(), "decided_at": t.isoformat()}


def _journal(path: Path) -> sqlite3.Connection:
    """Short AUSDT at T0 stopped at T0 + 8h; short BUSDT at T0 + 4h; short CUSDT at T0 + 8h."""
    conn = journal.connect(path)
    journal.record_setup(conn, _setup())
    journal.record_setup(conn, _setup(pair="BUSDT", **_at(4)))
    journal.record_setup(conn, _setup(pair="CUSDT", **_at(8)))
    stop = journal.outcome(-1, 100.0, 2.0, "stop", T0 + pd.Timedelta(hours=8), 102.0, 0.0, 0.0012)
    journal.record_outcome(conn, 1, stop)
    return conn


def test_update_frees_risk_at_the_exit_and_books_pnl(tmp_path: Path) -> None:
    j = _journal(tmp_path / "journal.sqlite")
    book = riskbook.connect(tmp_path / "product.sqlite")
    now = T0 + pd.Timedelta(hours=8, minutes=2)
    allocs, closes = riskbook.update(book, journal.setups(j), journal.scored(j), POLICY, START, now)
    a = allocs.set_index("pair")
    # B (T0 + 4h) still sees A open; C (T0 + 8h) decides after A's exit at T0 + 8h
    assert a.loc["BUSDT", "open_short"] == pytest.approx(0.02)
    assert a.loc["CUSDT", "open_short"] == pytest.approx(0.02)
    assert closes["setup_id"].tolist() == [1]
    c = closes.iloc[0]
    assert c["exit_reason"] == "stop" and c["net_r"] == pytest.approx(-1.06)
    assert c["pnl"] == pytest.approx(0.01 * -1.06)
    assert riskbook.open_positions(book)["pair"].tolist() == ["BUSDT", "CUSDT"]
    # nothing new: a second update adds no row
    again = riskbook.update(book, journal.setups(j), journal.scored(j), POLICY, START, now)
    assert again[0].empty and again[1].empty


def test_positions_without_a_price_close_as_no_price(tmp_path: Path) -> None:
    j = journal.connect(tmp_path / "journal.sqlite")
    journal.record_setup(j, _setup())  # barrier setup whose coin stops trading
    trend = {"timeframe": "1d", "target": None, "max_bars": None, "pair": "TUSDT", "stop": 130.0}
    journal.record_setup(j, _setup(**trend))
    book = riskbook.connect(tmp_path / "product.sqlite")
    exit_trend = T0 + pd.Timedelta(days=3)
    now = T0 + pd.Timedelta(days=4)
    _, closes = riskbook.update(
        book, journal.setups(j), journal.scored(j), POLICY, START, now, no_price={2: exit_trend}
    )
    c = closes.set_index("setup_id")
    assert c.loc[2, "exit_reason"] == "no_price" and c.loc[2, "exit_date"] == exit_trend.isoformat()
    assert pd.isna(c.loc[2, "pnl"]) and pd.isna(c.loc[2, "net_r"])
    # the barrier setup: 12h after the decision plus a 2-day grace without any outcome
    assert c.loc[1, "exit_reason"] == "no_price"
    assert c.loc[1, "exit_date"] == (T0 + pd.Timedelta(hours=12)).isoformat()
    assert riskbook.open_positions(book).empty


def test_update_is_deterministic_and_leaves_the_journal_untouched(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite"
    _journal(path).close()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    tables = []
    for name in ("a", "b"):
        j = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        book = riskbook.connect(tmp_path / f"{name}.sqlite")
        now = T0 + pd.Timedelta(days=1)
        riskbook.update(book, journal.setups(j), journal.scored(j), POLICY, START, now)
        tables.append(riskbook.book(book))
        j.close()
    pd.testing.assert_frame_equal(tables[0], tables[1])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_book_is_append_only_and_rejects_missing_values(tmp_path: Path) -> None:
    book = riskbook.connect(tmp_path / "product.sqlite")
    rows = riskbook.allocate(_batch(_row(1)), NO_OPEN, POLICY, START)
    assert riskbook.record_allocations(book, rows, POLICY.capital, T0) == 1
    assert riskbook.record_allocations(book, rows, POLICY.capital, T0) == 0  # one row per setup
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        book.execute("UPDATE allocations SET risk = 0")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        book.execute("DELETE FROM allocations")
    bad = rows.assign(setup_id=2, risk=np.nan)
    with pytest.raises(ValueError, match="risk"):
        riskbook.record_allocations(book, bad, POLICY.capital, T0)
