"""Data-store audit: every check finds a planted defect and stays silent on clean data (no
network: the verification sources are fakes)."""

from pathlib import Path

import numpy as np
import pandas as pd
from audit_store import main as audit_main
from mce.audit import (
    Sources,
    apply_known,
    compare_frames,
    cross_check_daily,
    scan_funding,
    scan_klines,
    scan_metrics,
    verify_sample,
)
from mce.pit import synthetic_store


def _bars(n: int, freq: str = "1D", seed: int = 0, start: str = "2024-01-01") -> pd.DataFrame:
    """Valid OHLCV candles with quote volume."""
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    open_ = np.r_[close[0], close[:-1]]
    spread = np.abs(rng.normal(0, 0.01, n)) * close
    volume = rng.uniform(1, 10, n)
    return pd.DataFrame(
        {
            "date": pd.date_range(start, periods=n, freq=freq, tz="UTC"),
            "open": open_,
            "high": np.maximum(open_, close) + spread,
            "low": np.minimum(open_, close) - spread,
            "close": close,
            "volume": volume,
            "quote_volume": volume * close,
        }
    )


def _flatten(df: pd.DataFrame, rows: slice, price: float) -> None:
    """Zero-volume candles at a constant price: what the API serves after a delisting."""
    df.loc[rows, ["open", "high", "low", "close"]] = price
    df.loc[rows, ["volume", "quote_volume"]] = 0.0


def _checks(issues: pd.DataFrame) -> list[str]:
    return sorted(issues["check"]) if len(issues) else []


# ------------------------------------------------------------------ 1-4 candles


def test_clean_candles_have_no_issue() -> None:
    df = _bars(200)
    summary, issues = scan_klines(df, "1d", "AUSDT")
    assert issues.empty
    assert summary["first"] == df["date"].iloc[0] and summary["last"] == df["date"].iloc[-1]
    assert summary["last_traded"] == df["date"].iloc[-1] and summary["zombie_rows"] == 0


def test_empty_file() -> None:
    summary, issues = scan_klines(_bars(5).iloc[:0], "1d", "AUSDT")
    assert _checks(issues) == ["empty"] and issues["severity"].iloc[0] == "info"
    assert summary["rows"] == 0 and pd.isna(summary["last_traded"])


def test_zombie_tail_after_delisting() -> None:
    df = _bars(200)
    _flatten(df, df.index[-30:], df["close"].iloc[-31])
    summary, issues = scan_klines(df, "1d", "AUSDT")
    assert _checks(issues) == ["zombie_tail"]
    row = issues.iloc[0]
    assert row["date"] == df["date"].iloc[-30] and row["severity"] == "warning"
    assert summary["zombie_rows"] == 30 and summary["last_traded"] == df["date"].iloc[-31]


def test_gap_duplicate_unsorted() -> None:
    df = _bars(200)
    _, issues = scan_klines(df.drop(index=50), "1d")
    assert _checks(issues) == ["gap"] and issues["date"].iloc[0] == df["date"].iloc[50]

    _, issues = scan_klines(pd.concat([df, df.iloc[[60]]]), "1d")
    assert _checks(issues) == ["duplicate"] and issues["date"].iloc[0] == df["date"].iloc[60]
    assert issues["severity"].iloc[0] == "critical"

    swapped = df.iloc[[*range(70), 71, 70, *range(72, 200)]]
    _, issues = scan_klines(swapped, "1d")
    assert _checks(issues) == ["unsorted"] and issues["date"].iloc[0] == df["date"].iloc[70]


def test_impossible_prices() -> None:
    df = _bars(200)
    df.loc[80, "high"] = df.loc[80, "low"] - 1
    df.loc[90, "close"] = df.loc[90, "high"] * 1.1
    df.loc[100, "close"] = np.nan
    df.loc[110, "low"] = 0.0
    _, issues = scan_klines(df, "1d")
    assert set(issues["check"]) == {"bad_ohlc"}
    assert set(issues["date"]) == set(df["date"].iloc[[80, 90, 100, 110]])
    assert (issues["severity"] == "critical").all()


