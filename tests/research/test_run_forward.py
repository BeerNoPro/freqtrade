"""Forward runner: product message format and trend exits (no network)."""

from pathlib import Path

import numpy as np
import pandas as pd
from mce import journal
from mce.journal import Setup
from run_forward import LABEL, Runner, message


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


def test_close_trend_leaves_a_setup_without_exit_price_pending(tmp_path: Path) -> None:
    """A coin that stops trading while a trend setup is open has no exit price (the store drops
    its fake candles): the setup stays without outcome instead of failing every cycle."""
    runner = Runner.__new__(Runner)
    runner.conn = journal.connect(tmp_path / "journal.sqlite")
    runner.pit, runner.fee = tmp_path / "pit", 0.0012
    trend = {"timeframe": "1d", "target": None, "max_bars": None, "side": -1, "stop": 110.0}
    for pair in ("DEADUSDT", "LIVEUSDT"):
        journal.record_setup(runner.conn, _setup(**trend, pair=pair))
    day = pd.Timestamp("2026-10-12 12:00", tz="UTC")
    trades = pd.DataFrame(
        {
            "pair": ["DEADUSDT", "LIVEUSDT"],
            "candle_date": [day, day],
            "exit_date": [day + pd.Timedelta(days=2)] * 2,
            "exit_price": [np.nan, 90.0],
        }
    )
    assert runner.close_trend(trades) == 1
    assert journal.pending(runner.conn)["pair"].tolist() == ["DEADUSDT"]
