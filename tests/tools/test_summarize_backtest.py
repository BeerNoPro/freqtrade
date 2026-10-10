import importlib.util
import sys
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).parents[2] / ".claude" / "skills" / "validate-strategy" / "summarize_backtest.py"
)
_spec = importlib.util.spec_from_file_location("summarize_backtest", SCRIPT)
summarize = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(summarize)


def make_trades(results: list[float], risk: float = 0.02, pairs=("BTC/USDT:USDT",)) -> list[dict]:
    """One trade per R result; profit_ratio = R * risk (initial stop at -risk)."""
    return [
        {
            "pair": pairs[i % len(pairs)],
            "profit_ratio": r * risk,
            "initial_stop_loss_ratio": -risk,
            "close_date": f"2025-01-01 00:{i // 60:02d}:{i % 60:02d}",
        }
        for i, r in enumerate(results)
    ]


def make_stats(trades: list[dict], profit_factor: float, per_pair: dict[str, float]) -> dict:
    return {
        "strategy": {
            "TestStrat": {
                "trades": trades,
                "total_trades": len(trades),
                "profit_factor": profit_factor,
                "results_per_pair": [
                    *({"key": k, "profit_total_abs": v} for k, v in per_pair.items()),
                    {"key": "TOTAL", "profit_total_abs": sum(per_pair.values())},
                ],
                "results_per_enter_tag": [],
                "backtest_start": "2025-01-01",
                "backtest_end": "2025-06-01",
                "trade_count_long": len(trades),
                "trade_count_short": 0,
                "winrate": 0.5,
                "profit_total": 0.1,
                "profit_total_long": 0.1,
                "profit_total_short": 0.0,
                "max_drawdown_account": 0.05,
            }
        }
    }


def run_main(monkeypatch, stats) -> int:
    monkeypatch.setattr(summarize, "load_backtest_stats", lambda path: stats)
    monkeypatch.setattr(sys, "argv", ["summarize_backtest.py", "somewhere"])
    return summarize.main()


def test_r_multiples():
    trades = [
        {"profit_ratio": 0.04, "initial_stop_loss_ratio": -0.02},
        {"profit_ratio": -0.02, "initial_stop_loss_ratio": -0.02},
        {"profit_ratio": 0.01, "initial_stop_loss_ratio": None},  # no risk -> skipped
        {"profit_ratio": 0.01, "initial_stop_loss_ratio": 0},
    ]
    assert summarize.r_multiples(trades) == pytest.approx([2.0, -1.0])


@pytest.mark.parametrize(
    "rs,expected",
    [
        ([], 0.0),
        ([1, 2, 3], 0.0),
        ([1, -2, -1, 3], 3.0),
        ([-1, -1, 5, -4], 4.0),
    ],
)
def test_max_drawdown_r(rs, expected):
    assert summarize.max_drawdown_r(rs) == expected


def test_all_gates_pass(monkeypatch, capsys):
    pairs = ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "XRP/USDT:USDT")
    trades = make_trades([2.0, -1.0] * 100, pairs=pairs)  # 200 trades, +0.5R each on average
    stats = make_stats(trades, 1.8, {p: 100.0 for p in pairs})
    assert run_main(monkeypatch, stats) == 0
    out = capsys.readouterr().out
    assert "Overall: PASS" in out
    assert "FAIL" not in out


def test_too_few_trades_fails(monkeypatch, capsys):
    pairs = ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "XRP/USDT:USDT")
    trades = make_trades([2.0, -1.0] * 10, pairs=pairs)
    assert run_main(monkeypatch, make_stats(trades, 1.8, {p: 10.0 for p in pairs})) == 3
    out = capsys.readouterr().out
    assert "FAIL  Trades" in out
    assert "Overall: FAIL" in out


def test_drawdown_and_concentration_fail(monkeypatch, capsys):
    # 20 losers in a row -> 20R drawdown; one pair makes 80% of the profit
    trades = make_trades([-1.0] * 20 + [3.0] * 180)
    stats = make_stats(trades, 2.0, {"BTC/USDT:USDT": 800.0, "ETH/USDT:USDT": 200.0})
    assert run_main(monkeypatch, stats) == 3
    out = capsys.readouterr().out
    assert "FAIL  Max drawdown (R)" in out
    assert "FAIL  Top pair share" in out
    assert "PASS  Trades" in out


def test_missing_stop_information_is_reported(monkeypatch, capsys):
    trades = [{"pair": "BTC/USDT:USDT", "profit_ratio": 0.01, "close_date": "x"}] * 200
    stats = make_stats(trades, 1.5, {"BTC/USDT:USDT": 1.0, "ETH/USDT:USDT": 1.0})
    assert run_main(monkeypatch, stats) == 3
    out = capsys.readouterr().out
    assert "R metrics unavailable" in out
    assert "FAIL  Expectancy (R)" in out


def test_no_strategy_results(monkeypatch, capsys):
    assert run_main(monkeypatch, {"strategy": {}}) == 1
    assert "No strategy results found." in capsys.readouterr().out