def test_relisted_ticker_and_trading_halt() -> None:
    df = _bars(200)
    _flatten(df, slice(100, 109), df["close"].iloc[99])
    df.loc[110:, ["open", "high", "low", "close", "quote_volume"]] *= 10
    _, issues = scan_klines(df, "1d")
    assert _checks(issues) == ["relist_suspect"] and issues["date"].iloc[0] == df["date"].iloc[110]

    df = _bars(200)
    _flatten(df, slice(100, 109), df["close"].iloc[99])
    _, issues = scan_klines(df, "1d")
    assert _checks(issues) == ["halt"] and issues["severity"].iloc[0] == "info"

    df = _bars(200)
    _flatten(df, slice(100, 102), df["close"].iloc[99])  # 3 days: below the 7-day threshold
    assert scan_klines(df, "1d")[1].empty
    h4 = _bars(400, freq="4h")
    _flatten(h4, slice(100, 141), h4["close"].iloc[99])  # 42 bars = 7 days at 4h
    assert _checks(scan_klines(h4, "4h")[1]) == ["halt"]


def test_known_issues_are_downgraded() -> None:
    issues = pd.DataFrame(
        [
            [
                "klines_1d",
                "BNBUSDT",
                "d1_h4_mismatch",
                "2024-03-28 00:00:00+00:00",
                "x",
                "critical",
            ],
            [
                "klines_1d",
                "BNBUSDT",
                "d1_h4_mismatch",
                "2024-03-29 00:00:00+00:00",
                "x",
                "critical",
            ],
        ],
        columns=["kind", "symbol", "check", "date", "detail", "severity"],
    )
    issues["date"] = pd.to_datetime(issues["date"])
    known = pd.DataFrame(
        {
            "kind": ["klines_1d"],
            "symbol": ["BNBUSDT"],
            "check": ["d1_h4_mismatch"],
            "date": ["2024-03-28"],
            "reason": ["API 4h volume differs from the archive; store equals the API"],
        }
    )
    out = apply_known(issues, known)
    assert out["severity"].tolist() == ["known", "critical"]  # only the listed day
    assert "archive" in out["detail"].iloc[0]
    assert apply_known(issues, known.iloc[:0])["severity"].tolist() == ["critical", "critical"]


# ------------------------------------------------------------------ 5 funding


def _funding() -> pd.DataFrame:
    """30 prints every 8h, then 60 prints every 4h (Binance shortened the interval)."""
    t8 = pd.date_range("2024-01-01", periods=30, freq="8h", tz="UTC")
    t4 = pd.date_range(t8[-1] + pd.Timedelta(hours=4), periods=60, freq="4h")
    return pd.DataFrame({"date": t8.append(t4), "funding_rate": 0.0001})


def test_funding_interval_change_is_not_a_gap() -> None:
    summary, issues = scan_funding(_funding(), "AUSDT")
    assert issues.empty and summary["rows"] == 90


def test_funding_defects() -> None:
    f = _funding()
    _, issues = scan_funding(f.drop(index=10), "AUSDT")
    assert _checks(issues) == ["gap"] and issues["date"].iloc[0] == f["date"].iloc[10]

    _, issues = scan_funding(pd.concat([f, f.iloc[[5]]]), "AUSDT")
    assert _checks(issues) == ["duplicate"]

    spiky = f.copy()
    spiky.loc[5, "funding_rate"] = 0.05
    _, issues = scan_funding(spiky, "AUSDT")
    assert _checks(issues) == ["outlier"] and issues["date"].iloc[0] == f["date"].iloc[5]

    last_traded = f["date"].iloc[20].floor("D")
    after = f["date"] > last_traded + pd.Timedelta(days=1)
    _, issues = scan_funding(f, "AUSDT", last_traded=last_traded)
    assert _checks(issues) == ["after_delist"]
    assert issues["date"].iloc[0] == f.loc[after, "date"].iloc[0]
    assert str(int(after.sum())) in issues["detail"].iloc[0]


# ------------------------------------------------------------------ 6 metrics


def _metrics(hours: int = 120) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01 00:50", periods=hours, freq="1h", tz="UTC")
    return pd.DataFrame({"date": dates, "oi_value": 1e6, "taker_ls": 1.0})


