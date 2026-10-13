"""Product layer step on a journal: book, messages, outbox, weekly summary (no network)."""

import json
from dataclasses import replace
from pathlib import Path

import pandas as pd
from mce import journal, outbox
from mce.product import Product, ProductConfig
from mce.riskbook import RiskPolicy


T0 = pd.Timestamp("2026-10-13 00:00", tz="UTC")
MIN = pd.Timedelta(minutes=2)
RULES = {
    "liquidation_bounce_4h@v1": {
        "name": "(b) mua sau thanh lý 4h",
        "evidence": ["Khối 1: 82 setup"],
        "milestone": {"events": 200, "before_live": 82, "deadline": "2028-01-01"},
    },
    "shock_no_oi_drop_short_4h@v1": {
        "name": "(c) bán theo cú sập 4h",
        "milestone": {"events": 200, "before_live": 62, "deadline": "2028-01-01"},
    },
}
CFG = ProductConfig(
    policy=RiskPolicy(), book_start=pd.Timestamp("2026-10-12", tz="UTC"), rules=RULES
)


def _setup(**kw) -> journal.Setup:
    base = {
        "strategy": "shock_no_oi_drop_short_4h@v1",
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
        "reason": "cú sập",
    }
    return journal.Setup(**{**base, **kw})


def _journal(path: Path):
    """T0: four shorts (c), one long (b), one trend setup recorded late."""
    conn = journal.connect(path)
    for pair in ("AUSDT", "BUSDT", "CUSDT", "DUSDT"):
        journal.record_setup(conn, _setup(pair=pair))
    journal.record_setup(
        conn,
        _setup(strategy="liquidation_bounce_4h@v1", pair="EUSDT", side=1, stop=98.0, target=104.0),
    )
    trend = {"strategy": "donchian_ls_1d@v1", "timeframe": "1d", "target": None, "max_bars": None}
    journal.record_setup(conn, _setup(pair="TUSDT", source="live_late", stop=130.0, **trend))
    return conn


def _stop_all(conn) -> None:
    for s in journal.pending(conn).itertuples():
        if pd.isna(s.max_bars):
            continue
        o = journal.outcome(
            s.side,
            s.entry,
            abs(s.entry - s.stop),
            "stop",
            T0 + pd.Timedelta(hours=4),
            s.stop,
            0.0,
            0.0012,
        )
        journal.record_outcome(conn, int(s.id), o)


def test_step_posts_one_message_per_setup_and_never_twice(tmp_path: Path) -> None:
    j = _journal(tmp_path / "journal.sqlite")
    p = Product(j, tmp_path / "product.sqlite", CFG, tmp_path / "messages.log")
    done = p.step(T0 + MIN)
    assert done == {"allocated": 6, "closed": 0, "messages": 6}
    box = outbox.rows(p.db).set_index("key")
    assert sorted(box["kind"]) == ["setup"] * 5 + ["setup_info"]
    assert (box["state"] == "local").all()  # Telegram off: kept locally, never sent later
    log = (tmp_path / "messages.log").read_text(encoding="utf-8")
    assert log.count("THỬ NGHIỆM - luật chưa đạt kiểm định") == 5
    assert "1/4 của một cược 1%: 4 lệnh SHORT cùng nến" in log
    assert "[KHÔNG VÀO: ghi trễ, quá cửa sổ quyết định] SHORT TUSDT" in log
    assert "- Mốc: 200 sự kiện sau IS (83/200), hạn 2028-01-01" in log
    assert p.step(T0 + 2 * MIN) == {"allocated": 0, "closed": 0, "messages": 0}


def test_closes_then_one_weekly_summary(tmp_path: Path) -> None:
    j = _journal(tmp_path / "journal.sqlite")
    p = Product(j, tmp_path / "product.sqlite", CFG, tmp_path / "messages.log")
    p.step(T0 + MIN)
    _stop_all(j)
    done = p.step(T0 + pd.Timedelta(hours=4) + MIN)
    assert done["closed"] == 5 and done["messages"] == 5
    closes = outbox.rows(p.db)
    closes = closes[closes["kind"] == "close"]
    assert len(closes) == 5 and closes["text"].str.contains("Chạm SL").all()
    long_close = closes[closes["key"] == "close:5"]["text"].iloc[0]
    assert long_close.endswith("Sổ: rủi ro 1.00% → -1.06% vốn (-106 USDT)")
    monday = pd.Timestamp("2026-10-19 00:00", tz="UTC") + MIN
    assert p.step(monday)["messages"] == 1
    weekly = outbox.rows(p.db).set_index("key").loc["weekly:2026-W42"]
    assert weekly["kind"] == "weekly"
    assert "tuần -2.12% (-212 USDT)" in weekly["text"]
    assert p.step(monday + MIN)["messages"] == 0


