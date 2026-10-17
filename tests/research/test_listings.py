"""Ticker changes: marked renames and reused tickers, measured on the store, and the suggestion
of unmarked rename pairs."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from mce import listings
from mce.listings import is_new_listing, listing_table, rename_candidates


def _daily(store: Path, symbol: str, start: str, end: str, price: float, last: str | None = None):
    """Daily candles at ``price``; after ``last`` flat and without volume (fake candles)."""
    days = pd.date_range(start, end, freq="D", tz="UTC")
    qv = np.where(days <= pd.Timestamp(last or end, tz="UTC"), 1e6, 0.0)
    k = pd.DataFrame({"date": days, "open": price, "high": price, "low": price, "close": price})
    k["volume"], k["quote_volume"] = qv / price, qv
    (store / "klines_1d").mkdir(parents=True, exist_ok=True)
    k.to_parquet(store / "klines_1d" / f"{symbol}.parquet", index=False)


@pytest.fixture
def store(tmp_path: Path) -> Path:
    _daily(tmp_path, "OLDUSDT", "2024-01-01", "2024-06-30", 2.0, last="2024-03-10")
    _daily(tmp_path, "NEWUSDT", "2024-03-15", "2024-06-30", 2.2)  # 5 days later, ratio 1.1
    _daily(tmp_path, "CHEAPUSDT", "2024-03-14", "2024-06-30", 0.02)  # in time, price far off
    _daily(tmp_path, "LATEUSDT", "2024-05-01", "2024-06-30", 2.0)  # price close, too late
    _daily(tmp_path, "REUSEUSDT", "2024-01-01", "2024-06-30", 1.0)
    (tmp_path / "funding").mkdir()
    dates = pd.date_range("2024-01-20", periods=30, freq="8h", tz="UTC")
    pd.DataFrame({"date": dates, "funding_rate": 1e-4}).to_parquet(
        tmp_path / "funding" / "REUSEUSDT.parquet", index=False
    )
    return tmp_path


def test_shipped_marks() -> None:
    assert listings.RENAMES == {
        "MATICUSDT": "POLUSDT",
        "RNDRUSDT": "RENDERUSDT",
        "FTMUSDT": "SUSDT",
        "BNXUSDT": "FORMUSDT",
        "EOSUSDT": "AUSDT",
        "TONUSDT": "GRAMUSDT",
    }
    assert listings.REUSED == {"LITUSDT": "2025-12-23", "PUMPUSDT": "2025-07-10"}
    assert listings.RELISTED == {
        "CTKUSDT": "2025-04-30",
        "MAVIAUSDT": "2025-03-26",
        "AIAUSDT": "2026-01-20",
        "SLPUSDT": "2025-07-23",
        "CVCUSDT": "2025-05-16",
        "CVXUSDT": "2025-07-23",
        "TLMUSDT": "2023-03-30",
        "ICPUSDT": "2022-09-27",
        "BNXUSDT": "2023-02-22",
    }
    assert not is_new_listing("POLUSDT") and not is_new_listing("GRAMUSDT")
    assert is_new_listing("LITUSDT") and is_new_listing("PUMPUSDT")  # another token
    assert not is_new_listing("CTKUSDT")  # the same token back after a delisting


def test_unmarked_rename_is_suggested_and_marked_one_is_measured(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(listings, "RENAMES", {})
    monkeypatch.setattr(listings, "REUSED", {})
    t = listing_table(store).set_index("symbol")
    assert t.loc["OLDUSDT", "last_traded"] == pd.Timestamp("2024-03-10", tz="UTC")
    assert t.loc["OLDUSDT", "status"] == "stopped" and t.loc["NEWUSDT", "status"] == "trading"
    c = rename_candidates(t.reset_index())
    assert c[["old", "new"]].values.tolist() == [["OLDUSDT", "NEWUSDT"]]
    assert c["gap_days"].iloc[0] == 5 and c["price_ratio"].iloc[0] == pytest.approx(1.1)
    assert is_new_listing("NEWUSDT")

    monkeypatch.setattr(listings, "RENAMES", {"OLDUSDT": "NEWUSDT"})
    t = listing_table(store).set_index("symbol")
    assert t.loc["OLDUSDT", "renamed_to"] == "NEWUSDT"
    assert t.loc["NEWUSDT", "renamed_from"] == "OLDUSDT"
    assert t.loc["NEWUSDT", "rename_gap_days"] == 5
    assert t.loc["NEWUSDT", "rename_price_ratio"] == pytest.approx(1.1)
    assert rename_candidates(t.reset_index()).empty  # marked pairs are not suggested again
    assert not is_new_listing("NEWUSDT") and is_new_listing("LATEUSDT")


def test_rows_before_the_current_token_or_contract(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(listings, "REUSED", {"REUSEUSDT": "2024-02-01"})
    monkeypatch.setattr(listings, "RELISTED", {"NEWUSDT": "2024-03-15", "LATEUSDT": "2024-06-01"})
    t = listing_table(store).set_index("symbol")
    assert t.loc["REUSEUSDT", "current_from"] == pd.Timestamp("2024-02-01", tz="UTC")
    assert t.loc["REUSEUSDT", "ticker_change"] == "reused"
    # the 31 January candles and the 30 prints from 01-20 00:00 to 01-29 16:00: the old token's
    assert t.loc["REUSEUSDT", "rows_before_current"] == 31 + 30
    assert t.loc["NEWUSDT", "ticker_change"] == "relisted"
    assert t.loc["NEWUSDT", "rows_before_current"] == 0  # the store holds only the new contract
    assert t.loc["LATEUSDT", "rows_before_current"] == 31  # May: rows of an older contract
    assert t.loc["OLDUSDT", "ticker_change"] == "" and t.loc["OLDUSDT", "rows_before_current"] == 0
    assert not is_new_listing("NEWUSDT") and is_new_listing("OLDUSDT")
