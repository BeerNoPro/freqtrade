"""
Texts of the product messages (docs/signal-bot-workflow.md, section 5): a setup taken, a setup not
taken (one line, log only), a close, the weekly summary and an operational alert.

The evidence line ("Kỳ vọng") states what is known about the rule so far: the published
development and block-1 results (copied from the reports into ``user_data/research/
product.json``), the closed setups of the live journal (mean net R, weekly-cluster CI from
``MIN_CI`` setups) and the progress to the pre-registered milestone. Before the milestone it says
that no conclusion is drawn.

Pure functions of journal rows (``mce.journal.setups`` / ``scored``), book rows
(``mce.riskbook``) and the rule's entry of the product config (``name``, ``validated``,
``evidence``, ``milestone``: ``{events, before_live, deadline}`` or ``{date}``).
"""

import numpy as np
import pandas as pd

from mce.riskbook import TAKEN, RiskPolicy
from mce.stats import cluster_bootstrap_mean


LABEL = "THỬ NGHIỆM - luật chưa đạt kiểm định, không giao dịch theo"
VALIDATED = "SETUP (luật đã qua kiểm định)"
MAX_COST_R = 0.20  # mce.forward.SetupPolicy.max_cost_r: costlier setups are skipped
MIN_CI = 10  # closed setups before a CI is shown
SIDE = {1: "LONG", -1: "SHORT"}
EXITS = {
    "target": "Chạm TP",
    "stop": "Chạm SL",
    "time": "Hết giờ",
    "rule": "Luật trend thoát (tín hiệu về 0)",
    "no_price": "Coin ngừng giao dịch: không có giá thoát, không tính lãi/lỗ",
}
NOT_TAKEN = {
    "late": "ghi trễ, quá cửa sổ quyết định",
    "duplicate": "coin đã có vị thế trend trong sổ",
}


def utc(t) -> str:
    return pd.Timestamp(t).strftime("%Y-%m-%d %H:%M UTC")


def title(strategy: str, rule: dict) -> str:
    name = rule.get("name")
    return f"{name} [{strategy}]" if name else strategy


def header(rule: dict) -> str:
    return VALIDATED if rule.get("validated") else LABEL


def risk_line(open_long: float, open_short: float, policy: RiskPolicy) -> str:
    side, total = f"{policy.max_side:.0%}", f"{policy.max_total:.0%}"
    return (
        f"LONG {open_long:.2%}/{side} · SHORT {open_short:.2%}/{side} · "
        f"tổng {open_long + open_short:.2%}/{total}"
    )


def _cap(limit: str, side: int, policy: RiskPolicy) -> str:
    return (
        f"{SIDE[side]} {policy.max_side:.0%}" if limit == "side" else f"tổng {policy.max_total:.0%}"
    )


# ---------------------------------------------------------------- evidence


def live_stats(scored: pd.DataFrame) -> dict[str, float]:
    """Closed setups of a rule in the live journal: n, mean net R, weekly-cluster CI (from
    ``MIN_CI`` setups, NaN before)."""
    r = pd.to_numeric(scored["net_r"], errors="coerce") if len(scored) else pd.Series(dtype=float)
    r = r[np.isfinite(r)]
    out = {"n": len(r), "mean": float(r.mean()) if len(r) else np.nan, "lo": np.nan, "hi": np.nan}
    if len(r) >= MIN_CI:
        weeks = pd.to_datetime(scored.loc[r.index, "decided_at"], utc=True).dt.strftime("%G-%V")
        ci = cluster_bootstrap_mean(r.to_numpy(), weeks.to_numpy())
        out["lo"], out["hi"] = ci["lo"], ci["hi"]
    return out


def progress(rule: dict, setups: pd.DataFrame, now: pd.Timestamp) -> str:
    """Progress to the milestone: events counted after IS (before the live journal + journal
    setups of the rule), or the date."""
    m = rule.get("milestone") or {}
    if "events" in m:
        k = int(m.get("before_live", 0)) + len(setups)
        return f"{m['events']} sự kiện sau IS ({k}/{m['events']}), hạn {m.get('deadline', '?')}"
    if "date" in m:
        left = (pd.Timestamp(m["date"], tz="UTC") - now.floor("D")).days
        return f"{m['date']} (còn {left} ngày)"
    return "chưa đặt"


