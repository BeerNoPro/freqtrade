"""
Image cards of the product messages (docs/signal-bot-workflow.md, section 5): HD PNGs for a setup
(candles with entry, stop and target; one message per setup), a close (path from entry to exit,
R after fees and funding), the weekly summary (closed P&L curve, rules and their milestones) and
an operational alert. Rendered with matplotlib (Agg, no display); every card is
``SIZE`` pixels (``ALERT_SIZE`` for alerts) whatever the data, and renders without candles.

Pure functions: rows of the journal (``mce.journal``), the book (``mce.riskbook``) and the rule's
product config in, PNG bytes out. The texts and their numbers come from ``mce.messages``.
"""

import io
import textwrap

import matplotlib


matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle

from mce.messages import EXITS, SIDE
from mce.riskbook import RiskPolicy


DPI = 200
SIZE = (1600, 1000)
ALERT_SIZE = (1600, 420)
BG, PANEL, GRID = "#0e141b", "#151f2a", "#2a3746"
TEXT, MUTED = "#e8eef4", "#8a99a8"
GREEN, RED, AMBER, BLUE, GRAY = "#26a69a", "#ef5350", "#f5a623", "#4f9cf9", "#6b7785"
LOOKBACK = {"4h": 48, "1d": 90}  # candles shown before the decision
plt.rcParams.update({"font.family": "DejaVu Sans", "axes.unicode_minus": False})


# ---------------------------------------------------------------- drawing helpers


def _fig(size: tuple[int, int] = SIZE) -> Figure:
    fig = plt.figure(figsize=(size[0] / DPI, size[1] / DPI), dpi=DPI)
    fig.patch.set_facecolor(BG)
    return fig


def _png(fig: Figure) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=DPI, facecolor=BG)
    plt.close(fig)
    return buf.getvalue()


def _p(x: float) -> str:
    return f"{x:.6g}"


def _r(v: float) -> str:
    """R with a sign, a value that rounds to zero shown as 0.00 (no "-0.00")."""
    return "0.00" if abs(v) < 0.005 else f"{v:+.2f}"


def _chip(fig: Figure, x: float, y: float, text: str, color: str, size: float = 8.5) -> None:
    fig.text(
        x,
        y,
        text,
        color=BG,
        fontsize=size,
        fontweight="bold",
        va="center",
        bbox={"boxstyle": "round,pad=0.35", "fc": color, "ec": "none"},
    )


def _header(fig: Figure, rule: dict, side: int | None, title: str, subtitle: str) -> None:
    validated = bool(rule.get("validated"))
    _chip(
        fig,
        0.03,
        0.94,
        "ĐÃ KIỂM ĐỊNH" if validated else "THỬ NGHIỆM",
        GREEN if validated else AMBER,
    )
    x = 0.17 if not validated else 0.19
    if side is not None:
        _chip(fig, x, 0.94, ("▲ " if side > 0 else "▼ ") + SIDE[side], GREEN if side > 0 else RED)
        x += 0.12
    fig.text(x, 0.94, title, color=TEXT, fontsize=15, fontweight="bold", va="center")
    fig.text(0.03, 0.885, subtitle, color=MUTED, fontsize=7.5, va="center")


def _style(ax) -> None:
    ax.set_facecolor(PANEL)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(colors=MUTED, labelsize=6, length=0)
    ax.grid(color=GRID, linewidth=0.4, alpha=0.6)
    ax.set_axisbelow(True)


