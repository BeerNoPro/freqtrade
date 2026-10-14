"""Image cards of the product messages: HD PNGs that render in every case (no network)."""

import io

import numpy as np
import pandas as pd
from mce import cards
from mce.riskbook import RiskPolicy
from PIL import Image


T0 = pd.Timestamp("2026-10-14 16:00", tz="UTC")
POLICY = RiskPolicy()
RULE = {"name": "(c) bán theo cú sập 4h", "validated": False}
EVIDENCE = [
    "Kỳ vọng: chưa tới mốc, không kết luận.",
    "- Khối 1 2026-04→10: 62 setup, +0.00R/setup (CI -0.27→+0.29)",
    "- Trực tiếp: 4 setup đã đóng, TB -1.07R",
    "- Mốc: 200 sự kiện sau IS (69/200), hạn 2028-01-01",
]


def _candles(n: int = 60, end: pd.Timestamp = T0, freq: str = "4h") -> pd.DataFrame:
    rng = np.random.default_rng(1)
    close = 1.40 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    open_ = np.r_[close[0], close[:-1]]
    spread = np.abs(rng.normal(0, 0.006, n)) * close
    dates = pd.date_range(end=end - pd.Timedelta(freq), periods=n, freq=freq)
    return pd.DataFrame(
        {
            "date": dates,
            "open": open_,
            "high": np.maximum(open_, close) + spread,
            "low": np.minimum(open_, close) - spread,
            "close": close,
        }
    )


def _setup(**kw) -> pd.Series:
    base = {
        "id": 12,
        "strategy": "shock_no_oi_drop_short_4h@v1",
        "pair": "XRPUSDT",
        "timeframe": "4h",
        "decided_at": T0.isoformat(),
        "side": -1,
        "entry": 1.3266,
        "stop": 1.35124,
        "target": 1.27732,
        "max_bars": 3,
        "leverage_max": 5,
        "cost_r": 0.07,
        "reason": "Bán theo cú sập không kèm thanh lý: nến 4h -2.1 ATR, OI z -0.6",
    }
    return pd.Series({**base, **kw})


def _alloc(**kw) -> pd.Series:
    base = {
        "setup_id": 12,
        "pair": "XRPUSDT",
        "side": -1,
        "sleeve": "event",
        "bet_size": 3,
        "state": "sent",
        "limit_hit": "",
        "base_risk": 0.00333,
        "granted_risk": 0.00333,
        "risk": 0.00333,
        "qty": 1352.68,
        "notional": 1794.5,
        "open_long": 0.01,
        "open_short": 0.01,
    }
    return pd.Series({**base, **kw})


def _size(png: bytes) -> tuple[int, int]:
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    return Image.open(io.BytesIO(png)).size


def test_setup_card_is_an_hd_png_with_or_without_candles() -> None:
    png = cards.setup_card(
        _setup(), _alloc(), RULE, EVIDENCE, (69 / 200, "69/200"), POLICY, _candles()
    )
    assert _size(png) == cards.SIZE
    trend = _setup(timeframe="1d", side=1, stop=1.10, target=None, max_bars=None)
    alloc = _alloc(sleeve="trend", side=1, bet_size=1, state="reduced", limit_hit="side")
    days = _candles(120, freq="1D")
    assert _size(cards.setup_card(trend, alloc, {}, [], None, POLICY, days)) == cards.SIZE
    empty = pd.DataFrame(columns=["date", "open", "high", "low", "close"])
    assert _size(cards.setup_card(_setup(), _alloc(), RULE, EVIDENCE, None, POLICY, empty))


def test_close_card_for_a_stop_and_for_no_price() -> None:
    outcome = pd.Series(
        {
            "exit_reason": "stop",
            "exit_date": (T0 + pd.Timedelta(hours=8)).isoformat(),
            "exit_price": 1.35124,
            "gross_r": -1.0,
            "fee_r": 0.07,
            "funding_r": -0.01,
            "net_r": -1.06,
            "pnl": 0.00333 * -1.06,
        }
    )
    later = _candles(70, end=T0 + pd.Timedelta(hours=8))
    assert _size(cards.close_card(_setup(), _alloc(), outcome, RULE, POLICY, later)) == cards.SIZE
    gone = outcome.copy()
    gone["exit_reason"], gone["exit_price"], gone["net_r"], gone["pnl"] = "no_price", *[np.nan] * 3
    assert _size(cards.close_card(_setup(), _alloc(), gone, RULE, POLICY, later))


def test_weekly_card_with_and_without_closed_positions() -> None:
    week = (pd.Timestamp("2026-10-12", tz="UTC"), pd.Timestamp("2026-10-19", tz="UTC"))
    summary = {
        "week": week,
        "week_pnl": -0.0212,
        "total": -0.0212,
        "max_dd": -0.0212,
        "equity": pd.Series(
            [-0.0106, -0.0212],
            index=[T0 + pd.Timedelta(hours=4), T0 + pd.Timedelta(hours=8)],
        ),
        "open": {"n": 1, "long": 0.0, "short": 0.0025},
        "rules": [
            {
                "name": "(c) bán theo cú sập 4h",
                "new": 3,
                "states": {"sent": 2, "reduced": 0, "blocked": 1, "late": 0, "skipped": 0},
                "closed": 1,
                "closed_r": -1.06,
                "live": "trực tiếp 1 setup đóng, TB -1.06R",
                "progress": (65 / 200, "65/200 sự kiện, hạn 2028-01-01"),
            }
        ],
        "ops": "Vận hành: 40/42 chu kỳ đúng giờ · bộ ghi đủ ≥ 90%: 5/7 ngày",
        "experimental": True,
    }
    assert _size(cards.weekly_card(summary, POLICY)) == cards.SIZE
    quiet = {**summary, "equity": pd.Series(dtype=float), "rules": []}
    assert _size(cards.weekly_card(quiet, POLICY)) == cards.SIZE


def test_alert_card() -> None:
    png = cards.alert_card("runner chạy chu kỳ 2026-10-14 12:00 UTC trễ 50 phút " * 4, T0)
    assert _size(png) == cards.ALERT_SIZE
