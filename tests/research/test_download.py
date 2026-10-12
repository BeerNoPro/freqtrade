"""Point-in-time store downloader: volume estimate printed by ``--plan-only`` (no network)."""

import pandas as pd
import pytest
from download_pit_universe import estimate_volume


def test_download_estimate() -> None:
    starts = {
        "AUSDT": pd.Timestamp("2024-01-01", tz="UTC"),
        "BUSDT": pd.Timestamp("2024-01-11", tz="UTC"),
    }
    end = pd.Timestamp("2024-01-21", tz="UTC")
    have = {"AUSDT": {"2024-01-01", "2024-01-02"}}
    assert estimate_volume(starts, end, have) == {
        "symbols": 2,
        "symbol_days": 30,
        "metrics_files_missing": 28,
    }
    assert estimate_volume({"AUSDT": end}, end)["symbol_days"] == 0
    with pytest.raises(ValueError):
        estimate_volume({"AUSDT": end + pd.Timedelta(days=1)}, end, {})
