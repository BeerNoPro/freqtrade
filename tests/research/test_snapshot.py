"""Before/after snapshots: stable digests and a keyed row-by-row diff."""

import numpy as np
import pandas as pd
import pytest
from mce.snapshot import diff_tables, digest


def _table() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "pair": ["AUSDT", "AUSDT", "BUSDT"],
            "date": pd.to_datetime(["2026-06-01", "2026-06-02", "2026-06-01"], utc=True),
            "side": [1, -1, 1],
            "entry": [1.0, 2.0, np.nan],
            "reason": ["a", "b", "c"],
        }
    )


def test_digest_is_stable_and_sees_the_last_bit() -> None:
    a = _table()
    assert digest(a) == digest(_table()) and len(digest(a)) == 16
    b = a.copy()
    b.loc[0, "entry"] = np.nextafter(1.0, 2.0)  # one ulp
    assert digest(b) != digest(a)
    assert digest(a[::-1]) != digest(a)  # row order is part of the output


def test_diff_tables_lists_removed_added_and_changed_cells() -> None:
    before = _table()
    after = _table().drop(index=1)
    after.loc[0, "entry"] = 1.5
    after.loc[3] = ["CUSDT", pd.Timestamp("2026-06-03", tz="UTC"), 1, 3.0, "d"]
    d = diff_tables(before, after, ["pair", "date"])
    got = {(r.pair, r.status, r.column) for r in d.itertuples()}
    assert got == {
        ("AUSDT", "only_before", ""),
        ("CUSDT", "only_after", ""),
        ("AUSDT", "changed", "entry"),
    }
    row = d[d["status"] == "changed"].iloc[0]
    assert row["before"] == 1.0 and row["after"] == 1.5
    assert row["date"] == pd.Timestamp("2026-06-01", tz="UTC")


def test_diff_tables_treats_nan_pairs_as_equal_and_respects_the_tolerance() -> None:
    before, after = _table(), _table()
    after.loc[1, "entry"] = 2.0 + 1e-12
    assert diff_tables(before, after, ["pair", "date"], atol=1e-9).empty  # NaN == NaN on BUSDT
    assert len(diff_tables(before, after, ["pair", "date"])) == 1  # exact by default
    after.loc[2, "entry"] = 5.0  # NaN -> value is a change
    d = diff_tables(before, after, ["pair", "date"], atol=1e-9)
    assert d["column"].tolist() == ["entry"] and d["pair"].tolist() == ["BUSDT"]


def test_diff_tables_reports_columns_on_one_side_and_rejects_duplicate_keys() -> None:
    before, after = _table(), _table().drop(columns="reason")
    d = diff_tables(before, after, ["pair", "date"])
    assert d["status"].tolist() == ["column_only_before"] and d["column"].tolist() == ["reason"]
    dup = pd.concat([_table(), _table().iloc[:1]], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate"):
        diff_tables(dup, _table(), ["pair", "date"])