def test_sendable_messages_wait_for_delivery_and_info_stays_local(tmp_path: Path) -> None:
    j = _journal(tmp_path / "journal.sqlite")
    cfg = replace(CFG, telegram_send=True)
    p = Product(j, tmp_path / "product.sqlite", cfg, tmp_path / "messages.log", sender=object())
    p.step(T0 + MIN)
    box = outbox.rows(p.db).set_index("kind")
    assert (box.loc["setup", "state"] == "pending").all()
    assert box.loc["setup_info", "state"] == "local"


def _alerts(p: Product) -> dict[str, str]:
    box = outbox.rows(p.db)
    return box[box["kind"] == "alert"].set_index("key")["text"].to_dict()


def test_late_cycle_alerts_once_and_not_after_an_on_time_run(tmp_path: Path) -> None:
    p = Product(
        journal.connect(tmp_path / "j.sqlite"), tmp_path / "p.sqlite", CFG, tmp_path / "m.log"
    )
    d0, d1 = T0, T0 + pd.Timedelta(hours=4)
    assert p.checks(d0 + pd.Timedelta(hours=3), d0) == 0  # first cycle ever: nothing to compare
    assert p.checks(d1 + MIN, d1) == 0  # on time
    assert p.checks(d1 + pd.Timedelta(hours=2), d1) == 0  # restart after an on-time run
    d2 = d1 + pd.Timedelta(hours=8)  # the 8h close was missed, the 12h one runs 50 min late
    assert p.checks(d2 + pd.Timedelta(minutes=50), d2) == 1
    assert p.checks(d2 + pd.Timedelta(minutes=55), d2) == 0  # same cycle: one alert
    text = _alerts(p)[f"alert:late:{d2.isoformat()}"]
    assert text.startswith(
        "CẢNH BÁO VẬN HÀNH · runner chạy chu kỳ 2026-10-13 12:00 UTC trễ 50 phút"
    )
    assert "chu kỳ trước chạy lúc 2026-10-13 06:00 UTC" in text


def test_recorder_heartbeat_and_coverage_alerts(tmp_path: Path) -> None:
    live = tmp_path / "live"
    live.mkdir()
    now = pd.Timestamp("2026-10-15 00:02", tz="UTC")
    beat = int((now - pd.Timedelta(minutes=40)).timestamp() * 1000)
    (live / ".heartbeat").write_text(str(beat), encoding="utf-8")
    asked = []

    def coverage(outdir: Path, days: list[str]) -> pd.DataFrame:
        asked.append(days)
        return pd.DataFrame(
            {"stream": ["book_ticker", "depth"], "day": days * 2, "coverage": [0.95, 0.42]}
        )

    j = journal.connect(tmp_path / "j.sqlite")
    p = Product(
        j, tmp_path / "p.sqlite", CFG, tmp_path / "m.log", recorder_dir=live, coverage=coverage
    )
    assert p.checks(now, now.floor("4h")) == 2
    alerts = _alerts(p)
    assert (
        "bộ ghi dữ liệu không chạy (nhịp tim cuối 2026-10-14 23:22 UTC)"
        in alerts["alert:recorder_down:2026-10-15T00"]
    )
    assert alerts["alert:coverage:2026-10-14"].endswith("2026-10-14: depth 42% (cần ≥ 90%)")
    assert p.checks(now + MIN, now.floor("4h")) == 0 and asked == [["2026-10-14"]]


