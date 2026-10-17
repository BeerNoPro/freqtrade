"""Point-in-time store downloader: volume estimate printed by ``--plan-only`` (no network)."""

import ccxt
import pandas as pd
import pytest
import requests
from download_pit_universe import _finish, _sync_all, estimate_volume, failure_line


def test_network_errors_are_failures_and_rejections_are_not() -> None:
    """A DNS drop loses symbols a rerun can fetch; a delisted symbol the API rejects is covered
    by the archive fallback and must not fail a strict run (2026-10-16: the outage lost metrics
    of 127 symbols while the job still ended with exit 0)."""

    def fn(symbol: str, start_ms: int) -> int:
        if symbol == "DNSUSDT":
            raise requests.ConnectionError("getaddrinfo failed")
        if symbol == "SLOWUSDT":
            raise ccxt.RequestTimeout("timed out")
        if symbol == "GONEUSDT":
            raise ccxt.BadSymbol('{"code":-1121,"msg":"Invalid symbol."}')
        return 5

    start = pd.Timestamp("2025-01-01", tz="UTC")
    starts = dict.fromkeys(["AUSDT", "DNSUSDT", "SLOWUSDT", "GONEUSDT", "BUSDT"], start)
    failed: list[str] = []
    waited: list[float] = []
    assert _sync_all("metrics", fn, starts, failed, waits=(1.0, 2.0), sleep=waited.append) == 10
    assert failed == ["DNSUSDT", "SLOWUSDT"]
    assert waited == [1.0, 2.0, 1.0, 2.0]  # network errors retried, the rejection is not


def test_a_short_network_drop_is_retried_not_lost() -> None:
    """A 10-second Wi-Fi drop must not lose a symbol: the same symbol is tried again."""
    calls = {"n": 0}

    def flaky(symbol: str, start_ms: int) -> int:
        calls["n"] += 1
        if calls["n"] <= 2:
            raise requests.ConnectionError("getaddrinfo failed")
        return 7

    failed: list[str] = []
    waited: list[float] = []
    starts = {"AUSDT": pd.Timestamp("2025-01-01", tz="UTC")}
    assert _sync_all("metrics", flaky, starts, failed, sleep=waited.append) == 7
    assert failed == [] and waited == [10.0, 30.0]


def test_failure_line_and_strict_exit(capsys) -> None:
    many = [f"C{i}USDT" for i in range(12)]
    line = failure_line({"klines_1d": [], "metrics": many})
    assert line.startswith("FAILED (network, rerun to fetch) metrics 12: C0USDT")
    assert line.endswith("(+2 more)")
    assert failure_line({"funding": []}) == ""
    _finish({"funding": []}, strict=True)
    assert "all symbols synced" in capsys.readouterr().out
    _finish({"metrics": ["AUSDT"]}, strict=False)
    with pytest.raises(SystemExit) as stop:
        _finish({"metrics": ["AUSDT"]}, strict=True)
    assert stop.value.code == 2


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
