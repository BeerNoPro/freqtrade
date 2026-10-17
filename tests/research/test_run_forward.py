"""Forward runner: several specs, trend exits and the product step (no network)."""

from pathlib import Path
from types import SimpleNamespace

import ccxt
import numpy as np
import pandas as pd
import pytest
import run_forward
from mce import journal
from mce.forward import SetupPolicy
from mce.journal import Setup
from run_forward import Runner, spec_strategies


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


START = pd.Timestamp("2026-10-12", tz="UTC")


def _trend_rows(strategy: str, pair: str, day: pd.Timestamp, exit_day=pd.NaT) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "strategy": [strategy],
            "pair": [pair],
            "timeframe": ["1d"],
            "candle_date": [day],
            "date": [day],
            "side": [1],
            "entry": [100.0],
            "atr": [20.0],
            "stop": [80.0],
            "target": [None],
            "max_bars": [None],
            "reason": ["trend"],
            "exit_date": [exit_day],
            "exit_price": [np.nan if pd.isna(exit_day) else 120.0],
        }
    )


def _spec(name: str, strategy: str, rows: pd.DataFrame, registered: bool = True):
    return SimpleNamespace(
        __name__=name,
        PIT=Path("pit"),
        POLICY=SetupPolicy(),
        LIVE_START=START,
        TOP_N=20,
        STRATEGIES=(strategy,),
        fingerprint=lambda: "fp",
        registered_fingerprint=lambda: "fp" if registered else None,
        setups_4h=lambda pit, end: pd.DataFrame(),
        setups_1d=lambda pit, end: rows,
    )


class NoQuote:
    def fapiPublicGetTickerBookTicker(self, params):
        raise ccxt.ExchangeError("offline")


def _multi(tmp_path: Path, specs: list) -> Runner:
    runner = Runner.__new__(Runner)
    runner.specs, runner.spec = specs, specs[0]
    runner.conn = journal.connect(tmp_path / "journal.sqlite")
    runner.pit, runner.fee, runner.ex = Path("pit"), 0.0012, NoQuote()
    runner.product, runner.no_price = None, {}
    runner.sync = lambda now, decision: []
    runner.funding = lambda pair, start, end: 0.0
    return runner


def test_spec_strategies_of_both_spec_styles() -> None:
    v1 = SimpleNamespace(
        REGISTRY=[SimpleNamespace(id="liq_4h@v1"), SimpleNamespace(id="shock_4h@v1")],
        STRATEGY_TREND="donchian_ls_1d@v1",
    )
    assert spec_strategies(v1) == {"liq_4h@v1", "shock_4h@v1", "donchian_ls_1d@v1"}
    assert spec_strategies(SimpleNamespace(STRATEGIES=("donchian_long_1d@v1",))) == {
        "donchian_long_1d@v1"
    }


def test_cycle_runs_every_spec_and_keeps_their_setups_apart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two trend rules on the same coin and candle: each records its own setup; the exit of one
    rule never closes the other's; an unregistered spec records nothing; each spec is reconciled
    against its own replay only."""
    monkeypatch.setattr(run_forward, "OUT", tmp_path)
    day = START + pd.Timedelta(days=1)
    a = _spec("spec_a", "trend_a_1d@v1", _trend_rows("trend_a_1d@v1", "ETHUSDT", day))
    b_rows = _trend_rows("trend_b_1d@v1", "ETHUSDT", day)
    b = _spec("spec_b", "trend_b_1d@v1", b_rows)
    off = _spec("spec_c", "trend_c_1d@v1", _trend_rows("trend_c_1d@v1", "ETHUSDT", day), False)
    runner = _multi(tmp_path, [a, b, off])
    now = day + pd.Timedelta(days=1, minutes=2)
    runner.cycle(now)
    assert sorted(journal.setups(runner.conn)["strategy"]) == ["trend_a_1d@v1", "trend_b_1d@v1"]
    # rule b exits two days later; rule a stays in
    b.setups_1d = lambda pit, end: _trend_rows(
        "trend_b_1d@v1", "ETHUSDT", day, day + pd.Timedelta(days=2)
    )
    runner.cycle(now + pd.Timedelta(days=2))
    assert journal.scored(runner.conn)["strategy"].tolist() == ["trend_b_1d@v1"]
    assert journal.pending(runner.conn)["strategy"].tolist() == ["trend_a_1d@v1"]
    assert not (tmp_path / "reconcile_issues.csv").exists()


def test_a_failing_product_layer_never_raises(tmp_path: Path) -> None:
    class Broken:
        def step(self, now, no_price):
            raise RuntimeError("boom")

        def cycle_failed(self, now, error):
            raise RuntimeError("boom")

    runner = _runner(tmp_path)
    runner.product = Broken()
    now = pd.Timestamp("2026-10-13 00:01", tz="UTC")
    runner.product_step(now)  # logged, not raised
    runner.cycle_failed(now, ValueError("x"))