def test_coverage_is_not_checked_for_days_before_the_book(tmp_path: Path) -> None:
    live = tmp_path / "live"
    live.mkdir()
    now = pd.Timestamp("2026-10-13 00:02", tz="UTC")  # yesterday 10-12 starts with the book
    (live / ".heartbeat").write_text(str(int(now.timestamp() * 1000)), encoding="utf-8")
    cfg = replace(CFG, book_start=pd.Timestamp("2026-10-12 16:00", tz="UTC"))

    def coverage(outdir: Path, days: list[str]) -> pd.DataFrame:
        raise AssertionError("not asked")

    j = journal.connect(tmp_path / "j.sqlite")
    p = Product(
        j, tmp_path / "p.sqlite", cfg, tmp_path / "m.log", recorder_dir=live, coverage=coverage
    )
    assert p.checks(now, now.floor("4h")) == 0


def test_reconcile_fingerprint_and_cycle_failure_alerts(tmp_path: Path) -> None:
    p = Product(
        journal.connect(tmp_path / "j.sqlite"), tmp_path / "p.sqlite", CFG, tmp_path / "m.log"
    )
    assert p.checks(T0 + MIN, T0, issues=2, registered=False) == 2
    alerts = _alerts(p)
    assert "lệch bản chạy lại ở 2 setup" in alerts["alert:reconcile:2026-10-13"]
    assert "fingerprint" in alerts["alert:fingerprint:2026-10-13"]
    err = IndexError("single positional indexer is out-of-bounds")
    assert p.cycle_failed(T0 + MIN, err) == 1
    assert p.cycle_failed(T0 + pd.Timedelta(hours=4), err) == 0  # same error within 6 hours
    assert p.cycle_failed(T0 + pd.Timedelta(hours=6), err) == 1


def test_flush_delivers_through_the_sender(tmp_path: Path) -> None:
    class Sender:
        def __init__(self) -> None:
            self.sent: list[str] = []

        def send(self, text: str) -> None:
            self.sent.append(text)

    j = _journal(tmp_path / "journal.sqlite")
    sender = Sender()
    cfg = replace(CFG, telegram_send=True)
    p = Product(j, tmp_path / "product.sqlite", cfg, tmp_path / "messages.log", sender=sender)
    p.step(T0 + MIN)
    assert p.flush(T0 + MIN, sleep=lambda s: None)["sent"] == 5
    assert len(sender.sent) == 5 and all("THỬ NGHIỆM" in t for t in sender.sent)
    off = Product(j, tmp_path / "other.sqlite", CFG, tmp_path / "other.log")
    assert off.flush(T0 + MIN) == {}


def test_weekly_operations_line(tmp_path: Path) -> None:
    live = tmp_path / "live"
    live.mkdir()

    def coverage(outdir: Path, days: list[str]) -> pd.DataFrame:
        cov = [0.5 if d == "2026-10-14" else 0.99 for d in days]
        return pd.DataFrame({"stream": ["depth"] * len(days), "day": days, "coverage": cov})

    j = journal.connect(tmp_path / "j.sqlite")
    p = Product(
        j, tmp_path / "p.sqlite", CFG, tmp_path / "m.log", recorder_dir=live, coverage=coverage
    )
    start = pd.Timestamp("2026-10-12", tz="UTC")
    for k in range(42):
        d = start + k * pd.Timedelta(hours=4)
        p.db.execute("INSERT INTO cycles VALUES (?, ?)", (d.isoformat(), (d + MIN).isoformat()))
    ops = p.ops(start, start + pd.Timedelta(days=7))
    assert ops == {
        "on_time": 42,
        "expected": 42,
        "recorder_good": 6,
        "recorder_days": 7,
        "recorder_worst": "depth 50% ngày 2026-10-14",
    }


def test_config_loads_from_json(tmp_path: Path) -> None:
    raw = {
        "capital": 5000,
        "risk": {"event_bet": 0.01, "trend_slot": 0.0025, "max_side": 0.05, "max_total": 0.08},
        "book_start": "2026-10-14T00:00:00+00:00",
        "telegram_send": False,
        "rules": RULES,
    }
    path = tmp_path / "product.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    cfg = ProductConfig.load(path)
    assert cfg.policy.capital == 5000 and cfg.policy.min_setup == 0.001
    assert cfg.book_start == pd.Timestamp("2026-10-14", tz="UTC")
    assert cfg.send_kinds == ("setup", "close", "weekly", "alert")
    assert cfg.rules["shock_no_oi_drop_short_4h@v1"]["milestone"]["before_live"] == 62