def progress_value(
    rule: dict, setups: pd.DataFrame, now: pd.Timestamp
) -> tuple[float | None, str] | None:
    """(share of the way to the milestone, label) for a progress bar: events counted after IS,
    or time elapsed between the milestone's ``from`` and its ``date`` (no share without
    ``from``). None without a milestone."""
    m = rule.get("milestone") or {}
    if "events" in m:
        k = int(m.get("before_live", 0)) + len(setups)
        return k / m["events"], f"{k}/{m['events']} sự kiện, hạn {m.get('deadline', '?')}"
    if "date" in m:
        end = pd.Timestamp(m["date"], tz="UTC")
        label = f"tới {m['date']} (còn {(end - now.floor('D')).days} ngày)"
        if "from" not in m:
            return None, label
        start = pd.Timestamp(m["from"], tz="UTC")
        return float((now - start) / (end - start)), label
    return None


def evidence_lines(
    rule: dict, setups: pd.DataFrame, scored: pd.DataFrame, now: pd.Timestamp
) -> list[str]:
    """The "Kỳ vọng" block of a setup message for one rule (its journal setups and scored
    rows)."""
    head = (
        "Kỳ vọng (luật đã qua kiểm định):"
        if rule.get("validated")
        else "Kỳ vọng: chưa tới mốc, không kết luận."
    )
    lines = [head, *[f"- {e}" for e in rule.get("evidence", [])]]
    st = live_stats(scored)
    if st["n"] == 0:
        lines.append("- Trực tiếp: chưa có setup đóng")
    else:
        ci = f" (CI {st['lo']:+.2f}→{st['hi']:+.2f})" if np.isfinite(st["lo"]) else ""
        lines.append(f"- Trực tiếp: {st['n']} setup đã đóng, TB {st['mean']:+.2f}R{ci}")
    lines.append(f"- Mốc: {progress(rule, setups, now)}")
    return lines


# ---------------------------------------------------------------- setups


def _exit_rule(s: pd.Series) -> str:
    if pd.notna(s["target"]) and pd.notna(s["max_bars"]):
        hours = int(s["max_bars"]) * pd.Timedelta(s["timeframe"]) / pd.Timedelta(hours=1)
        tp = (s["target"] / s["entry"] - 1) * 100
        return f"TP {s['target']:.6g} ({tp:+.1f}%) · thoát sau {hours:g}h nếu chưa chạm"
    return (
        "không TP · thoát khi tín hiệu xu hướng về 0 ở giá đóng nến ngày; SL là mức thoát của "
        "cả ensemble"
    )


def _share(a: pd.Series, side: int, policy: RiskPolicy) -> str:
    """Why the risk is what it is: scaled down by a cap, one part of a shared bet, or bounded
    by the leverage cap."""
    if a["state"] == "reduced":
        return f" (giảm từ {a['base_risk']:.2%} do trần {_cap(a['limit_hit'], side, policy)})"
    if a["sleeve"] == "event" and a["bet_size"] > 1:
        n = int(a["bet_size"])
        return f" (1/{n} của một cược {policy.event_bet:.0%}: {n} lệnh {SIDE[side]} cùng nến)"
    if a["risk"] < a["granted_risk"] - 1e-12:
        return f" (giới hạn bởi đòn bẩy tối đa; được cấp {a['granted_risk']:.2%})"
    return ""


def setup_text(
    s: pd.Series,
    a: pd.Series,
    rule: dict,
    evidence: list[str],
    policy: RiskPolicy,
    hedge: str | None,
) -> str:
    """Message of a setup taken: journal row ``s``, allocation ``a``."""
    side = int(s["side"])
    pct = (s["stop"] / s["entry"] - 1) * 100
    lines = [
        header(rule),
        f"{SIDE[side]} {s['pair']} · {title(s['strategy'], rule)}",
        f"Nến {s['timeframe']} đóng {utc(s['decided_at'])}",
        f"Vào {s['entry']:.6g} (giá đóng nến) · SL {s['stop']:.6g} ({pct:+.1f}%) · {_exit_rule(s)}",
        (
            f"Khối lượng {a['qty']:.6g} ({a['notional']:,.0f} USDT): rủi ro {a['risk']:.2%} "
            f"vốn mẫu {policy.capital:,.0f} USDT{_share(a, side, policy)} · đòn bẩy tối đa "
            f"{int(s['leverage_max'])}x isolated"
        ),
        f"Chi phí ước tính {s['cost_r']:.2f}R",
        *evidence,
        f"Rủi ro mở sau chu kỳ: {risk_line(a['open_long'], a['open_short'], policy)}",
    ]
    if hedge:
        lines.append(f"Lưu ý: sổ đang có {hedge} cùng coin, cần chế độ hedge")
    lines.append(f"Lý do: {s['reason']}")
    return "\n".join(lines)


