"""Market data recorder: row parsing, storage, compaction, scheduling and WebSocket reconnects."""

import asyncio
import json
from pathlib import Path

import pandas as pd
import pytest
import websockets
from mce import recorder
from mce.recorder import (
    PartWriter,
    beat,
    compact_all,
    coverage,
    depth_rows,
    force_order_row,
    next_run,
    option_series,
    read_day,
    record_ws,
    snapshot_rows,
    top_symbols,
)


# a real message of the !forceOrder@arr stream (2026-10-11)
SAMPLE = {
    "e": "forceOrder",
    "E": 1791712687186,
    "o": {
        "s": "CFXUSDT",
        "S": "BUY",
        "o": "LIMIT",
        "f": "IOC",
        "q": "688",
        "p": "0.0691900",
        "ap": "0.0685859",
        "X": "FILLED",
        "l": "412",
        "z": "688",
        "T": 1791712686180,
        "st": 1,
    },
}
DAY_MS = 1791676800000  # 2026-10-11 00:00 UTC


def test_force_order_row_renames_and_keeps_unknown_fields() -> None:
    row = force_order_row(SAMPLE, 5)
    assert row["received"] == 5 and row["event_time"] == SAMPLE["E"]
    assert row["symbol"] == "CFXUSDT" and row["side"] == "BUY" and row["avg_price"] == "0.0685859"
    assert row["filled_qty"] == "688" and row["trade_time"] == 1791712686180
    assert row["o_st"] == 1  # a field added by the exchange is kept, not dropped


def test_snapshot_and_depth_rows() -> None:
    payload = [{"symbol": "BTCUSDT", "bidPrice": "1"}, {"symbol": "ETHUSDT", "bidPrice": "2"}]
    assert snapshot_rows(payload, 7) == [{"taken": 7, **p} for p in payload]
    assert [r["symbol"] for r in snapshot_rows(payload, 7, ["ETHUSDT"])] == ["ETHUSDT"]
    book = {
        "lastUpdateId": 9,
        "E": 1,
        "T": 2,
        "bids": [["10", "1"], ["9", "2"]],
        "asks": [["11", "3"]],
    }
    rows = depth_rows("BTCUSDT", book, 7)
    assert [(r["side"], r["level"], r["price"]) for r in rows] == [
        ("bid", 0, "10"),
        ("bid", 1, "9"),
        ("ask", 0, "11"),
    ]
    assert rows[0]["last_update_id"] == 9 and rows[0]["taken"] == 7


def test_top_symbols_and_option_series() -> None:
    info = {
        "symbols": [
            {"symbol": "BTCUSDT", "status": "TRADING", "contractType": "PERPETUAL"},
            {"symbol": "ETHUSDT", "status": "TRADING", "contractType": "PERPETUAL"},
            {"symbol": "OLDUSDT", "status": "SETTLING", "contractType": "PERPETUAL"},
            {"symbol": "BTCUSDT_261225", "status": "TRADING", "contractType": "CURRENT_QUARTER"},
            {"symbol": "USDCUSDT", "status": "TRADING", "contractType": "PERPETUAL"},
            {
                "symbol": "TSLAUSDT",
                "status": "TRADING",
                "contractType": "PERPETUAL",
                "underlyingType": "EQUITY",
                "underlyingSubType": ["TradFi"],
            },
        ]
    }
    tickers = [
        {"symbol": s, "quoteVolume": str(v)}
        for s, v in [("BTCUSDT", 5), ("ETHUSDT", 9), ("OLDUSDT", 99), ("USDCUSDT", 50)]
        + [("BTCUSDT_261225", 70), ("TSLAUSDT", 80)]
    ]
    assert top_symbols(tickers, info, 5) == ["ETHUSDT", "BTCUSDT"]
    assert top_symbols(tickers, info, 1) == ["ETHUSDT"]
    options = {
        "optionSymbols": [
            {"symbol": "BTC-261225-50000-P"},
            {"symbol": "BTC-261225-90000-C"},
            {"symbol": "ETH-261030-3000-C"},
        ]
    }
    assert option_series(options) == [("BTC", "261225"), ("ETH", "261030")]


def test_next_run_is_on_the_grid_and_strictly_later() -> None:
    assert next_run(60.0, 60, 2.0) == 62.0
    assert next_run(62.0, 60, 2.0) == 122.0  # a boundary that just passed is not reused
    assert next_run(61.9, 60, 2.0) == 62.0
    assert next_run(3599.0, 300, 2.0) == 3602.0


