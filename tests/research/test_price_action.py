"""Price action structure: swings, breaks of structure, flip-zone retests, candle confirmation and
stop/target scoring on hand-built candles with hand-computed answers."""

import numpy as np
import pandas as pd
import pytest
from mce.price_action import confirmations, score_stops, structure, swings, zone_touches


# (open, high, low, close); ATR fixed at 1. With k = 1: swing high 12 at bar 1 (known at 2),
# swing low 9.5 at bar 3 (known at 4); bar 5 closes above 12 (break up, flip level 12, stop
# 9.5); bar 7 leaves the zone (low > 12.5); bar 8 dips back into it (first retest); bar 9 is a
# bullish rejection candle closing above 12; bar 10 closes below 9.5 (break down).
BARS = [
    (10.0, 11.0, 9.0, 10.0),
    (10.0, 12.0, 10.0, 11.5),
    (11.5, 11.8, 10.5, 11.0),
    (11.0, 11.2, 9.5, 10.0),
    (10.0, 11.0, 10.0, 10.8),
    (10.8, 12.6, 10.7, 12.5),
    (12.5, 13.0, 12.2, 12.8),
    (12.8, 13.5, 12.9, 13.4),
    (13.4, 13.6, 12.3, 12.6),
    (12.6, 12.9, 12.1, 12.8),
    (12.8, 13.0, 9.0, 9.2),
]


def frame(bars=BARS, atr: float = 1.0) -> pd.DataFrame:
    df = pd.DataFrame(bars, columns=["open", "high", "low", "close"])
    df.insert(0, "date", pd.date_range("2025-01-01", periods=len(df), freq="4h", tz="UTC"))
    df["atr"] = atr
    return df


def mirror(df: pd.DataFrame, c: float = 30.0) -> pd.DataFrame:
    """The same candles upside down: every long pattern becomes the short one."""
    out = df.copy()
    out["open"], out["close"] = c - df["open"], c - df["close"]
    out["high"], out["low"] = c - df["low"], c - df["high"]
    return out


def test_swings_are_known_k_bars_later() -> None:
    high = np.array([1, 2, 5, 3, 2, 4, 6, 4, 3], float)
    low = high - 1
    s = swings(high, low, k=2)
    # 5 at bar 2 (known at 4), 6 at bar 6 (known at 8)
    assert np.isnan(s["high_price"].iloc[3]) and s["high_price"].iloc[4] == 5
    assert list(s["high_price"].iloc[4:8]) == [5, 5, 5, 5] and s["high_price"].iloc[8] == 6
    assert s["high_bar"].iloc[8] == 6
    # a tie with a later bar is a swing (bar 1, known at 2), a tie with an earlier one is not
    tie = swings(np.array([1, 3, 3, 2, 1], float), np.zeros(5), k=1)
    assert tie["high_bar"].iloc[2] == 1 and tie["high_bar"].iloc[3] == 1
    assert tie["low_price"].isna().all()  # equal lows: never strictly below the earlier bar


def test_structure_breaks_and_flips() -> None:
    st = structure(frame(), k=1)
    assert list(st["state"]) == [0, 0, 0, 0, 0, 1, 1, 1, 1, 1, -1]
    assert list(np.flatnonzero(st["bos"])) == [5, 10]
    up = st.iloc[5]
    assert up["level"] == 12.0 and up["stop"] == 9.5
    down = st.iloc[10]
    assert down["level"] == 9.5 and down["stop"] == 13.6  # swing high at bar 8, known at 9
    assert st["break_id"].iloc[9] != st["break_id"].iloc[10]


def test_first_retest_after_leaving_the_zone_and_its_confirmation() -> None:
    df = frame()
    st = structure(df, k=1)
    touches = zone_touches(df, st, zone_atr=0.5)
    assert list(touches["row"]) == [8] and list(touches["side"]) == [1]  # bar 9 is not a first
    assert touches["level"].iloc[0] == 12.0 and touches["stop"].iloc[0] == 9.5
    conf = confirmations(df, st, touches, window=3)
    assert list(conf["row"]) == [9] and list(conf["touch_row"]) == [8]


def test_confirmation_needs_a_close_beyond_the_level_and_a_pattern() -> None:
    below = list(BARS)
    below[9] = (12.6, 12.9, 11.5, 11.9)  # closes below the flip level
    df = frame(below)
    st = structure(df, k=1)
    assert confirmations(df, st, zone_touches(df, st, 0.5), window=3).empty
    plain = list(BARS)
    plain[9] = (12.6, 12.9, 12.55, 12.8)  # bullish, above the level, small wick, no engulfing
    df = frame(plain)
    st = structure(df, k=1)
    assert confirmations(df, st, zone_touches(df, st, 0.5), window=3).empty
    engulf = list(BARS)
    engulf[9] = (12.4, 13.55, 12.35, 13.5)  # opens <= 12.6, closes > 13.4 after a bearish bar
    df = frame(engulf)
    st = structure(df, k=1)
    assert list(confirmations(df, st, zone_touches(df, st, 0.5), window=3)["row"]) == [9]


