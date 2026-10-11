import numpy as np
import pandas as pd
import pytest
from mce.archive import (
    funding_to_frame,
    klines_to_frame,
    parse_archive_funding,
    parse_archive_klines,
    parse_s3_prefixes,
)
from mce.universe import is_candidate, pair_to_symbol, point_in_time_members


def _panel(n_days: int, start: str = "2021-01-01") -> tuple[pd.DataFrame, pd.DataFrame]:
    """Random closes / volumes for 8 symbols; one listed late, one delisted, one with a gap."""
    rng = np.random.default_rng(7)
    idx = pd.date_range(start, periods=n_days, freq="D", tz="UTC")
    cols = [f"S{i}USDT" for i in range(8)]
    close = pd.DataFrame(
        100 * np.exp(np.cumsum(rng.normal(0, 0.03, (n_days, 8)), axis=0)), index=idx, columns=cols
    )
    volume = pd.DataFrame(rng.lognormal(10, 1, (n_days, 8)), index=idx, columns=cols)
    close.iloc[:200, 5] = np.nan  # listed late
    close.iloc[500:, 6] = np.nan  # delisted
    close.iloc[300, 7] = np.nan  # one missing day
    return close, volume.where(close.notna())


@pytest.mark.parametrize("rebalance", ["M", "D"])
def test_members_are_causal(rebalance: str) -> None:
    close, volume = _panel(800)
    full = point_in_time_members(
        close, volume, n=3, volume_window=10, min_history=60, rebalance=rebalance
    )
    for cut in (250, 499, 501, 640):
        part = point_in_time_members(
            close.iloc[:cut],
            volume.iloc[:cut],
            n=3,
            volume_window=10,
            min_history=60,
            rebalance=rebalance,
        )
        pd.testing.assert_frame_equal(part, full.iloc[:cut])
    assert full.sum(axis=1).max() <= 3


def test_ranking_needs_consecutive_history() -> None:
    idx = pd.date_range("2021-01-01", periods=40, freq="D", tz="UTC")
    close = pd.DataFrame(1.0, index=idx, columns=["AUSDT", "BUSDT", "CUSDT", "NEWUSDT"])
    volume = pd.DataFrame({"AUSDT": 4.0, "BUSDT": 3.0, "CUSDT": 2.0, "NEWUSDT": 100.0}, index=idx)
    close.iloc[:15, 3] = np.nan  # NEW starts on day 15
    close.iloc[20, 0] = np.nan  # A misses day 20 -> must rebuild 10 consecutive closes
    m = point_in_time_members(close, volume, n=2, volume_window=3, min_history=10, rebalance="D")
    assert m.iloc[:9].sum().sum() == 0  # nobody has 10 closes yet
    assert m.iloc[12][["AUSDT", "BUSDT"]].all() and m.iloc[12].sum() == 2
    assert m.iloc[24][["NEWUSDT", "BUSDT"]].all()  # NEW seasoned on day 24, A still rebuilding
    assert not m.iloc[29]["AUSDT"]  # A back only on day 30
    assert m.iloc[30][["NEWUSDT", "AUSDT"]].all() and m.iloc[30].sum() == 2


def test_monthly_holds_and_drops_delisted() -> None:
    idx = pd.date_range("2021-01-01", "2021-03-31", freq="D", tz="UTC")
    close = pd.DataFrame(1.0, index=idx, columns=["AUSDT", "BUSDT", "CUSDT"])
    volume = pd.DataFrame({"AUSDT": 3.0, "BUSDT": 2.0, "CUSDT": 1.0}, index=idx)
    volume.loc["2021-02-10":, "CUSDT"] = 50.0  # C becomes most liquid mid-February
    close.loc["2021-02-15":, "AUSDT"] = np.nan  # A delisted mid-February
    m = point_in_time_members(close, volume, n=2, volume_window=1, min_history=5)
    assert m.loc[:"2021-01-30"].sum().sum() == 0  # first ranking at the January month end
    jan_end = pd.Timestamp("2021-01-31", tz="UTC")
    assert m.loc[jan_end, ["AUSDT", "BUSDT"]].all()
    feb = m.loc["2021-02-01":"2021-02-27"]
    assert not feb["CUSDT"].any()  # no re-ranking inside the month
    assert feb.loc[:"2021-02-14", "AUSDT"].all() and not feb.loc["2021-02-15":, "AUSDT"].any()
    assert m.loc["2021-02-28":, ["BUSDT", "CUSDT"]].all().all()  # February ranking from Feb 28