def test_writer_and_compaction_round_trip(tmp_path: Path) -> None:
    writer = PartWriter(tmp_path)
    rows = [{"taken": DAY_MS - 1000 + i, "symbol": "BTCUSDT", "bidPrice": "1.5"} for i in range(3)]
    writer.append("book_ticker", rows[:2], DAY_MS - 3_600_000)  # 2026-10-10 23:00
    writer.append("book_ticker", rows[2:], DAY_MS - 7_200_000)  # 2026-10-10 22:00
    writer.append("book_ticker", rows, DAY_MS + 60_000)  # today: must not be compacted
    part = tmp_path / "book_ticker" / "2026-10-10" / "part-23.jsonl"
    with part.open("a", encoding="utf-8") as f:
        f.write('{"taken": 1, "sym')  # a line cut by a crash
    writer.gap("liquidations", 1, 2, "reconnected")

    assert compact_all(tmp_path, "2026-10-11") == {"book_ticker": 3}
    out = pd.read_parquet(tmp_path / "book_ticker" / "2026-10-10.parquet")
    assert len(out) == 3 and out["taken"].is_monotonic_increasing
    assert out["bidPrice"].dtype == float and out["symbol"].tolist() == ["BTCUSDT"] * 3
    assert not (tmp_path / "book_ticker" / "2026-10-10").exists()
    assert (tmp_path / "book_ticker" / "2026-10-11" / "part-00.jsonl").exists()
    assert compact_all(tmp_path, "2026-10-11") == {}  # idempotent
    gaps = (tmp_path / "_gaps.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(gaps[0]) == {
        "stream": "liquidations",
        "start": 1,
        "end": 2,
        "reason": "reconnected",
    }


def _rows(folder: Path) -> int:
    return sum(len(p.read_text(encoding="utf-8").splitlines()) for p in folder.rglob("*.jsonl"))


async def test_record_ws_reconnects_and_logs_the_gap(tmp_path: Path) -> None:
    connections = []

    async def server_side(ws) -> None:
        connections.append(ws)
        if len(connections) == 1:
            await ws.send(json.dumps(SAMPLE))
            await ws.send(json.dumps([SAMPLE, SAMPLE]))  # array payloads are split
            return  # closing the connection forces a reconnect
        await ws.send(json.dumps(SAMPLE))
        await ws.wait_closed()

    async with websockets.serve(server_side, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        writer = PartWriter(tmp_path)
        task = asyncio.create_task(
            record_ws(f"ws://127.0.0.1:{port}", "liquidations", writer, backoff=(0.01, 0.05))
        )
        for _ in range(250):
            await asyncio.sleep(0.02)
            if _rows(tmp_path / "liquidations") >= 4:
                break
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert _rows(tmp_path / "liquidations") == 4 and len(connections) == 2
    gaps = [json.loads(x) for x in (tmp_path / "_gaps.jsonl").read_text().splitlines()]
    assert [g["reason"] for g in gaps] == ["reconnected"] and gaps[0]["end"] >= gaps[0]["start"]


async def test_sleep_until_follows_the_wall_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"t": 1000.0, "asleep": 1}
    naps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        naps.append(seconds)
        # the machine sleeps through the first nap: the wall clock jumps by an hour
        clock["t"] += 3600.0 if clock["asleep"] else seconds
        clock["asleep"] = 0

    monkeypatch.setattr(recorder.time, "time", lambda: clock["t"])
    monkeypatch.setattr(recorder.asyncio, "sleep", fake_sleep)
    await recorder.sleep_until(1000.0 + 600.0)
    assert naps == [30.0]  # one short step, then the wall clock shows the target has passed
    naps.clear()
    await recorder.sleep_until(clock["t"] + 45.0)
    assert naps == [30.0, 15.0]


def test_coverage_counts_snapshots_gaps_and_compaction(tmp_path: Path) -> None:
    writer = PartWriter(tmp_path)
    # yesterday (2026-10-10): book_ticker every minute from 12:00 to 17:59, compacted
    start = DAY_MS - 12 * 3_600_000
    for m in range(360):
        t = start + m * 60_000
        writer.append("book_ticker", [{"taken": t, "symbol": "BTCUSDT"}], t)
    compact_all(tmp_path, "2026-10-11")
    # liquidations: first row at 12:00 yesterday, a 60-minute outage logged at 15:00
    writer.append("liquidations", [{"received": start, "symbol": "X"}], start)
    writer.gap("all", start + 3 * 3_600_000, start + 4 * 3_600_000, "machine asleep")
    # today: 30 snapshots in the first hour, still in part files
    for m in range(30):
        writer.append("book_ticker", [{"taken": DAY_MS + m * 60_000}], DAY_MS + m * 60_000)
    cov = coverage(tmp_path, at_ms=DAY_MS + 3_600_000).set_index(["stream", "day"])
    y = cov.loc[("book_ticker", "2026-10-10")]
    assert y["snapshots"] == 360 and y["coverage"] == pytest.approx(0.25) and y["hours"] == 6
    assert y["state"] == "compacted" and not y["late_compaction"] and y["gap_min"] == 60
    t = cov.loc[("book_ticker", "2026-10-11")]
    assert t["coverage"] == pytest.approx(0.5) and t["state"] == "open"  # 30 of 60 minutes
    liq = cov.loc[("liquidations", "2026-10-10")]
    # 12 hours before the first row + 1 hour outage missing out of 24
    assert liq["coverage"] == pytest.approx(11 / 24) and liq["late_compaction"]


