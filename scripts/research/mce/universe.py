"""
Research universe: ``point_in_time_members`` ranks coins by liquidity using only data available
at each date, over a candidate list that includes delisted contracts (see ``mce.archive``).
Never test on a list of coins chosen today (survivorship bias).
"""

from collections.abc import Iterable

import numpy as np
import pandas as pd


# Binance tags every perp with an underlying type (COIN, EQUITY, COMMODITY, INDEX, FX, PREMARKET)
# and TradFi contracts also carry a "TradFi" subtype. These bases are not crypto exposure but are
# tagged COIN, or are index contracts already removed from exchangeInfo. Excluded by asset class,
# never by results.
EXCLUDED_BASES = frozenset(
    {
        # stablecoins and fiat-pegged tokens
        "USDC",
        "BUSD",
        "TUSD",
        "FDUSD",
        "USDP",
        "DAI",
        "USDE",
        "USD1",
        # tokenized gold
        "PAXG",
        "XAUT",
        # index contracts no longer listed
        "FOOTBALL",
        "BLUEBIRD",
    }
)


def is_candidate(
    symbol: str, underlying_type: str | None = None, subtypes: Iterable[str] = ()
) -> bool:
    """
    USDT-margined perpetual on a crypto asset. ``underlying_type`` / ``subtypes`` come from
    exchangeInfo; ``None`` means the contract was already removed from it (old crypto listings).
    """
    if not symbol.endswith("USDT") or "_" in symbol or symbol[:-4] in EXCLUDED_BASES:
        return False
    return underlying_type in (None, "COIN") and "TradFi" not in subtypes


def point_in_time_members(
    close: pd.DataFrame,
    quote_volume: pd.DataFrame,
    n: int = 15,
    volume_window: int = 30,
    min_history: int = 365,
    rebalance: str = "M",
) -> pd.DataFrame:
    """
    Boolean membership (rows = calendar days, columns = symbols), causal: row t uses data <= t.

    Eligible on day t: ``min_history`` consecutive daily closes up to t. Ranked by the median
    quote volume of the last ``volume_window`` days; the top ``n`` are members.
    ``rebalance="M"``: ranking made at the close of the last calendar day of each month holds
    until the next month end; ``"D"``: re-ranked every day. A member without a close on day t
    (delisted or missing) is dropped that day and not replaced until the next ranking.
    ``close`` must be on a continuous daily index; ``quote_volume`` is aligned to it.
    """
    has = close.notna()
    seasoned = has.astype(float).rolling(min_history, min_periods=min_history).sum() >= min_history
    volume = quote_volume.reindex_like(close)
    liquidity = volume.rolling(volume_window, min_periods=volume_window).median()
    rank = liquidity.where(seasoned).rank(axis=1, ascending=False, method="first")
    ranked = rank.le(n)
    if rebalance == "D":
        members = ranked
    elif rebalance == "M":
        snap = ranked.astype(float)
        snap.loc[~np.asarray(close.index.is_month_end)] = np.nan
        members = snap.ffill().fillna(0.0).astype(bool)
    else:
        raise ValueError(f"rebalance must be 'M' or 'D', got {rebalance!r}")
    return members & has