def test_symbol_helpers() -> None:
    assert pair_to_symbol("BTC/USDT:USDT") == "BTCUSDT"
    assert pair_to_symbol("1000PEPE/USDT:USDT") == "1000PEPEUSDT"
    assert is_candidate("LUNAUSDT") and is_candidate("1000SHIBUSDT", "COIN", ["Meme", "Crypto"])
    assert not is_candidate("USDCUSDT", "COIN", ["Crypto"])  # stablecoin tagged COIN
    assert not is_candidate("BTCDOMUSDT", "INDEX", ["Index", "Crypto"])
    assert not is_candidate("TSLAUSDT", "EQUITY", ["TradFi"])
    assert not is_candidate("OPENAIUSDT", "PREMARKET", ["Pre-IPO", "TradFi"])
    assert not is_candidate("BTCUSDT_250627") and not is_candidate("ETHBTC")


def test_parse_s3_listing() -> None:
    prefix = "data/futures/um/monthly/klines/"
    xml = (
        "<ListBucketResult><IsTruncated>true</IsTruncated>"
        f"<CommonPrefixes><Prefix>{prefix}BTCUSDT/</Prefix></CommonPrefixes>"
        f"<CommonPrefixes><Prefix>{prefix}LUNAUSDT/</Prefix></CommonPrefixes>"
        "</ListBucketResult>"
    )
    assert parse_s3_prefixes(xml, prefix) == (["BTCUSDT", "LUNAUSDT"], True)
    assert parse_s3_prefixes("<IsTruncated>false</IsTruncated>", prefix) == ([], False)


def test_kline_and_funding_frames() -> None:
    day = 86_400_000
    t0 = 1_609_459_200_000  # 2021-01-01
    rows = [
        [t0, "1", "2", "0.5", "1.5", "10", t0 + day - 1, "15", 5, "4", "6", "0"],
        [t0 + day, "1.5", "2", "1", "1.8", "8", t0 + 2 * day - 1, "14", 5, "4", "6", "0"],
    ]
    df = klines_to_frame(rows, now_ms=t0 + day + 5)  # second candle still open
    assert len(df) == 1 and df["date"].iloc[0] == pd.Timestamp("2021-01-01", tz="UTC")
    assert df["close"].iloc[0] == 1.5 and df["quote_volume"].iloc[0] == 15.0
    assert klines_to_frame([]).empty

    fr = funding_to_frame([{"fundingTime": t0 + 5, "fundingRate": "0.0001", "markPrice": "1"}])
    assert fr["date"].iloc[0] == pd.Timestamp("2021-01-01", tz="UTC")
    assert fr["funding_rate"].iloc[0] == pytest.approx(0.0001)


def test_archive_csv_parsers() -> None:
    header = "open_time,open,high,low,close,volume,close_time,quote_volume,count,tb,tq,ignore\n"
    ms = "1614556800000,1.99,2.27,1.96,2.25,566,1614643199999,1230,400,261,568,0\n"
    us = "1614643200000000,2.25,2.38,2.10,2.23,533,1614729599999999,1192,343,241,539,0\n"
    k = parse_archive_klines(header + ms + us)  # header and microsecond stamps tolerated
    assert k["date"].tolist() == list(pd.date_range("2021-03-01", periods=2, freq="D", tz="UTC"))
    assert k["close"].tolist() == [2.25, 2.23] and k["quote_volume"].iloc[0] == 1230.0
    assert parse_archive_klines(ms)["close"].iloc[0] == 2.25  # no header

    f = parse_archive_funding(
        "calc_time,funding_interval_hours,last_funding_rate\n"
        "1614556800000,8,0.00010862\n1614585600003,8,-0.0002\n"
    )
    assert f["date"].iloc[1] == pd.Timestamp("2021-03-01 08:00", tz="UTC")
    assert f["funding_rate"].tolist() == pytest.approx([0.00010862, -0.0002])