def not_taken_text(s: pd.Series, a: pd.Series, rule: dict, policy: RiskPolicy) -> str:
    """One line for a setup the book did not take."""
    side = int(s["side"])
    if a["state"] == "blocked":
        why = f"hết ngân sách rủi ro, trần {_cap(a['limit_hit'], side, policy)}"
    elif a["state"] == "skipped_cost":
        why = f"chi phí {s['cost_r']:.2f}R > {MAX_COST_R:.2f}R"
    else:
        why = NOT_TAKEN.get(a["state"], a["state"])
    return (
        f"[KHÔNG VÀO: {why}] {SIDE[side]} {s['pair']} · {title(s['strategy'], rule)} · nến "
        f"{s['timeframe']} đóng {utc(s['decided_at'])} · vào {s['entry']:.6g}, SL {s['stop']:.6g}"
    )


def _held(start, end) -> str:
    d = pd.Timestamp(end) - pd.Timestamp(start)
    hours = d / pd.Timedelta(hours=1)
    return f"{hours:g}h" if hours < 48 else f"{d / pd.Timedelta(days=1):.0f} ngày"


def close_text(s: pd.Series, a: pd.Series, o: pd.Series, rule: dict, policy: RiskPolicy) -> str:
    """Message of a closed position: journal row ``s``, allocation ``a``, outcome ``o`` (closure
    with the journal's ``gross_r``, ``fee_r``, ``funding_r`` when there is a price)."""
    side = int(s["side"])
    tag = "" if rule.get("validated") else "THỬ NGHIỆM · "
    head = f"{tag}ĐÓNG {SIDE[side]} {s['pair']} · {title(s['strategy'], rule)}"
    opened = f"vào {s['entry']:.6g} ({utc(s['decided_at'])})"
    if o["exit_reason"] == "no_price":
        return f"{head}\n{EXITS['no_price']} · {opened}, dừng {utc(o['exit_date'])}"
    held = _held(s["decided_at"], o["exit_date"])
    return "\n".join(
        [
            head,
            (
                f"{EXITS.get(o['exit_reason'], o['exit_reason'])} · {opened} → ra "
                f"{o['exit_price']:.6g} ({utc(o['exit_date'])}), giữ {held}"
            ),
            (
                f"R gộp {o['gross_r']:+.2f} · phí {-o['fee_r']:+.2f} · funding "
                f"{-o['funding_r']:+.2f} → R ròng {o['net_r']:+.2f}"
            ),
            (
                f"Sổ: rủi ro {a['risk']:.2%} → {o['pnl']:+.2%} vốn "
                f"({o['pnl'] * policy.capital:+,.0f} USDT)"
            ),
        ]
    )


# ---------------------------------------------------------------- weekly


