"""Clean reads of the store: Binance keeps serving flat zero-volume candles and funding prints for
delisted contracts; research reads drop them, the raw files stay as downloaded."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from mce.archive import drop_dead_tail, load_wide, read_store_file, traded, trading_span


UTC = "UTC"


def _klines(start: str, volumes: list[float], freq: str = "1D") -> pd.DataFrame:
    """Candles whose close counts up (1, 2, ...) except after a zero-volume candle, which repeats
    the previous close (flat, like Binance's fake candles)."""
    close = []
    for i, v in enumerate(volumes):
        close.append(float(i + 1) if v > 0 or i == 0 else close[-1])
    c = np.array(close)
    return pd.DataFrame(
        {
            "date": pd.date_range(start, periods=len(volumes), freq=freq, tz=UTC),
            "open": c,
            "high": c,
            "low": c,
            "close": c,
            "volume": np.array(volumes) / 10,
            "quote_volume": np.array(volumes, dtype=float),
        }
    )


def _write(store: Path, kind: str, symbol: str, df: pd.DataFrame) -> None:
    (store / kind).mkdir(parents=True, exist_ok=True)
    df.to_parquet(store / kind / f"{symbol}.parquet", index=False)


def _funding(start: str, n: int) -> pd.DataFrame:
    dates = pd.date_range(start, periods=n, freq="8h", tz=UTC)
    return pd.DataFrame({"date": dates, "funding_rate": np.arange(1, n + 1) * 1e-4})


def test_traded_and_dead_tail() -> None:
    k = _klines("2026-06-20", [5, 0, 7, 3, 0, 0, 0])  # a halt inside, a fake tail after 06-23
    assert traded(k).tolist() == [True, False, True, True, False, False, False]
    kept = drop_dead_tail(k)
    assert kept["date"].iloc[-1] == pd.Timestamp("2026-06-23", tz=UTC)
    assert len(kept) == 4 and kept["quote_volume"].iloc[1] == 0  # the interior zero stays
    assert drop_dead_tail(_klines("2026-06-20", [0, 0])).empty  # never traded in the window
    alive = _klines("2026-06-20", [1, 2])
    pd.testing.assert_frame_equal(drop_dead_tail(alive), alive)


@pytest.fixture
def store(tmp_path: Path) -> Path:
    # DEAD: traded 06-20 .. 06-23 (4h candles to 06-23 08:00), fake candles and prints after
    _write(tmp_path, "klines_1d", "DEADUSDT", _klines("2026-06-20", [5, 6, 7, 3, 0, 0, 0]))
    _write(
        tmp_path,
        "klines_4h",
        "DEADUSDT",
        _klines("2026-06-20", [1.0] * 21 + [0.0] * 21, freq="4h"),  # last trade 06-23 08:00
    )
    _write(tmp_path, "funding", "DEADUSDT", _funding("2026-06-20", 21))  # to 06-26 16:00
    # ONLY1D: no 4h file, funding cut by the daily candles
    _write(tmp_path, "klines_1d", "ONLY1DUSDT", _klines("2026-06-20", [5, 6, 0, 0]))
    _write(tmp_path, "funding", "ONLY1DUSDT", _funding("2026-06-20", 12))
    # NOKLINES: funding without any candle file is left as stored
    _write(tmp_path, "funding", "NOKLINESUSDT", _funding("2026-06-20", 3))
    # REUSED: the candles start 06-22 (a reused ticker), the prints from 06-20 (the old contract's
    # included, as the funding endpoint serves them), also under a second funding kind
    bars = _klines("2026-06-22", [0.0] + [1.0] * 11, freq="4h")  # 00:00 idle, 04:00 first trade
    _write(tmp_path, "klines_4h", "REUSEDUSDT", bars)
    _write(tmp_path, "klines_1d", "REUSEDUSDT", _klines("2026-06-22", [5, 6]))
    for kind in ("funding", "funding_all"):
        _write(tmp_path, kind, "REUSEDUSDT", _funding("2026-06-20", 12))  # to 06-23 16:00
    # LATE4H: trades from 06-20 (daily candles) but its 4h candles are stored from 06-22 only
    # (the store keeps 4h from 100 days before a coin first enters the top 25): no print is lost
    _write(tmp_path, "klines_1d", "LATE4HUSDT", _klines("2026-06-20", [5, 6, 7, 8]))
    _write(tmp_path, "klines_4h", "LATE4HUSDT", _klines("2026-06-22", [1.0] * 12, freq="4h"))
    _write(tmp_path, "funding", "LATE4HUSDT", _funding("2026-06-20", 12))
    return tmp_path


def test_trading_span_starts_on_daily_and_ends_on_4h_candles(store: Path) -> None:
    def span(symbol: str, end: pd.Timestamp | None = None):
        return trading_span(store, symbol, end)

    assert span("DEADUSDT") == (
        pd.Timestamp("2026-06-20", tz=UTC),
        pd.Timestamp("2026-06-23 12:00", tz=UTC),
    )
    assert span("ONLY1DUSDT")[1] == pd.Timestamp("2026-06-22", tz=UTC)
    assert span("NOKLINESUSDT") is None
    # with data cut before 06-21 06:00 the last trade seen is the 04:00 candle
    cut = pd.Timestamp("2026-06-21 06:00", tz=UTC)
    assert span("DEADUSDT", cut)[1] == pd.Timestamp("2026-06-21 08:00", tz=UTC)
    # the start comes from the daily candles (stored for every coin from the store's start), the
    # end from the 4h ones (finer) when stored
    assert span("REUSEDUSDT")[0] == pd.Timestamp("2026-06-22", tz=UTC)
    assert span("LATE4HUSDT") == (
        pd.Timestamp("2026-06-20", tz=UTC),
        pd.Timestamp("2026-06-24", tz=UTC),
    )


def test_funding_before_the_first_trade_is_dropped(store: Path) -> None:
    for kind in ("funding", "funding_all"):
        f = read_store_file(store, kind, "REUSEDUSDT")
        # the old contract's prints of 06-20 and 06-21 go; from 06-22 00:00 they stay
        assert f["date"].min() == pd.Timestamp("2026-06-22", tz=UTC), kind
        assert f["date"].max() == pd.Timestamp("2026-06-23 16:00", tz=UTC), kind
        assert len(read_store_file(store, kind, "REUSEDUSDT", raw=True)) == 12
    assert len(read_store_file(store, "funding", "LATE4HUSDT")) == 12  # nothing lost


def test_clean_reads_drop_fake_candles_and_prints(store: Path) -> None:
    d1 = read_store_file(store, "klines_1d", "DEADUSDT")
    assert d1["date"].max() == pd.Timestamp("2026-06-23", tz=UTC)
    raw = read_store_file(store, "klines_1d", "DEADUSDT", raw=True)
    assert len(raw) == 7
    f = read_store_file(store, "funding", "DEADUSDT")
    assert f["date"].max() == pd.Timestamp("2026-06-23 08:00", tz=UTC)  # <= 12:00 close
    f1 = read_store_file(store, "funding", "ONLY1DUSDT")
    assert f1["date"].max() == pd.Timestamp("2026-06-22", tz=UTC)  # print at the close kept
    assert len(read_store_file(store, "funding", "NOKLINESUSDT")) == 3
    assert read_store_file(store, "klines_1d", "MISSINGUSDT") is None


def test_clean_read_cuts_at_end_before_cleaning(store: Path) -> None:
    # a cut inside the fake tail: only the candles before it are read, then the tail is dropped
    end = pd.Timestamp("2026-06-25", tz=UTC)
    d1 = read_store_file(store, "klines_1d", "DEADUSDT", end)
    assert d1["date"].max() == pd.Timestamp("2026-06-23", tz=UTC)
    early = pd.Timestamp("2026-06-22", tz=UTC)
    assert read_store_file(store, "klines_1d", "DEADUSDT", early)["date"].max() == pd.Timestamp(
        "2026-06-21", tz=UTC
    )


def test_load_wide_is_clean_unless_raw(store: Path) -> None:
    syms = ["DEADUSDT", "ONLY1DUSDT", "MISSINGUSDT"]
    clean = load_wide(store, "klines_1d", "close", syms)
    raw = load_wide(store, "klines_1d", "close", syms, raw=True)
    assert list(clean.columns) == ["DEADUSDT", "ONLY1DUSDT"]
    assert clean.index.max() == pd.Timestamp("2026-06-23", tz=UTC)  # no fake day left
    assert clean.loc["2026-06-22":, "ONLY1DUSDT"].isna().all()
    assert raw["DEADUSDT"].notna().sum() == 7
    cut = load_wide(store, "klines_1d", "close", syms, end=pd.Timestamp("2026-06-22", tz=UTC))
    assert cut.index.max() == pd.Timestamp("2026-06-21", tz=UTC)
    fund = load_wide(store, "funding", "funding_rate", ["DEADUSDT"])
    assert fund.index.max() == pd.Timestamp("2026-06-23 08:00", tz=UTC)