def test_metrics_clean_and_defects() -> None:
    m = _metrics()
    summary, issues = scan_metrics(m, "AUSDT")
    assert issues.empty and summary["missing_hours"] == 0

    summary, issues = scan_metrics(m.drop(index=[40, 41, 42]), "AUSDT")
    assert _checks(issues) == ["gap"] and summary["missing_hours"] == 3
    assert issues["date"].iloc[0] == m["date"].iloc[40]

    zero = m.copy()
    zero.loc[7, "oi_value"] = 0.0
    _, issues = scan_metrics(zero, "AUSDT")
    assert _checks(issues) == ["zero_oi"] and issues["date"].iloc[0] == m["date"].iloc[7]

    extra = pd.DataFrame({"date": [m["date"].iloc[9] + pd.Timedelta(minutes=5)], "oi_value": 1e6})
    _, issues = scan_metrics(pd.concat([m, extra]).sort_values("date"), "AUSDT")
    assert _checks(issues) == ["wrong_minute"] and issues["date"].iloc[0] == extra["date"].iloc[0]


# ------------------------------------------------------------------ 7 daily vs 4h


def _daily_from_4h(h4: pd.DataFrame) -> pd.DataFrame:
    g = h4.set_index("date").resample("1D")
    d = pd.DataFrame(
        {
            "open": g["open"].first(),
            "high": g["high"].max(),
            "low": g["low"].min(),
            "close": g["close"].last(),
            "volume": g["volume"].sum(),
            "quote_volume": g["quote_volume"].sum(),
        }
    )
    return d.reset_index()


def test_daily_candles_match_4h_candles() -> None:
    h4 = _bars(6 * 20, freq="4h")
    d1 = _daily_from_4h(h4)
    assert cross_check_daily(d1, h4, "AUSDT").empty

    bad = d1.copy()
    bad.loc[5, "volume"] *= 1.5
    issues = cross_check_daily(bad, h4, "AUSDT")
    assert _checks(issues) == ["d1_h4_mismatch"] and issues["date"].iloc[0] == d1["date"].iloc[5]
    assert "volume" in issues["detail"].iloc[0]

    assert cross_check_daily(d1, h4.iloc[:-2], "AUSDT").empty  # last day has only 4 bars


# ------------------------------------------------------------------ 8 compare with a source


def test_compare_frames() -> None:
    a = _bars(20)[["date", "close", "volume"]]
    cols = ["close", "volume"]
    assert compare_frames(a, a.copy(), cols).empty

    b = a.copy()
    b.loc[3, "close"] *= 1.01
    b.loc[4, "close"] *= 1 + 1e-12  # rounding noise, within tolerance
    diff = compare_frames(a, b, cols)
    assert len(diff) == 1
    assert diff.iloc[0][["date", "column", "status"]].tolist() == [a["date"][3], "close", "value"]

    diff = compare_frames(a.drop(index=5), a.drop(index=6), cols)
    status = dict(zip(diff["date"], diff["status"], strict=True))
    assert status == {a["date"][5]: "missing_in_store", a["date"][6]: "missing_in_source"}


# ------------------------------------------------------------------ 9 verification, fake sources


def _read(store: Path, kind: str, symbol: str) -> pd.DataFrame:
    return pd.read_parquet(store / kind / f"{symbol}.parquet")