def _in(dates: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    t = pd.to_datetime(dates, utc=True)
    return (t >= start) & (t < end)


def _book_numbers(book: pd.DataFrame, start, end) -> dict:
    """Closed P&L of the week and in total, its drawdown and curve, positions open at ``end``."""
    taken = book[book["state"].isin(TAKEN)]
    exit_t = pd.to_datetime(taken["exit_date"], utc=True)
    closed = taken[exit_t.notna() & (exit_t < end) & taken["pnl"].notna()]
    closed = closed.assign(_t=pd.to_datetime(closed["exit_date"], utc=True)).sort_values("_t")
    cum = closed["pnl"].cumsum()
    still = taken[exit_t.isna() | (exit_t >= end)]
    return {
        "week_pnl": float(closed.loc[closed["_t"] >= start, "pnl"].sum()),
        "total": float(cum.iloc[-1]) if len(cum) else 0.0,
        "max_dd": min(float((cum - cum.cummax().clip(lower=0)).min()) if len(cum) else 0.0, 0.0),
        "equity": pd.Series(cum.to_numpy(dtype=float), index=closed["_t"].to_numpy()),
        "open": {
            "n": len(still),
            "long": float(still.loc[still["side"] > 0, "risk"].sum()),
            "short": float(still.loc[still["side"] < 0, "risk"].sum()),
        },
    }


def _rule_numbers(strategy, rule, book, setups, scored, start, end) -> dict:
    a = book[(book["strategy"] == strategy) & (book["state"] != "pre_book")]
    a = a[_in(a["decided_at"], start, end)]
    n = a["state"].value_counts()
    sc = scored[scored["strategy"] == strategy]
    week = sc[_in(sc["exit_date"], start, end)] if len(sc) else sc
    st = live_stats(sc)
    mine = setups[setups["strategy"] == strategy]
    out = {
        "name": rule.get("name", strategy),
        "new": len(a),
        "states": {
            "sent": int(n.get("sent", 0)),
            "reduced": int(n.get("reduced", 0)),
            "blocked": int(n.get("blocked", 0) + n.get("duplicate", 0)),
            "late": int(n.get("late", 0)),
            "skipped": int(n.get("skipped_cost", 0)),
        },
        "closed": len(week),
        "closed_r": float(week["net_r"].sum()) if len(week) else 0.0,
        "live": (
            f"trực tiếp {st['n']} setup đóng, TB {st['mean']:+.2f}R"
            if st["n"]
            else "trực tiếp chưa có setup đóng"
        ),
        "progress": progress_value(rule, mine, end),
    }
    s = out["states"]
    out["text"] = (
        f"{out['name']}: mới {out['new']} (vào {s['sent']}, giảm {s['reduced']}, chặn "
        f"{s['blocked']}, trễ {s['late']}, bỏ qua {s['skipped']}) · đóng {out['closed']}: "
        f"{out['closed_r']:+.2f}R · {out['live']} · mốc {progress(rule, mine, end)}"
    )
    return out


def _ops_line(ops: dict) -> str:
    line = "Vận hành: "
    if ops.get("expected"):
        line += f"{ops['on_time']}/{ops['expected']} chu kỳ đúng giờ"
    else:
        line += "chưa có số liệu chu kỳ"
    if ops.get("recorder_days"):
        line += f" · bộ ghi đủ ≥ 90%: {ops['recorder_good']}/{ops['recorder_days']} ngày"
        if ops.get("recorder_worst"):
            line += f" (thấp nhất {ops['recorder_worst']})"
    return line


def weekly_summary(
    week: tuple[pd.Timestamp, pd.Timestamp],
    book: pd.DataFrame,
    setups: pd.DataFrame,
    scored: pd.DataFrame,
    rules: dict,
    ops: dict,
) -> dict:
    """Numbers of the week [start, end), shared by the text and the card: the book (P&L,
    drawdown and curve of the closed P&L, positions open at the end), each rule (new setups by
    book state, closed in the week, live journal stats, milestone) and operations (``ops``:
    on_time, expected, recorder_good, recorder_days, optional recorder_worst)."""
    start, end = week
    names = (
        list(rules) + sorted(set(setups["strategy"]) - set(rules)) if len(setups) else list(rules)
    )
    experimental = [s for s, r in rules.items() if not r.get("validated")]
    return {
        "week": week,
        **_book_numbers(book, start, end),
        "rules": [
            _rule_numbers(s, rules.get(s, {}), book, setups, scored, start, end) for s in names
        ],
        "ops": _ops_line(ops),
        "experimental": bool(experimental),
        "all_experimental": len(experimental) == len(rules),
    }


def weekly_text(
    week: tuple[pd.Timestamp, pd.Timestamp],
    book: pd.DataFrame,
    setups: pd.DataFrame,
    scored: pd.DataFrame,
    rules: dict,
    policy: RiskPolicy,
    ops: dict,
) -> str:
    """Text of the weekly summary (numbers: ``weekly_summary``)."""
    w = weekly_summary(week, book, setups, scored, rules, ops)
    start, end = week
    iso = start.isocalendar()
    last = end - pd.Timedelta(days=1)
    head = f"TỔNG KẾT TUẦN {iso.year}-W{iso.week:02d} ({start:%d/%m} → {last:%d/%m} UTC)"
    lines = [head + (" · THỬ NGHIỆM" if w["experimental"] else "")]
    if w["experimental"]:
        lines.append(
            "Chưa luật nào tới mốc: số liệu để theo dõi, không kết luận."
            if w["all_experimental"]
            else "Luật gắn THỬ NGHIỆM chưa tới mốc: số liệu để theo dõi, không kết luận."
        )
    o = w["open"]
    lines += [
        (
            f"Sổ (vốn mẫu {policy.capital:,.0f} USDT): tuần {w['week_pnl']:+.2%} "
            f"({w['week_pnl'] * policy.capital:+,.0f} USDT) · lũy kế {w['total']:+.2%} · sụt "
            f"giảm lớn nhất {w['max_dd']:.2%}"
        ),
        f"Đang mở {o['n']}: {risk_line(o['long'], o['short'], policy)}",
        *[r["text"] for r in w["rules"]],
        w["ops"],
    ]
    return "\n".join(lines)


def weekly_caption(summary: dict, policy: RiskPolicy) -> str:
    start, _ = summary["week"]
    iso = start.isocalendar()
    tag = "THỬ NGHIỆM · " if summary["experimental"] else ""
    return cap(
        f"{tag}TỔNG KẾT TUẦN {iso.year}-W{iso.week:02d} · tuần {summary['week_pnl']:+.2%} "
        f"({summary['week_pnl'] * policy.capital:+,.0f} USDT) · lũy kế {summary['total']:+.2%} · "
        f"đang mở {summary['open']['n']}\n{summary['ops']}"
    )


# ---------------------------------------------------------------- captions (Telegram photos)


CAPTION = 1024  # characters of a Telegram photo caption


def cap(text: str, limit: int = CAPTION) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _tag(rule: dict) -> str:
    return "" if rule.get("validated") else "THỬ NGHIỆM · "


def _levels(s: pd.Series) -> str:
    out = f"Vào {s['entry']:.6g} · SL {s['stop']:.6g} ({(s['stop'] / s['entry'] - 1) * 100:+.1f}%)"
    if pd.notna(s.get("target")) and pd.notna(s.get("max_bars")):
        hours = int(s["max_bars"]) * pd.Timedelta(s["timeframe"]) / pd.Timedelta(hours=1)
        tp = (s["target"] / s["entry"] - 1) * 100
        out += f" · TP {s['target']:.6g} ({tp:+.1f}%) · {hours:g}h"
    else:
        out += " · thoát theo xu hướng"
    return out


def setup_caption(
    s: pd.Series,
    a: pd.Series,
    rule: dict,
    policy: RiskPolicy,
    partners: list[str] | None = None,
) -> str:
    """Short caption under the setup card (the card holds the details). ``partners``: the other
    coins of the same bet (one message per setup, the shared bet noted here)."""
    side = int(s["side"])
    share = ""
    if a["state"] == "reduced":
        share = f" (giảm do trần {_cap(a['limit_hit'], side, policy)})"
    elif a["sleeve"] == "event" and a["bet_size"] > 1:
        share = f" (1/{int(a['bet_size'])} cược)"
    lines = [
        f"{_tag(rule)}{SIDE[side]} {s['pair']} · {rule.get('name') or s['strategy']}",
        _levels(s),
        (
            f"KL {a['qty']:.6g} ({a['notional']:,.0f} USDT) · rủi ro {a['risk']:.2%} vốn"
            f"{share} · ≤{int(s['leverage_max'])}x isolated"
        ),
    ]
    if partners:
        lines.append(
            f"Chung một cược {policy.event_bet:.0%} với {SIDE[side]} {', '.join(partners)} cùng "
            "nến: cùng một cú sốc, thường thắng thua cùng lúc"
        )
    lines.append(str(s["reason"]))
    return cap("\n".join(lines))


def close_caption(s: pd.Series, o: pd.Series, rule: dict, policy: RiskPolicy) -> str:
    side = int(s["side"])
    head = f"{_tag(rule)}ĐÓNG {SIDE[side]} {s['pair']} · "
    if o["exit_reason"] == "no_price":
        return cap(head + EXITS["no_price"])
    what = EXITS.get(o["exit_reason"], o["exit_reason"])
    return cap(
        f"{head}{what} · R ròng {o['net_r']:+.2f} · {o['pnl']:+.2%} vốn "
        f"({o['pnl'] * policy.capital:+,.0f} USDT)"
    )


def alert_text(text: str) -> str:
    return f"CẢNH BÁO VẬN HÀNH · {text}"
