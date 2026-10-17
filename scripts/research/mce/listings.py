"""
Ticker changes on Binance USDT-M perpetuals that mislead a study taken at face value:

- Renames (token migrations, rebrands): the old contract is settled and delisted, the new ticker
  is listed as a new contract a few days later. The new ticker is not a new listing for a study of
  listings, and the end of the old one is not an ordinary delisting.
- Reused tickers: from a given day the ticker names another token. Rows before that day belong to
  the old token. Its first candle is a new listing.
- Relisted tickers: the same token listed again after a delisting. Its first candle in the store is
  not a new listing, and the old contract's history is missing from the store.

For reused and relisted tickers the API serves only the current contract, so the store holds no
row of the old one (``rows_before_current`` checks it) and the old history is only in the
data.binance.vision archive: delisted contracts missing from the point-in-time universe of their
time (a survivorship gap for studies beyond the top 20).

The marks come from Binance announcements or from the archive's listing months
(``audit_store.py --dates-all``), confirmed by the user on 2026-10-13; dates are the first traded
day of the current contract in the store; gaps and price ratios are measured on the store
(``listing_table``, written by the audit to ``listings.csv``). ``rename_candidates`` suggests
unmarked pairs to check by hand. Reads the raw files: this module describes the store, it is not a
research loader.
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
    "EOSUSDT": "AUSDT",  # EOS -> Vaulta A, perpetual from 2025-05-28
    "TONUSDT": "GRAMUSDT",  # Toncoin TON -> Gram GRAM, 2026-06 (GRAM trades from 2026-07-02)
}
# ticker -> first day of the token it names now
REUSED: dict[str, str] = {
    "LITUSDT": "2025-12-23",
    "PUMPUSDT": "2025-07-10",  # pump.fun PUMP; an earlier PUMPUSDT (2025-04) was another token
}
# ticker -> first day of the current contract after a delisting of the same token
RELISTED: dict[str, str] = {
    "CTKUSDT": "2025-04-30",  # delisted 2024-04-01 (announced)
    "MAVIAUSDT": "2025-03-26",  # delisted 2024-12-16 (announced)
    "AIAUSDT": "2026-01-20",  # relaunched 2026-01-20 after a 2025 delisting (announced)
    # from the archive's listing months only, no announcement found
    "SLPUSDT": "2025-07-23",
    "CVCUSDT": "2025-05-16",
    "CVXUSDT": "2025-07-23",
    "TLMUSDT": "2023-03-30",
    "ICPUSDT": "2022-09-27",
    "BNXUSDT": "2023-02-22",
}
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
    "ticker_change",
    "current_from",
    "rows_before_current",
]


def is_new_listing(symbol: str) -> bool:
    """False when the ticker's first candle in the store is not a token's first listing: the new
    ticker of a rename (a migration) or a relisted contract (the same token again)."""
    return symbol not in RENAMES.values() and symbol not in RELISTED


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
    reused or relisted ticker (``ticker_change``) the first day of its current token or contract
    and the rows of any kind dated before it."""
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
    t["ticker_change"], t["rows_before_current"] = "", 0
    for change, marks in (("reused", REUSED), ("relisted", RELISTED)):
        for symbol, day in marks.items():
            hit = t.index[t["symbol"] == symbol]
            if len(hit):
                first = pd.Timestamp(day, tz="UTC")
                t.at[hit[0], "ticker_change"] = change
                t.at[hit[0], "current_from"] = first
                t.at[hit[0], "rows_before_current"] = _rows_before(pit, symbol, first)
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
