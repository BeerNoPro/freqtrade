"""
Collect short-history Binance futures data (OI, long/short ratios, taker volume).

    .venv/Scripts/python.exe scripts/research/collect_derivs.py
    .venv/Scripts/python.exe scripts/research/collect_derivs.py --periods 1h --pairs BTC/USDT:USDT
"""

import argparse
import logging
from pathlib import Path

from mce.collector import ENDPOINTS, collect
from mce.universe import DEFAULT_PAIRS


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--pairs", nargs="+", default=DEFAULT_PAIRS)
    parser.add_argument("--periods", nargs="+", default=["1h"])
    parser.add_argument("--fast-pairs", nargs="*", default=["BTC/USDT:USDT", "ETH/USDT:USDT"])
    parser.add_argument("--fast-period", default="5m")
    parser.add_argument("--endpoints", nargs="+", choices=list(ENDPOINTS))
    parser.add_argument("--outdir", type=Path, default=Path("user_data/research/derivs"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    added = collect(args.pairs, args.periods, args.outdir, args.endpoints)
    if args.fast_pairs:
        fast = collect(args.fast_pairs, [args.fast_period], args.outdir, args.endpoints)
        for key, n in fast.items():
            added[key] = added.get(key, 0) + n
    for key, n in sorted(added.items()):
        print(f"{key:20s} +{n} rows")


if __name__ == "__main__":
    main()
