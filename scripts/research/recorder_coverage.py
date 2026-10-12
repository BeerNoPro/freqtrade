"""
Coverage of the market recorder (``mce.recorder.coverage``): per stream and UTC day, the share
of the day recorded, hours with data, logged gaps, size and whether past days were compacted
into parquet. The summary gives, per stream, the share of days recorded at >= 90% since the
first day, the condition the recorder studies were registered with.

    .venv/Scripts/python.exe scripts/research/recorder_coverage.py [--days 14] [--outdir ...]
"""

import argparse
from pathlib import Path

import pandas as pd
from mce.recorder import coverage


REPO = Path(__file__).resolve().parents[2]
GOOD = 0.90


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--outdir", type=Path, default=REPO / "user_data" / "research" / "live")
    parser.add_argument("--days", type=int, default=14, help="days shown in the detail table")
    args = parser.parse_args()
    cov = coverage(args.outdir)
    if cov.empty:
        print("no recorder data")
        return
    last = sorted(cov["day"].unique())[-args.days :]
    detail = cov[cov["day"].isin(last)].pivot(index="day", columns="stream", values="coverage")
    print("Share of each UTC day recorded (today: of the hours elapsed):")
    print(detail.to_string(float_format=lambda x: f"{x:.0%}"))
    full = cov[cov["day"] < pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d")]
    summary = cov.groupby("stream").agg(
        days=("day", "nunique"),
        mean_coverage=("coverage", "mean"),
        size_mb=("size_mb", "sum"),
        gap_min=("gap_min", "sum"),
    )
    summary["full_days_ge_90"] = full.groupby("stream")["coverage"].apply(
        lambda c: (c >= GOOD).sum()
    )
    print("\nPer stream since the first day:")
    print(summary.to_string(float_format=lambda x: f"{x:.2f}"))
    late = cov[cov["late_compaction"]]
    print(f"\nTotal size {cov['size_mb'].sum():.0f} MB over {cov['day'].nunique()} days")
    if len(late):
        print("Past days not compacted yet (next housekeeping hour does it):")
        print(late[["stream", "day", "state"]].to_string(index=False))
    else:
        print("Every past day is compacted into parquet.")


if __name__ == "__main__":
    main()