def test_coverage_of_selected_days_keeps_the_first_day_rule(tmp_path: Path) -> None:
    """Only the days asked for are read; the stream's first day is still the first one ever (the
    time before its first row counts as missing only there)."""
    writer, hour = PartWriter(tmp_path), 3_600_000
    day1, day2 = DAY_MS - 24 * hour, DAY_MS
    writer.append("liquidations", [{"received": day1 + 6 * hour, "symbol": "X"}], day1 + 6 * hour)
    writer.append("liquidations", [{"received": day2 + 6 * hour, "symbol": "X"}], day2 + 6 * hour)
    at = day2 + 24 * hour
    second = coverage(tmp_path, at_ms=at, days=["2026-10-11"])
    assert second["day"].tolist() == ["2026-10-11"] and second["coverage"].iloc[0] == 1.0
    first = coverage(tmp_path, at_ms=at, days=["2026-10-10"])
    assert first["coverage"].iloc[0] == pytest.approx(18 / 24)
    assert coverage(tmp_path, at_ms=at, days=[]).empty


def test_coverage_counts_overlapping_gaps_once(tmp_path: Path) -> None:
    writer, hour = PartWriter(tmp_path), 3_600_000
    day1, day2 = DAY_MS - 24 * hour, DAY_MS
    # first day: first row at 06:00, the machine slept 03:00-09:00 (overlaps the time before it)
    writer.append("liquidations", [{"received": day1 + 6 * hour, "symbol": "X"}], day1 + 6 * hour)
    writer.gap("all", day1 + 3 * hour, day1 + 9 * hour, "machine asleep")
    # second day: one sleep logged by the heartbeat (all) and by the WebSocket reconnect, plus a
    # separate 30-minute reconnect
    writer.append("liquidations", [{"received": day2, "symbol": "X"}], day2)
    writer.gap("all", day2 + 3 * hour, day2 + 9 * hour, "machine asleep")
    writer.gap("liquidations", day2 + 2 * hour, day2 + 9 * hour + hour // 2, "reconnected")
    writer.gap("liquidations", day2 + 12 * hour, day2 + 12 * hour + hour // 2, "reconnected")
    writer.gap("book_ticker", day2 + 4 * hour, day2 + 5 * hour, "request failed")
    cov = coverage(tmp_path, at_ms=day2 + 24 * hour).set_index(["stream", "day"])
    first = cov.loc[("liquidations", "2026-10-10")]
    assert first["coverage"] == pytest.approx(15 / 24)  # 00:00-09:00 missing, once
    second = cov.loc[("liquidations", "2026-10-11")]
    assert second["coverage"] == pytest.approx(16 / 24)  # 02:00-09:30 and 12:00-12:30
    assert second["gap_min"] == 480


def test_read_day_merges_compacted_and_open_rows(tmp_path: Path) -> None:
    writer = PartWriter(tmp_path)
    t = DAY_MS - 3_600_000  # 2026-10-10 23:00
    row = {"symbol": "BTCUSDT", "bidPrice": "99.9", "askPrice": "100.1"}
    writer.append("book_ticker", [{"taken": t, **row}], t)
    compact_all(tmp_path, "2026-10-11")
    writer.append("book_ticker", [{"taken": t + 60_000, **row, "bidPrice": "100.0"}], t + 60_000)
    df = read_day(tmp_path, "book_ticker", "2026-10-10")  # one parquet row + one part row
    assert list(df["taken"]) == [t, t + 60_000]
    assert list(df["bidPrice"]) == [99.9, 100.0] and df["askPrice"].dtype == float
    assert read_day(tmp_path, "book_ticker", "2026-10-09").empty


def test_heartbeat_logs_a_long_silence_as_a_gap(tmp_path: Path) -> None:
    writer, path = PartWriter(tmp_path), tmp_path / ".heartbeat"
    beat(path, writer, 1_000_000, 120_000)  # first beat ever: nothing to compare
    beat(path, writer, 1_060_000, 120_000)  # one minute later: fine
    assert not (tmp_path / "_gaps.jsonl").exists()
    beat(path, writer, 1_400_000, 120_000)  # machine slept / recorder stopped
    gap = json.loads((tmp_path / "_gaps.jsonl").read_text(encoding="utf-8"))
    assert (gap["stream"], gap["start"], gap["end"]) == ("all", 1_060_000, 1_400_000)
    assert path.read_text(encoding="utf-8") == "1400000"
