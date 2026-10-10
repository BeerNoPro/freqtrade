import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


sys.path.insert(0, str(Path(__file__).parents[2] / "scripts" / "research"))


def make_ohlcv(n: int, freq: str = "1h", seed: int = 0, start: str = "2024-01-01") -> pd.DataFrame:
    """Random-walk OHLCV with a few volatility regimes."""
    rng = np.random.default_rng(seed)
    vol = np.repeat(rng.uniform(0.002, 0.02, size=n // 200 + 1), 200)[:n]
    close = 100 * np.exp(np.cumsum(rng.normal(0, vol)))
    open_ = np.r_[close[0], close[:-1]]
    spread = np.abs(rng.normal(0, vol)) * close
    return pd.DataFrame(
        {
            "date": pd.date_range(start, periods=n, freq=freq, tz="UTC"),
            "open": open_,
            "high": np.maximum(open_, close) + spread,
            "low": np.minimum(open_, close) - spread,
            "close": close,
            "volume": rng.uniform(1, 10, size=n),
        }
    )


@pytest.fixture
def ohlcv() -> pd.DataFrame:
    return make_ohlcv(2000)
