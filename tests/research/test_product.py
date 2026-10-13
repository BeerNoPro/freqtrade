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
