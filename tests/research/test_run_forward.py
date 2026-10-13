"""Forward runner: trend exits and the product step (no network)."""

from pathlib import Path

import numpy as np
import pandas as pd
from mce import journal
from mce.journal import Setup
from run_forward import Runner


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


def _runner(tmp_path: Path) -> Runner:
    runner = Runner.__new__(Runner)
    runner.conn = journal.connect(tmp_path / "journal.sqlite")
    runner.pit, runner.fee = tmp_path / "pit", 0.0012
    runner.product, runner.no_price = None, {}
    return runner


def test_close_trend_leaves_a_setup_without_exit_price_pending(tmp_path: Path) -> None:
    """A coin that stops trading while a trend setup is open has no exit price (the store drops
    its fake candles): the setup stays without outcome instead of failing every cycle, and the
    product layer is told to close it without a price."""
    runner = _runner(tmp_path)
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
    assert runner.no_price == {1: day + pd.Timedelta(days=3)}


def test_a_failing_product_step_never_raises(tmp_path: Path) -> None:
    class Broken:
        def step(self, now, no_price):
            raise RuntimeError("boom")

    runner = _runner(tmp_path)
    runner.product = Broken()
    runner.product_step(pd.Timestamp("2026-10-13 00:01", tz="UTC"))  # logged, not raised
