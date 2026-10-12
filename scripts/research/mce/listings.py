"""
Ticker changes on Binance USDT-M perpetuals that mislead a study taken at face value:

- Renames (token migrations, rebrands): the old contract is settled and delisted, the new ticker
  is listed as a new contract a few days later. The new ticker is not a new listing for a study of
  listings, and the end of the old one is not an ordinary delisting.
- Reused tickers: from a given day the ticker names another token. Rows before that day belong to
  the old token (the API serves only the new one, so the store usually holds none; the old history
  is only in the data.binance.vision archive).

The marks are facts from Binance announcements, checked by the user; dates, gaps and price
ratios are measured on the store (``listing_table``, written by ``scripts/research/audit_store.py``
to ``listings.csv``). ``rename_candidates`` suggests unmarked pairs to check by hand. Reads the raw
files: this module describes the store, it is not a research loader.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd


# old ticker -> new ticker (1 old token swapped for 1 new one in each case)
RENAMES: dict[str, str] = {
    "MATICUSDT": "POLUSDT",  # Polygon MATIC -> POL, 2024-09
    "RNDRUSDT": "RENDERUSDT",  # Render RNDR -> RENDER, 2024-07
    "FTMUSDT": "SUSDT",  # Fantom FTM -> Sonic S, 2025-01
    "BNXUSDT": "FORMUSDT",  # BinaryX BNX -> Four FORM, 2025-03
}
# ticker -> first day of the token it names now (its first candle in the store)
REUSED: dict[str, str] = {"LITUSDT": "2025-12-23"}
STOPPED_AFTER = pd.Timedelta(days=7)  # no trade for longer than this before the store's last day
KINDS = ("klines_1d", "klines_4h", "funding", "metrics", "metrics_api")
COLS = [
    "symbol",
    "first_traded",
    "last_traded",
    "last_row",
    "status",
    "close_first",
    "close_last",
    "renamed_from",
    "renamed_to",
    "rename_gap_days",
    "rename_price_ratio",
    "reused_from",
    "rows_before_reuse",
]


def is_new_listing(symbol: str) -> bool:
    """False for the new ticker of a rename: its first candle is a migration, not a listing."""
    return symbol not in RENAMES.values()


def _symbols(pit: Path) -> list[str]:
    meta = pit / "symbols.json"
    if meta.exists():
        return list(json.loads(meta.read_text(encoding="utf-8"))["candidates"])
    return sorted(p.stem for p in (pit / "klines_1d").glob("*.parquet"))


def _rows_before(pit: Path, symbol: str, day: pd.Timestamp) -> int:
    n = 0
    for kind in KINDS:
        path = pit / kind / f"{symbol}.parquet"
        if path.exists():
            n += int((pd.read_parquet(path, columns=["date"])["date"] < day).sum())
    return n


def listing_table(pit: Path) -> pd.DataFrame:
    """One row per symbol with daily candles: first and last day with a trade, last stored row,
    status (``stopped`` when the last trade is more than 7 days before the store's last day, else
    ``trading``; ``empty`` without any trade), closes of the first and last traded days, the rename
    links with the gap in days and the price ratio (new first close / old last close), and for a
    reused ticker the rows of any kind dated before its new token."""
    rows = []
    for symbol in _symbols(pit):
        path = pit / "klines_1d" / f"{symbol}.parquet"
        if not path.exists():
            continue
        k = pd.read_parquet(path).sort_values("date")
        traded = k[k["quote_volume"] > 0]
        row = dict.fromkeys(COLS)
        row["symbol"] = symbol
        row["last_row"] = k["date"].max() if len(k) else pd.NaT
        if len(traded):
            row["first_traded"], row["last_traded"] = traded["date"].iloc[[0, -1]]
            row["close_first"], row["close_last"] = traded["close"].iloc[[0, -1]]
        rows.append(row)
    t = pd.DataFrame(rows, columns=COLS)
    latest = t["last_row"].max()
    t["status"] = np.where(
        t["last_traded"].isna(),
        "empty",
        np.where(t["last_traded"] < latest - STOPPED_AFTER, "stopped", "trading"),
    )
    by = t.set_index("symbol")
    for old, new in RENAMES.items():
        if old not in by.index or new not in by.index:
            continue
        gap = (by.at[new, "first_traded"] - by.at[old, "last_traded"]).days
        ratio = by.at[new, "close_first"] / by.at[old, "close_last"]
        for sym, link, other in ((old, "renamed_to", new), (new, "renamed_from", old)):
            i = t.index[t["symbol"] == sym][0]
            t.at[i, link] = other
            t.at[i, "rename_gap_days"], t.at[i, "rename_price_ratio"] = gap, ratio
    t["rows_before_reuse"] = 0
    for symbol, day in REUSED.items():
        hit = t.index[t["symbol"] == symbol]
        if len(hit):
            first = pd.Timestamp(day, tz="UTC")
            t.at[hit[0], "reused_from"] = first
            t.at[hit[0], "rows_before_reuse"] = _rows_before(pit, symbol, first)
    return t


def rename_candidates(
    table: pd.DataFrame, max_gap_days: int = 14, max_ratio: float = 1.15
) -> pd.DataFrame:
    """Unmarked pairs that look like a rename: a stopped ticker and one whose first trade comes
    1 to ``max_gap_days`` days after its last trade, at a first close within a factor
    ``max_ratio`` of its last close. Suggestions only: a pair is a rename when Binance said so."""
    marked = set(RENAMES.items())
    old = table[table["status"] == "stopped"]
    new = table[table["first_traded"].notna()]
    out = []
    for o in old.itertuples():
        gap = (new["first_traded"] - o.last_traded).dt.days
        ratio = new["close_first"] / o.close_last
        hit = new[(gap >= 1) & (gap <= max_gap_days) & ratio.between(1 / max_ratio, max_ratio)]
        for n in hit.itertuples():
            if (o.symbol, n.symbol) not in marked:
                out.append(
                    {
                        "old": o.symbol,
                        "new": n.symbol,
                        "old_last_traded": o.last_traded,
                        "new_first_traded": n.first_traded,
                        "gap_days": int(gap[n.Index]),
                        "price_ratio": float(ratio[n.Index]),
                    }
                )
    cols = ["old", "new", "old_last_traded", "new_first_traded", "gap_days", "price_ratio"]
    return pd.DataFrame(out, columns=cols)
