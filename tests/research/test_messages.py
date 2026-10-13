"""Product messages: setup, not taken, close, evidence line, weekly summary, alert."""

import numpy as np
import pandas as pd
from mce import messages
from mce.riskbook import RiskPolicy


T0 = pd.Timestamp("2026-10-13 00:00", tz="UTC")
POLICY = RiskPolicy()
RULE = {
    "name": "(c) bán theo cú sập 4h",
    "validated": False,
    "evidence": ["Khối 1 (2026-04→10): 62 setup, +0.00R/setup (CI -0.27→+0.29)"],
    "milestone": {"events": 200, "before_live": 62, "deadline": "2028-01-01"},
}


def _setup(**kw) -> pd.Series:
    base = {
        "id": 7,
        "strategy": "shock_no_oi_drop_short_4h@v1",
        "pair": "DOGEUSDT",
        "timeframe": "4h",
        "candle_date": (T0 - pd.Timedelta(hours=4)).isoformat(),
        "decided_at": T0.isoformat(),
        "side": -1,
        "entry": 0.0822,
        "stop": 0.0837373,
        "target": 0.0791254,
        "max_bars": 3,
        "leverage_max": 5,
        "cost_r": 0.06,
        "status": "setup",
        "source": "live",
        "reason": "Bán theo cú sập không kèm thanh lý: nến 4h -2.2 ATR, OI z -1.4",
    }
    return pd.Series({**base, **kw})


def _alloc(**kw) -> pd.Series:
    base = {
        "setup_id": 7,
        "sleeve": "event",
        "bet_size": 4,
        "state": "sent",
        "limit_hit": "",
        "base_risk": 0.0025,
        "granted_risk": 0.0025,
        "risk": 0.0025,
        "qty": 16262.6,
        "notional": 1336.8,
        "open_long": 0.01,
        "open_short": 0.01,
    }
    return pd.Series({**base, **kw})


def _scored(values: list[float], start: pd.Timestamp = T0) -> pd.DataFrame:
    dates = [start + pd.Timedelta(days=3 * i) for i in range(len(values))]
    return pd.DataFrame({"decided_at": [d.isoformat() for d in dates], "net_r": values})


def test_setup_message_of_a_shared_bet() -> None:
    lines = messages.evidence_lines(RULE, pd.DataFrame(index=range(4)), _scored([]), T0)
    text = messages.setup_text(_setup(), _alloc(), RULE, lines, POLICY, None)
    assert text.startswith(messages.LABEL)
    assert "SHORT DOGEUSDT · (c) bán theo cú sập 4h [shock_no_oi_drop_short_4h@v1]" in text
    assert "Nến 4h đóng 2026-10-13 00:00 UTC" in text
    assert "SL 0.0837373 (+1.9%)" in text and "TP 0.0791254 (-3.7%)" in text
    assert "thoát sau 12h nếu chưa chạm" in text
    assert "rủi ro 0.25% vốn mẫu 10,000 USDT (1/4 của một cược 1%: 4 lệnh SHORT cùng nến)" in text
    assert "đòn bẩy tối đa 5x isolated" in text and "Chi phí ước tính 0.06R" in text
    assert "Kỳ vọng: chưa tới mốc, không kết luận." in text
    assert "- Khối 1 (2026-04→10): 62 setup" in text
    assert "- Trực tiếp: chưa có setup đóng" in text
    assert "- Mốc: 200 sự kiện sau IS (66/200), hạn 2028-01-01" in text
    assert "Rủi ro mở sau chu kỳ: LONG 1.00%/5% · SHORT 1.00%/5% · tổng 2.00%/8%" in text
    assert text.endswith("Lý do: Bán theo cú sập không kèm thanh lý: nến 4h -2.2 ATR, OI z -1.4")


def test_setup_message_of_a_reduced_trend_setup_with_a_hedge_note() -> None:
    trend = _setup(timeframe="1d", side=1, stop=0.07, target=None, max_bars=None)
    alloc = _alloc(sleeve="trend", bet_size=1, state="reduced", limit_hit="total")
    alloc["base_risk"], alloc["granted_risk"], alloc["risk"] = 0.0025, 0.0015, 0.0015
    text = messages.setup_text(trend, alloc, {}, [], POLICY, "SHORT DOGEUSDT (sự kiện)")
    assert "LONG DOGEUSDT · shock_no_oi_drop_short_4h@v1" in text  # no rule config: the id
    assert "không TP · thoát khi tín hiệu xu hướng về 0 ở giá đóng nến ngày" in text
    assert "rủi ro 0.15% vốn mẫu 10,000 USDT (giảm từ 0.25% do trần tổng 8%)" in text
    assert "Lưu ý: sổ đang có SHORT DOGEUSDT (sự kiện) cùng coin, cần chế độ hedge" in text


def test_validated_rule_drops_the_label() -> None:
    text = messages.setup_text(_setup(), _alloc(), {**RULE, "validated": True}, [], POLICY, None)
    assert messages.LABEL not in text and text.startswith("SETUP")


