"""
Research universes.

``DEFAULT_PAIRS``: fixed list of liquid USDT-M perps listed before 2023 (selection made today,
so a survivorship bias toward coins that are still large remains and must be reported).

``point_in_time_members``: top-n by liquidity using only data available at each date, over a
candidate list that includes delisted contracts (see ``mce.archive``).
"""

import numpy as np
import pandas as pd


DEFAULT_PAIRS = [
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "SOL/USDT:USDT",
    "XRP/USDT:USDT",
    "DOGE/USDT:USDT",
    "ADA/USDT:USDT",
    "LINK/USDT:USDT",
    "AVAX/USDT:USDT",
    "BNB/USDT:USDT",
    "LTC/USDT:USDT",
    "DOT/USDT:USDT",
    "TRX/USDT:USDT",
    "BCH/USDT:USDT",
    "ATOM/USDT:USDT",
    "NEAR/USDT:USDT",
]

# Not crypto-asset exposure: excluded by asset class, never by results.
# Binance TradFi perps (stocks, ETFs, pre-IPO) were listed from late 2025 and cannot meet a
# 365-day history requirement inside the 2021-2025 research window; extend this set before
# using the universe on later data.
EXCLUDED_BASES = frozenset(
    {
        # stablecoins and fiat
        "USDC",
        "BUSD",
        "TUSD",
        "FDUSD",
        "USDP",
        "DAI",
        "USDE",
        "USD1",
        "USDBRL",
        # index contracts
        "BTCDOM",
        "DEFI",
        "FOOTBALL",
        "BLUEBIRD",
        # commodities and tokenized commodities
        "XAU",
        "XAG",
        "XAUT",
        "PAXG",
        "XPT",
        "XPD",
        "NATGAS",
        "COPPER",
        "CL",
        "BZ",
    }
)


def pair_to_symbol(pair: str) -> str:
    """ccxt pair to raw Binance symbol: ``BTC/USDT:USDT`` -> ``BTCUSDT``."""
    return pair.split(":")[0].replace("/", "")


def is_candidate(symbol: str) -> bool:
    """USDT-margined perpetual whose base is a crypto asset."""
    return symbol.endswith("USDT") and "_" not in symbol and symbol[:-4] not in EXCLUDED_BASES


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