def test_short_side_mirrors_the_long_side() -> None:
    df = mirror(frame())
    st = structure(df, k=1)
    assert list(st["state"]) == [0, 0, 0, 0, 0, -1, -1, -1, -1, -1, 1]
    assert st["level"].iloc[5] == pytest.approx(18.0) and st["stop"].iloc[5] == pytest.approx(20.5)
    touches = zone_touches(df, st, 0.5)
    assert list(touches["row"]) == [8] and list(touches["side"]) == [-1]
    assert list(confirmations(df, st, touches, window=3)["row"]) == [9]


@pytest.mark.parametrize("cut", [6, 8, 9])
def test_structure_is_causal(cut: int) -> None:
    df = frame()
    full, part = structure(df, k=1), structure(df.iloc[: cut + 1], k=1)
    pd.testing.assert_frame_equal(full.iloc[: cut + 1], part)
    t_full = zone_touches(df, full, 0.5)
    t_part = zone_touches(df.iloc[: cut + 1], part, 0.5)
    pd.testing.assert_frame_equal(t_full[t_full["row"] <= cut].reset_index(drop=True), t_part)


def _path(rows: list[tuple[float, float, float]]) -> pd.DataFrame:
    """Entry candle closing at 100, then (high, low, close) candles."""
    bars = [(100.0, 100.0, 100.0, 100.0)] + [(c, h, lo, c) for h, lo, c in rows]
    return frame(bars)


def test_score_stops_exits() -> None:
    one = np.array([0])
    long, short = np.array([1]), np.array([-1])
    # stop 98 (risk 2), target 104
    hit_stop = score_stops(
        _path([(101, 97.5, 98), (106, 99, 105)]), one, long, np.array([98.0]), max_bars=2
    )
    assert hit_stop["reason"].iloc[0] == "stop" and hit_stop["gross_r"].iloc[0] == -1
    hit_target = score_stops(_path([(104.5, 99, 104)]), one, long, np.array([98.0]), max_bars=1)
    assert hit_target["reason"].iloc[0] == "target" and hit_target["gross_r"].iloc[0] == 2
    both = score_stops(_path([(105, 97, 100)]), one, long, np.array([98.0]), max_bars=1)
    assert both["reason"].iloc[0] == "stop"  # the stop is assumed first
    timed = score_stops(
        _path([(101, 99, 100), (102, 99, 100), (101.5, 99.5, 101)]),
        one,
        long,
        np.array([98.0]),
        max_bars=3,
    )
    assert timed["reason"].iloc[0] == "time" and timed["gross_r"].iloc[0] == pytest.approx(0.5)
    assert timed["bars"].iloc[0] == 3 and timed["exit_row"].iloc[0] == 3
    # short: stop 102, target 96
    s = score_stops(_path([(101, 95.5, 96)]), one, short, np.array([102.0]), max_bars=1)
    assert s["reason"].iloc[0] == "target" and s["gross_r"].iloc[0] == 2


def test_score_stops_floor_incomplete_and_late_entry() -> None:
    one, long = np.array([0]), np.array([1])
    # stop 99.9 is closer than 0.5 ATR: the risk becomes 0.5, stop 99.5, target 101
    floor = score_stops(_path([(101.2, 99.8, 101)]), one, long, np.array([99.9]), max_bars=1)
    assert floor["risk"].iloc[0] == pytest.approx(0.5) and floor["stop_price"].iloc[0] == 99.5
    assert floor["reason"].iloc[0] == "target" and floor["gross_r"].iloc[0] == 2
    short_path = score_stops(_path([(101, 99, 100)]), one, long, np.array([98.0]), max_bars=3)
    assert np.isnan(short_path["gross_r"].iloc[0])  # not enough candles after the entry
    # one candle late: entry 101 at bar 1, same stop price 98 (risk 3), target 107
    late = score_stops(
        _path([(101, 100, 101), (107.5, 100, 107)]),
        one,
        long,
        np.array([98.0]),
        max_bars=1,
        delay=1,
    )
    assert late["entry"].iloc[0] == 101 and late["risk"].iloc[0] == 3
    assert late["reason"].iloc[0] == "target" and late["gross_r"].iloc[0] == 2
    # a late entry already through the stop is not taken
    gone = score_stops(
        _path([(100, 97, 97.5), (99, 96, 98)]), one, long, np.array([98.0]), max_bars=1, delay=1
    )
    assert np.isnan(gone["gross_r"].iloc[0])
