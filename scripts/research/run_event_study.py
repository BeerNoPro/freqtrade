"""
Run an in-sample event study for a hooks module and write a markdown report.

    .venv/Scripts/python.exe scripts/research/run_event_study.py \
        --hooks user_data/research/hooks_indicators_v1.py --start 2025-01-01 --end 2026-04-01

The hooks module must expose ``REGISTRY`` (an ``mce.hooks.HookRegistry``).
"""

import argparse
import importlib.util
import json
from pathlib import Path

import pandas as pd
from mce.data import btc_context, prepare_pair
from mce.event_study import StudyConfig, by_regime, run, summarize
from mce.hooks import HookRegistry
from mce.universe import DEFAULT_PAIRS


def load_registry(path: Path) -> HookRegistry:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.REGISTRY


def fmt(df: pd.DataFrame) -> str:
    return df.to_markdown(index=False, floatfmt=".3f") if len(df) else "_(empty)_"


def coverage(frames: dict[str, pd.DataFrame], cfg: StudyConfig) -> pd.DataFrame:
    rows = []
    for pair, df in frames.items():
        part = df[(df["date"] >= cfg.start) & (df["date"] < cfg.end)]
        rows.append(
            {
                "pair": pair,
                "first": df["date"].min().date() if len(df) else None,
                "candles": len(part),
                "zero_vol_pct": float((part["volume"] == 0).mean() * 100) if len(part) else None,
                "funding_pct": float(part["funding_rate"].notna().mean() * 100)
                if len(part)
                else None,
                "premium_pct": float(part["premium"].notna().mean() * 100) if len(part) else None,
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="MCE event study")
    parser.add_argument("--hooks", type=Path, required=True)
    parser.add_argument("--pairs", nargs="+", default=DEFAULT_PAIRS)
    parser.add_argument("--datadir", type=Path, default=Path("user_data/data/binance"))
    parser.add_argument("--start", default="2025-01-01")
    parser.add_argument("--end", default="2026-04-01", help="exclusive")
    parser.add_argument("--out", type=Path, default=Path("user_data/reports"))
    parser.add_argument("--tag", default="IS-v1")
    parser.add_argument("--timeframe", default="1h", choices=["1h", "4h"])
    parser.add_argument("--horizons", nargs="+", type=int, default=[1, 4, 12, 24])
    args = parser.parse_args()

    registry = load_registry(args.hooks)
    hooks = [h for h in registry if h.timeframes[0] == args.timeframe]
    missing = [h.id for h in hooks if h.horizon not in args.horizons]
    if missing:
        raise SystemExit(f"primary horizon not in --horizons for: {missing}")
    cfg = StudyConfig(
        start=pd.Timestamp(args.start, tz="UTC"),
        end=pd.Timestamp(args.end, tz="UTC"),
        horizons=tuple(args.horizons),
    )
    btc = btc_context(args.datadir, args.timeframe)
    frames = {p: prepare_pair(p, args.datadir, btc, args.timeframe) for p in args.pairs}
    frames = {p: df for p, df in frames.items() if len(df)}

    events = run(hooks, frames, cfg)
    summary = summarize(events, hooks, cfg)

    args.out.mkdir(parents=True, exist_ok=True)
    events.to_parquet(args.out / f"event-study-{args.tag}-events.parquet", index=False)
    summary.to_csv(args.out / f"event-study-{args.tag}-summary.csv", index=False)

    cols = ["hook", "horizon", "n", "hit_rate", "mean_ret_r", "lift_r", "lift_lo", "lift_hi"]
    cols += ["p_bh", "net_r", "year_share", "pair_share", "pass"]
    lines = [
        f"# Event study {args.tag}",
        "",
        f"- Range: {cfg.start.date()} -> {cfg.end.date()} (exclusive)",
        f"- Timeframe: {args.timeframe}, pairs: {len(frames)}",
        f"- Registry fingerprint (whole file): `{registry.fingerprint()}`",
        f"- Hooks: {', '.join(f'{h.id} ({h.fingerprint()})' for h in hooks)}",
        f"- Config: {json.dumps({k: str(v) for k, v in cfg.__dict__.items()})}",
        "- R = move / ATR14 at entry; lift = signed return minus same-pair same-regime mean;",
        "  CI = weekly block bootstrap; p_bh = Benjamini-Hochberg over all hook x horizon rows.",
        "",
        "## Summary (all horizons)",
        fmt(summary[[c for c in cols if c in summary]]) if len(summary) else "_(no events)_",
        "",
        "## Primary horizon: triple barrier",
    ]
    tb_cols = ["hook", "horizon", "n", "tb_tp", "tb_sl", "tb_net_r", "mfe_r", "mae_r", "fee_r"]
    if "tb_tp" in summary:
        lines.append(fmt(summary[summary["primary"]][tb_cols]))
    for hook in hooks:
        lines += ["", f"## {hook.id} — by regime (h={hook.horizon})", hook.hypothesis, ""]
        lines.append(fmt(by_regime(events, hook, hook.horizon)) if len(events) else "_(none)_")
    lines += ["", "## Data coverage", fmt(coverage(frames, cfg))]
    report = args.out / f"event-study-{args.tag}.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"report: {report}")
    print(summary[[c for c in cols if c in summary]].to_string(index=False))


if __name__ == "__main__":
    main()