def test_setup_not_taken_is_one_line() -> None:
    blocked = _alloc(state="blocked", limit_hit="side", risk=0.0, qty=0.0, notional=0.0)
    text = messages.not_taken_text(_setup(), blocked, RULE, POLICY)
    assert text.startswith("[KHÔNG VÀO: hết ngân sách rủi ro, trần SHORT 5%] SHORT DOGEUSDT")
    assert "\n" not in text
    late = messages.not_taken_text(_setup(), _alloc(state="late"), RULE, POLICY)
    assert late.startswith("[KHÔNG VÀO: ghi trễ, quá cửa sổ quyết định]")
    cost = messages.not_taken_text(_setup(cost_r=0.31), _alloc(state="skipped_cost"), RULE, POLICY)
    assert cost.startswith("[KHÔNG VÀO: chi phí 0.31R > 0.20R]")


def test_close_message_and_no_price() -> None:
    outcome = pd.Series(
        {
            "exit_reason": "stop",
            "exit_date": (T0 + pd.Timedelta(hours=8)).isoformat(),
            "exit_price": 0.0837373,
            "gross_r": -1.0,
            "fee_r": 0.064,
            "funding_r": -0.004,
            "net_r": -1.06,
            "pnl": 0.0025 * -1.06,
        }
    )
    text = messages.close_text(_setup(), _alloc(), outcome, RULE, POLICY)
    assert text.startswith("THỬ NGHIỆM · ĐÓNG SHORT DOGEUSDT · (c) bán theo cú sập 4h")
    assert "Chạm SL · vào 0.0822 (2026-10-13 00:00 UTC) → ra 0.0837373" in text
    assert "(2026-10-13 08:00 UTC), giữ 8h" in text
    assert "R gộp -1.00 · phí -0.06 · funding +0.00 → R ròng -1.06" in text
    assert text.endswith("Sổ: rủi ro 0.25% → -0.27% vốn (-26 USDT)")
    gone = outcome.copy()
    gone["exit_reason"], gone["exit_price"], gone["net_r"], gone["pnl"] = "no_price", *[np.nan] * 3
    text = messages.close_text(_setup(), _alloc(), gone, RULE, POLICY)
    assert "Coin ngừng giao dịch: không có giá thoát, không tính lãi/lỗ" in text


def test_evidence_lines_show_a_ci_from_ten_closed_setups_and_a_date_milestone() -> None:
    rule = {"evidence": [], "milestone": {"date": "2027-04-01"}}
    scored = _scored(list(np.linspace(-0.5, 1.5, 12)))
    lines = messages.evidence_lines(rule, pd.DataFrame(), scored, T0)
    assert lines[1].startswith("- Trực tiếp: 12 setup đã đóng, TB +0.50R (CI ")
    assert lines[2] == "- Mốc: 2027-04-01 (còn 170 ngày)"
    few = messages.evidence_lines(rule, pd.DataFrame(), _scored([-1.0, 0.5]), T0)
    assert few[1] == "- Trực tiếp: 2 setup đã đóng, TB -0.25R"


def test_weekly_summary() -> None:
    week = (pd.Timestamp("2026-10-12", tz="UTC"), pd.Timestamp("2026-10-19", tz="UTC"))
    book = pd.DataFrame(
        {
            "strategy": ["shock_no_oi_drop_short_4h@v1"] * 3,
            "decided_at": [T0.isoformat()] * 3,
            "side": [-1, -1, -1],
            "state": ["sent", "sent", "blocked"],
            "risk": [0.0025, 0.0025, 0.0],
            "exit_date": [(T0 + pd.Timedelta(hours=8)).isoformat(), None, None],
            "pnl": [-0.00265, np.nan, np.nan],
        }
    )
    journal_setups = pd.DataFrame(
        {"strategy": ["shock_no_oi_drop_short_4h@v1"] * 3, "decided_at": [T0.isoformat()] * 3}
    )
    scored = pd.DataFrame(
        {
            "strategy": ["shock_no_oi_drop_short_4h@v1"],
            "decided_at": [T0.isoformat()],
            "exit_date": [(T0 + pd.Timedelta(hours=8)).isoformat()],
            "net_r": [-1.06],
        }
    )
    ops = {"on_time": 40, "expected": 42, "recorder_good": 5, "recorder_days": 7}
    rules = {"shock_no_oi_drop_short_4h@v1": RULE}
    text = messages.weekly_text(week, book, journal_setups, scored, rules, POLICY, ops)
    assert text.startswith("TỔNG KẾT TUẦN 2026-W42 (12/10 → 18/10 UTC) · THỬ NGHIỆM")
    assert "Chưa luật nào tới mốc: số liệu để theo dõi, không kết luận." in text
    assert "Sổ (vốn mẫu 10,000 USDT): tuần -0.27% (-26 USDT) · lũy kế -0.27%" in text
    assert "sụt giảm lớn nhất -0.27%" in text
    assert "Đang mở 1: LONG 0.00%/5% · SHORT 0.25%/5% · tổng 0.25%/8%" in text
    assert "(c) bán theo cú sập 4h: mới 3 (vào 2, giảm 0, chặn 1, trễ 0, bỏ qua 0)" in text
    assert "đóng 1: -1.06R" in text and "mốc 200 sự kiện sau IS (65/200)" in text
    assert text.endswith("Vận hành: 40/42 chu kỳ đúng giờ · bộ ghi đủ ≥ 90%: 5/7 ngày")


def test_alert_text() -> None:
    assert messages.alert_text("bộ ghi ngừng").startswith("CẢNH BÁO VẬN HÀNH · bộ ghi ngừng")