def _window(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    return df[(df["date"] >= start) & (df["date"] < end)].reset_index(drop=True)


def _month(month: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    start = pd.Timestamp(f"{month}-01", tz="UTC")
    return start, start + pd.offsets.MonthBegin(1)


def _fake_sources(store: Path, altered: list) -> Sources:
    """Sources that serve the store's own rows; the first archive month served is altered once."""

    def archive_klines(symbol: str, interval: str, month: str) -> pd.DataFrame:
        df = _window(_read(store, f"klines_{interval}", symbol), *_month(month))
        if not altered:
            df.loc[0, "close"] *= 1.05
            altered.append((symbol, month))
        return df

    def months(symbol: str, interval: str) -> list[str]:
        d = _read(store, f"klines_{interval}", symbol)["date"]
        return sorted(d.dt.strftime("%Y-%m").unique())

    return Sources(
        api_klines=lambda s, i, a, b: _window(_read(store, f"klines_{i}", s), a, b),
        archive_klines=archive_klines,
        api_funding=lambda s, a, b: _window(_read(store, "funding", s), a, b),
        archive_funding=lambda s, m: _window(_read(store, "funding", s), *_month(m)),
        archive_metrics=lambda s, d: _window(
            _read(store, "metrics", s),
            pd.Timestamp(d, tz="UTC"),
            pd.Timestamp(d, tz="UTC") + pd.Timedelta(days=1),
        ),
        archive_months=months,
    )


def test_verify_reports_exactly_the_altered_value(tmp_path: Path) -> None:
    synthetic_store(tmp_path, n_coins=3, start="2024-01-01", end="2024-04-01")
    empty = _read(tmp_path, "klines_1d", "C00USDT").iloc[:0]  # coin without rows in the window
    empty.to_parquet(tmp_path / "klines_1d" / "C09USDT.parquet", index=False)
    altered: list = []
    now = pd.Timestamp("2024-04-15", tz="UTC")
    samples, issues = verify_sample(tmp_path, _fake_sources(tmp_path, altered), 4, 0, now=now)
    assert set(samples["kind"]) == {"klines_1d", "klines_4h", "funding", "metrics", "dates"}
    assert "C09USDT" not in set(samples["symbol"])
    assert len(altered) == 1 and len(issues) == 1
    row = issues.iloc[0]
    assert row["check"] == "source_mismatch" and row["severity"] == "critical"
    assert (
        row["symbol"] == altered[0][0] and "archive" in row["detail"] and "close" in row["detail"]
    )
    again, _ = verify_sample(tmp_path, _fake_sources(tmp_path, [None]), 4, 0, now=now)
    picked = ["kind", "symbol", "period"]
    pd.testing.assert_frame_equal(samples[picked], again[picked])  # same seed, same sample


# ------------------------------------------------------------------ 10 command line, end to end


def _plant_defects(store: Path) -> None:
    """C00: duplicated daily row. C01: one missing 4h bar. C02: delisted 10 days before the end
    (flat zero-volume candles in both timeframes, funding still printed)."""
    d = _read(store, "klines_1d", "C00USDT")
    pd.concat([d, d.iloc[[40]]]).to_parquet(store / "klines_1d" / "C00USDT.parquet", index=False)
    h = _read(store, "klines_4h", "C01USDT")
    h.drop(index=300).to_parquet(store / "klines_4h" / "C01USDT.parquet", index=False)
    d, h = _read(store, "klines_1d", "C02USDT"), _read(store, "klines_4h", "C02USDT")
    price = d["close"].iloc[-11]
    _flatten(d, d.index[-10:], price)
    _flatten(h, h.index[-60:], price)
    d.to_parquet(store / "klines_1d" / "C02USDT.parquet", index=False)
    h.to_parquet(store / "klines_4h" / "C02USDT.parquet", index=False)
    # C03 trades on: prints of the day after its last closed daily candle are normal
    f = _read(store, "funding", "C03USDT")
    after = d["date"].iloc[-1] + pd.to_timedelta([24, 32], unit="h")
    today = pd.DataFrame({"date": after, "funding_rate": 1e-4})
    pd.concat([f, today]).to_parquet(store / "funding" / "C03USDT.parquet", index=False)


def test_command_line_finds_planted_defects(tmp_path: Path) -> None:
    store, out = tmp_path / "store", tmp_path / "out"
    synthetic_store(store, n_coins=4, start="2024-01-01", end="2024-07-01")
    args = ["--store", str(store), "--out", str(out), "--log", str(tmp_path / "audit.log")]
    args += ["--known", str(tmp_path / "no_known_issues.csv")]
    assert audit_main(args) == 0  # clean synthetic store

    _plant_defects(store)
    assert audit_main(args) == 1  # a duplicate is critical
    run = out / "latest" / "store"
    issues = pd.read_csv(run / "issues.csv")
    found = set(zip(issues["symbol"], issues["kind"], issues["check"], strict=True))
    assert found == {
        ("C00USDT", "klines_1d", "duplicate"),
        ("C01USDT", "klines_4h", "gap"),
        ("C02USDT", "klines_1d", "zombie_tail"),
        ("C02USDT", "klines_4h", "zombie_tail"),
        ("C02USDT", "funding", "after_delist"),
    }
    zombies = pd.read_csv(run / "zombie_members.csv")
    assert set(zombies["symbol"]) == {"C02USDT"} and len(zombies) == 10
    symbols = pd.read_csv(run / "symbols.csv")
    assert len(symbols) == 4 * 4  # 4 coins x 4 kinds
    assert (run / "summary.md").read_text(encoding="utf-8").strip()
