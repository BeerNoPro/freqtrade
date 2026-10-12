"""Forward runner: product message format (no network)."""

from mce.journal import Setup
from run_forward import LABEL, message


def _setup(**kw) -> Setup:
    base = {
        "strategy": "liquidation_bounce_4h@v1",
        "fingerprint": "f",
        "source": "live",
        "pair": "SOLUSDT",
        "timeframe": "4h",
        "candle_date": "2026-10-12T12:00:00+00:00",
        "decided_at": "2026-10-12T16:00:00+00:00",
        "side": 1,
        "entry": 100.0,
        "atr": 2.0,
        "stop": 98.0,
        "target": 104.0,
        "max_bars": 3,
        "qty": 50.0,
        "notional": 5000.0,
        "leverage_max": 5,
        "cost_r": 0.07,
        "status": "setup",
        "reason": "Mua sau thanh lý",
    }
    return Setup(**{**base, **kw})


def test_message_carries_label_levels_size_and_reason() -> None:
    text = message(_setup())
    assert text.startswith(LABEL) and "BỎ QUA" not in text
    assert "LONG SOLUSDT" in text and "SL 98 (-2.0%)" in text and "TP 104 (+4.0%)" in text
    assert "thoát sau 12h" in text and "5,000 USDT" in text and "5x isolated" in text
    assert "Kỳ vọng: chưa kiểm định" in text and text.endswith("Lý do: Mua sau thanh lý")


def test_message_of_a_trend_setup_and_a_skipped_one() -> None:
    trend = message(_setup(side=-1, stop=110.0, target=None, max_bars=None, timeframe="1d"))
    assert "SHORT SOLUSDT" in trend and "trailing stop" in trend and "SL 110 (+10.0%)" in trend
    assert "BỎ QUA" in message(_setup(status="skipped_cost", cost_r=0.3))
