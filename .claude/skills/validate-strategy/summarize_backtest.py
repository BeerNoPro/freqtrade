"""Summarize a Freqtrade backtest result and check it against the project's pass gates.

Usage:
    .venv/Scripts/python.exe .claude/skills/validate-strategy/summarize_backtest.py \
        [result_file_or_dir] [--strategy NAME]

Without arguments the latest result in user_data/backtest_results is used.
R-multiples are approximated as profit_ratio / |initial_stop_loss_ratio| (both are
relative to stake, leverage included), so they are only meaningful when the initial
stoploss equals the risk the strategy intended for the trade.
"""

import argparse
import sys
from pathlib import Path

from freqtrade.data.btanalysis import load_backtest_stats


GATES = {
    "min_trades": 150,
    "min_profit_factor": 1.3,
    "min_expectancy_r": 0.2,
    "max_drawdown_r": 15.0,
    "max_pair_share": 0.30,
}


def r_multiples(trades: list[dict]) -> list[float]:
    out = []
    for t in trades:
        risk = abs(t.get("initial_stop_loss_ratio") or 0.0)
        if risk > 0:
            out.append((t.get("profit_ratio") or 0.0) / risk)
    return out


def max_drawdown_r(rs: list[float]) -> float:
    peak = cum = mdd = 0.0
    for r in rs:
        cum += r
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    return mdd


def verdict(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", default="user_data/backtest_results")
    parser.add_argument("--strategy", default=None)
    args = parser.parse_args()

    stats = load_backtest_stats(Path(args.path))
    strategies = stats.get("strategy", {})
    if not strategies:
        print("No strategy results found.")
        return 1
    name = args.strategy or next(iter(strategies))
    s = strategies[name]
    trades = sorted(s.get("trades", []), key=lambda t: t.get("close_date", ""))

    total = s["total_trades"]
    pf = s.get("profit_factor") or 0.0
    rs = r_multiples(trades)
    exp_r = sum(rs) / len(rs) if rs else 0.0
    mdd_r = max_drawdown_r(rs)

    pairs = [p for p in s.get("results_per_pair", []) if p.get("key") != "TOTAL"]
    total_profit = sum(p["profit_total_abs"] for p in pairs if p["profit_total_abs"] > 0)
    top_pair = max(pairs, key=lambda p: p["profit_total_abs"], default=None)
    top_share = (
        top_pair["profit_total_abs"] / total_profit if top_pair and total_profit > 0 else 0.0
    )

    print(f"Strategy: {name}   Period: {s['backtest_start']} -> {s['backtest_end']}")
    print(
        f"Trades: {total} (long {s['trade_count_long']}, short {s['trade_count_short']})  "
        f"Winrate: {s.get('winrate', 0):.1%}  Profit: {s['profit_total']:.2%}  "
        f"Max DD (account): {s.get('max_drawdown_account', 0):.2%}"
    )
    print(f"Profit long: {s['profit_total_long']:.2%}  Profit short: {s['profit_total_short']:.2%}")
    print("\nPer entry tag:")
    for tag in s.get("results_per_enter_tag", []):
        if tag.get("key") == "TOTAL":
            continue
        print(
            f"  {str(tag['key']):30s} trades {tag['trades']:5d}  "
            f"profit {tag['profit_total_abs']:12.2f}  winrate {tag.get('winrate', 0):.1%}"
        )

    print("\nGates:")
    rows = [
        ("Trades", f"{total}", f">= {GATES['min_trades']}", total >= GATES["min_trades"]),
        (
            "Profit factor",
            f"{pf:.2f}",
            f">= {GATES['min_profit_factor']}",
            pf >= GATES["min_profit_factor"],
        ),
        (
            "Expectancy (R)",
            f"{exp_r:+.2f}",
            f">= +{GATES['min_expectancy_r']}",
            exp_r >= GATES["min_expectancy_r"],
        ),
        (
            "Max drawdown (R)",
            f"{mdd_r:.1f}",
            f"<= {GATES['max_drawdown_r']}",
            mdd_r <= GATES["max_drawdown_r"],
        ),
        (
            "Top pair share",
            f"{top_share:.0%} ({top_pair['key'] if top_pair else '-'})",
            f"<= {GATES['max_pair_share']:.0%}",
            top_share <= GATES["max_pair_share"],
        ),
    ]
    for label, value, target, ok in rows:
        print(f"  {verdict(ok)}  {label:18s} {value:>22s}   target {target}")
    if not rs:
        print("  NOTE: no initial_stop_loss_ratio in trades -> R metrics unavailable")
    passed = all(r[3] for r in rows)
    print(f"\nOverall: {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 3


if __name__ == "__main__":
    sys.exit(main())