def _candles(ax, k: pd.DataFrame, right: int, timeframe: str) -> None:
    """Candles at x = 0..n-1, room for ``right`` more bars; sparse date labels."""
    if k.empty:
        ax.set_xlim(-1, right + 1)
        ax.text(
            0.5,
            0.5,
            "không có dữ liệu nến",
            color=MUTED,
            fontsize=8,
            ha="center",
            transform=ax.transAxes,
        )
        return
    x = np.arange(len(k))
    o, h, lo, c = (k[col].to_numpy(dtype=float) for col in ("open", "high", "low", "close"))
    colors = np.where(c >= o, GREEN, RED)
    ax.vlines(x, lo, h, color=colors, linewidth=0.7)
    body = np.maximum(np.abs(c - o), (h - lo).max() * 0.002)
    ax.bar(x, body, bottom=np.minimum(o, c), width=0.65, color=colors, linewidth=0)
    ax.set_xlim(-1, len(k) + right)
    step = max(1, len(k) // 5)
    fmt = "%d/%m" if timeframe == "1d" else "%d/%m %Hh"
    ticks = list(range(0, len(k), step))
    ax.set_xticks(ticks, [pd.Timestamp(k["date"].iloc[i]).strftime(fmt) for i in ticks])


def _level(ax, y: float, x0: float, x1: float, color: str, label: str, style: str = "--") -> None:
    ax.hlines(y, x0, x1, color=color, linewidth=1.1, linestyles=style)
    ax.text(x1 + 0.3, y, label, color=color, fontsize=6.3, va="center", fontweight="bold")


def _limits(ax, k: pd.DataFrame, levels: list[float | None]) -> None:
    vals = [v for v in levels if v is not None and np.isfinite(v)]
    if not k.empty:
        vals += [float(k["low"].min()), float(k["high"].max())]
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.08 or abs(hi) * 0.01 or 1.0
    ax.set_ylim(lo - pad, hi + pad)


def _rows(
    fig: Figure,
    rows: list[tuple[str, str, str]],
    x0: float,
    x1: float,
    top: float,
    step: float = 0.062,
) -> None:
    for i, (label, value, color) in enumerate(rows):
        y = top - i * step
        fig.text(x0, y, label, color=MUTED, fontsize=7, va="center")
        fig.text(
            x1, y, value, color=color, fontsize=9.5, fontweight="bold", va="center", ha="right"
        )


def _bar(
    fig: Figure,
    rect: tuple[float, float, float, float],
    frac: float | None,
    color: str,
    label: str,
    value: str,
) -> None:
    ax = fig.add_axes(rect)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.add_patch(Rectangle((0, 0), 1, 1, color=GRID, linewidth=0))
    if frac is not None and np.isfinite(frac):
        ax.add_patch(Rectangle((0, 0), min(max(frac, 0.0), 1.0), 1, color=color, linewidth=0))
    y = rect[1] + rect[3] + 0.012
    fig.text(rect[0], y, label, color=MUTED, fontsize=6.5, va="bottom")
    fig.text(rect[0] + rect[2], y, value, color=TEXT, fontsize=6.5, va="bottom", ha="right")


def _risk_bars(
    fig: Figure,
    open_long: float,
    open_short: float,
    policy: RiskPolicy,
    x: float = 0.65,
    w: float = 0.32,
    top: float = 0.25,
) -> None:
    fig.text(x, top + 0.045, "RỦI RO MỞ SAU CHU KỲ", color=MUTED, fontsize=6.5, fontweight="bold")
    items = [
        ("LONG", open_long, policy.max_side, GREEN),
        ("SHORT", open_short, policy.max_side, RED),
        ("TỔNG", open_long + open_short, policy.max_total, BLUE),
    ]
    for i, (name, value, cap, color) in enumerate(items):
        y = top - 0.03 - i * 0.075
        _bar(fig, (x, y, w, 0.022), value / cap, color, name, f"{value:.2%} / {cap:.0%}")


def _wrapped(
    fig: Figure,
    lines: list[str],
    x: float,
    top: float,
    width: int,
    size: float,
    color: str = TEXT,
    step: float = 0.034,
    limit: int = 8,
) -> float:
    """Wrapped lines from ``top`` down; returns the y below the last one."""
    out: list[str] = []
    for line in lines:
        out += textwrap.wrap(line, width) or [""]
    for i, line in enumerate(out[:limit]):
        fig.text(x, top - i * step, line, color=color, fontsize=size, va="top")
    return top - min(len(out), limit) * step


# ---------------------------------------------------------------- setup


def _setup_rows(s: pd.Series, a: pd.Series) -> list[tuple[str, str, str]]:
    stop_pct = (s["stop"] / s["entry"] - 1) * 100
    rows = [
        ("Vào (giá đóng nến)", _p(s["entry"]), BLUE),
        ("Dừng lỗ (SL)", f"{_p(s['stop'])}  {stop_pct:+.1f}%", RED),
    ]
    if pd.notna(s.get("target")) and pd.notna(s.get("max_bars")):
        tp_pct = (s["target"] / s["entry"] - 1) * 100
        hours = int(s["max_bars"]) * pd.Timedelta(s["timeframe"]) / pd.Timedelta(hours=1)
        rows += [
            ("Chốt lời (TP)", f"{_p(s['target'])}  {tp_pct:+.1f}%", GREEN),
            ("Thoát theo thời gian", f"sau {hours:g}h", TEXT),
        ]
    else:
        rows += [("Chốt lời", "theo xu hướng (về 0)", TEXT)]
    share = ""
    if a["state"] == "reduced":
        share = " (giảm)"
    elif a["sleeve"] == "event" and a["bet_size"] > 1:
        share = f" (1/{int(a['bet_size'])} cược)"
    rows += [
        ("Khối lượng", f"{a['qty']:.6g}", TEXT),
        ("Giá trị lệnh", f"{a['notional']:,.0f} USDT", TEXT),
        ("Rủi ro / vốn mẫu", f"{a['risk']:.2%}{share}", AMBER),
        ("Đòn bẩy tối đa", f"{int(s['leverage_max'])}x isolated", TEXT),
        ("Chi phí ước tính", f"{s['cost_r']:.2f}R", TEXT),
    ]
    return rows


def setup_card(
    s: pd.Series,
    a: pd.Series,
    rule: dict,
    evidence: list[str],
    progress: tuple[float, str] | None,
    policy: RiskPolicy,
    candles: pd.DataFrame,
) -> bytes:
    """Card of a setup taken: journal row ``s``, allocation ``a``, the candles closed at the
    decision (last row = the decision candle), the rule's evidence lines and milestone."""
    side = int(s["side"])
    fig = _fig()
    name = rule.get("name") or s["strategy"]
    _header(
        fig,
        rule,
        side,
        s["pair"],
        f"{name} · nến {s['timeframe']} đóng {pd.Timestamp(s['decided_at']):%Y-%m-%d %H:%M} UTC",
    )
    ax = fig.add_axes((0.04, 0.4, 0.5, 0.44))
    _style(ax)
    k = candles.tail(LOOKBACK.get(s["timeframe"], 60)).reset_index(drop=True)
    has_tp = pd.notna(s.get("target")) and pd.notna(s.get("max_bars"))
    ahead = int(s["max_bars"]) if has_tp else 8
    _candles(ax, k, ahead + 6, s["timeframe"])
    x0 = max(len(k) - 1, 0)
    x1 = x0 + ahead
    ax.fill_between([x0, x1], s["entry"], s["stop"], color=RED, alpha=0.14, linewidth=0)
    _level(ax, s["stop"], x0, x1, RED, f"SL {_p(s['stop'])}")
    if has_tp:
        ax.fill_between([x0, x1], s["entry"], s["target"], color=GREEN, alpha=0.14, linewidth=0)
        _level(ax, s["target"], x0, x1, GREEN, f"TP {_p(s['target'])}")
    _level(ax, s["entry"], x0, x1, BLUE, f"Vào {_p(s['entry'])}", "-")
    ax.scatter([x0], [s["entry"]], marker="v" if side < 0 else "^", color=TEXT, s=18, zorder=5)
    _limits(ax, k, [s["entry"], s["stop"], s["target"] if has_tp else None])
    _rows(fig, _setup_rows(s, a), 0.64, 0.97, 0.83, step=0.055)
    bottom = _wrapped(fig, evidence[:4], 0.04, 0.34, 78, 6.6, step=0.032, limit=6)
    if progress is not None:
        _bar(
            fig,
            (0.04, max(bottom - 0.05, 0.08), 0.5, 0.02),
            progress[0],
            AMBER,
            "Tiến độ tới mốc",
            progress[1],
        )
    _wrapped(fig, [f"Lý do: {s['reason']}"], 0.04, 0.045, 120, 6.2, color=MUTED, limit=1)
    _risk_bars(fig, a["open_long"], a["open_short"], policy)
    return _png(fig)


# ---------------------------------------------------------------- close


def close_card(
    s: pd.Series, a: pd.Series, o: pd.Series, rule: dict, policy: RiskPolicy, candles: pd.DataFrame
) -> bytes:
    """Card of a closed position: candles up to the exit candle, entry and exit, R parts."""
    side = int(s["side"])
    fig = _fig()
    reason = o["exit_reason"]
    _header(
        fig,
        rule,
        side,
        f"ĐÓNG {s['pair']}",
        f"{rule.get('name') or s['strategy']} · vào "
        f"{pd.Timestamp(s['decided_at']):%Y-%m-%d %H:%M} → ra "
        f"{pd.Timestamp(o['exit_date']):%Y-%m-%d %H:%M} UTC",
    )
    net = o.get("net_r")
    good = pd.notna(net) and net > 0
    color = GRAY if reason == "no_price" else (GREEN if good else RED)
    _chip(fig, 0.64, 0.82, EXITS.get(reason, reason).split(":")[0], color, 9)
    ax = fig.add_axes((0.04, 0.12, 0.5, 0.66))
    _style(ax)
    bar = pd.Timedelta(s["timeframe"])
    held_bars = int((pd.Timestamp(o["exit_date"]) - pd.Timestamp(s["decided_at"])) / bar)
    k = candles.tail(held_bars + LOOKBACK.get(s["timeframe"], 60) // 2).reset_index(drop=True)
    _candles(ax, k, 8, s["timeframe"])
    dates = (
        pd.to_datetime(k["date"], utc=True) if len(k) else pd.Series(dtype="datetime64[ns, UTC]")
    )
    hit: np.ndarray = np.flatnonzero(dates == pd.Timestamp(s["decided_at"]) - bar)
    xe = int(hit[0]) if len(hit) else 0
    xx = max(len(k) - 1, xe)
    edge = xx + 2  # lines run past the last candle so the labels sit in free space
    _level(ax, s["stop"], xe, edge, RED, f"SL {_p(s['stop'])}")
    if pd.notna(s.get("target")):
        _level(ax, s["target"], xe, edge, GREEN, f"TP {_p(s['target'])}")
    _level(ax, s["entry"], xe, edge, BLUE, f"Vào {_p(s['entry'])}", "-")
    ax.scatter([xe], [s["entry"]], marker="v" if side < 0 else "^", color=TEXT, s=18, zorder=5)
    levels = [s["entry"], s["stop"], s["target"] if pd.notna(s.get("target")) else None]
    if pd.notna(o.get("exit_price")):
        ax.scatter([xx], [o["exit_price"]], marker="X", color=color, s=40, zorder=6)
        levels.append(float(o["exit_price"]))
    _limits(ax, k, levels)
    if reason == "no_price":
        _wrapped(fig, [EXITS["no_price"]], 0.64, 0.7, 40, 8, color=MUTED)
        return _png(fig)
    parts = [
        ("R gộp", o["gross_r"]),
        ("Phí", -o["fee_r"]),
        ("Funding", -o["funding_r"]),
        ("R ròng", o["net_r"]),
    ]
    span = max(1.0, max(abs(v) for _, v in parts))
    for i, (label, v) in enumerate(parts):
        y = 0.68 - i * 0.075
        fig.text(0.64, y + 0.006, label, color=MUTED, fontsize=7)
        rect = (0.75, y, 0.15, 0.025)
        axb = fig.add_axes(rect)
        axb.set_xlim(-span, span)
        axb.set_ylim(0, 1)
        axb.axis("off")
        axb.add_patch(Rectangle((-span, 0), 2 * span, 1, color=GRID, linewidth=0))
        axb.add_patch(
            Rectangle((min(0, v), 0), abs(v), 1, color=GREEN if v >= 0 else RED, linewidth=0)
        )
        fig.text(0.97, y + 0.006, _r(v), color=TEXT, fontsize=7.5, ha="right", fontweight="bold")
    held = pd.Timestamp(o["exit_date"]) - pd.Timestamp(s["decided_at"])
    hours = held / pd.Timedelta(hours=1)
    _rows(
        fig,
        [
            ("Lãi/lỗ theo sổ", f"{o['pnl']:+.2%}", color),
            ("Theo vốn mẫu", f"{o['pnl'] * policy.capital:+,.0f} USDT", color),
            ("Rủi ro đã đặt", f"{a['risk']:.2%}", AMBER),
            (
                "Giữ lệnh",
                f"{hours:g}h" if hours < 48 else f"{held / pd.Timedelta(days=1):.0f} ngày",
                TEXT,
            ),
            ("Giá ra", _p(o["exit_price"]), TEXT),
        ],
        0.64,
        0.97,
        0.36,
    )
    return _png(fig)


# ---------------------------------------------------------------- weekly


def weekly_card(summary: dict, policy: RiskPolicy) -> bytes:
    """Card of the weekly summary (``mce.messages.weekly_summary``)."""
    start, end = summary["week"]
    iso = start.isocalendar()
    fig = _fig()
    rule = {} if summary.get("experimental", True) else {"validated": True}
    _header(
        fig,
        rule,
        None,
        f"TỔNG KẾT TUẦN {iso.year}-W{iso.week:02d}",
        f"{start:%d/%m} → {end - pd.Timedelta(days=1):%d/%m} UTC · vốn mẫu "
        f"{policy.capital:,.0f} USDT"
        + (" · chưa tới mốc, không kết luận" if summary.get("experimental", True) else ""),
    )
    ax = fig.add_axes((0.05, 0.5, 0.5, 0.33))
    _style(ax)
    eq = summary["equity"]
    if len(eq):
        x = pd.DatetimeIndex(eq.index).tz_convert(None)
        y = eq.to_numpy(dtype=float) * 100
        ax.step(x, y, where="post", color=BLUE, linewidth=1.3)
        ax.fill_between(x, y, 0, step="post", color=GREEN, alpha=0.18, where=y >= 0)
        ax.fill_between(x, y, 0, step="post", color=RED, alpha=0.18, where=y < 0)
        ax.axhline(0, color=MUTED, linewidth=0.6)
        ax.set_ylabel("% vốn (lệnh đã đóng)", color=MUTED, fontsize=6)
        ax.tick_params(axis="x", labelrotation=0)
        for label in ax.get_xticklabels():
            label.set_fontsize(5.5)
    else:
        ax.set_xticks([])
        ax.set_yticks([])
        ax.text(
            0.5,
            0.5,
            "chưa có lệnh nào đóng trong sổ",
            color=MUTED,
            fontsize=8,
            ha="center",
            transform=ax.transAxes,
        )
    color = GREEN if summary["week_pnl"] >= 0 else RED
    _rows(
        fig,
        [
            (
                "Tuần này",
                f"{summary['week_pnl']:+.2%} ({summary['week_pnl'] * policy.capital:+,.0f} USDT)",
                color,
            ),
            ("Lũy kế", f"{summary['total']:+.2%}", GREEN if summary["total"] >= 0 else RED),
            (
                "Sụt giảm lớn nhất",
                f"{summary['max_dd']:.2%}",
                RED if summary["max_dd"] < 0 else TEXT,
            ),
            ("Vị thế đang mở", str(summary["open"]["n"]), TEXT),
        ],
        0.64,
        0.97,
        0.83,
        step=0.055,
    )
    _risk_bars(fig, summary["open"]["long"], summary["open"]["short"], policy, top=0.58)
    top = 0.31
    for i, r in enumerate(summary["rules"][:4]):
        y = top - i * 0.07
        st = r["states"]
        fig.text(0.05, y, r["name"], color=TEXT, fontsize=7.5, fontweight="bold")
        fig.text(
            0.05,
            y - 0.03,
            (
                f"mới {r['new']} · vào {st['sent']} · giảm {st['reduced']} · chặn "
                f"{st['blocked']} · trễ {st['late']} · bỏ qua {st['skipped']} · đóng "
                f"{r['closed']}: {r['closed_r']:+.2f}R · {r['live']}"
            ),
            color=MUTED,
            fontsize=6,
        )
        prog = r.get("progress")
        if prog is not None:
            _bar(fig, (0.72, y - 0.025, 0.25, 0.018), prog[0], AMBER, "mốc", prog[1])
    fig.text(0.05, 0.03, summary["ops"], color=MUTED, fontsize=6.5)
    return _png(fig)


# ---------------------------------------------------------------- alert


def alert_card(text: str, now: pd.Timestamp) -> bytes:
    """Card of an operational alert: a red band and the wrapped text."""
    fig = _fig(ALERT_SIZE)
    fig.patches.append(
        Rectangle((0, 0.74), 1, 0.26, transform=fig.transFigure, color=RED, zorder=0)
    )
    fig.text(
        0.03, 0.87, "CẢNH BÁO VẬN HÀNH", color=TEXT, fontsize=14, fontweight="bold", va="center"
    )
    fig.text(
        0.97, 0.87, f"{now:%Y-%m-%d %H:%M} UTC", color=TEXT, fontsize=8, va="center", ha="right"
    )
    _wrapped(fig, [text], 0.03, 0.62, 80, 10.5, step=0.15, limit=4)
    return _png(fig)
