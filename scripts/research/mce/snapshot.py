"""
Before/after snapshots of research outputs, for proving that a code or data change leaves the
outputs of registered rules unchanged, or listing exactly what it changed: a stable digest of a
table and a row-by-row diff keyed on columns. Exact by default.
"""

import hashlib

import numpy as np
import pandas as pd


DIFF_COLS = ["column", "before", "after", "status"]


def digest(df: pd.DataFrame) -> str:
    """First 16 hex characters of the sha256 of the table as CSV (rows in their order, floats
    at full precision, index left out)."""
    text = df.to_csv(index=False, float_format="%.17g", lineterminator="\n")
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _keyed(df: pd.DataFrame, key: list[str], side: str) -> pd.DataFrame:
    if df.duplicated(key).any():
        raise ValueError(f"duplicate keys in the {side} table: {key}")
    return df.set_index(key)


def _same(a: pd.Series, b: pd.Series, atol: float, rtol: float) -> np.ndarray:
    both_nan = (a.isna() & b.isna()).to_numpy()
    if pd.api.types.is_numeric_dtype(a) and pd.api.types.is_numeric_dtype(b):
        x, y = a.to_numpy(dtype=float), b.to_numpy(dtype=float)
        with np.errstate(invalid="ignore"):
            close = np.abs(x - y) <= atol + rtol * np.abs(y)
        return both_nan | close
    return both_nan | (a.astype(object) == b.astype(object)).to_numpy(dtype=bool)


def diff_tables(
    before: pd.DataFrame,
    after: pd.DataFrame,
    key: list[str],
    atol: float = 0.0,
    rtol: float = 0.0,
) -> pd.DataFrame:
    """
    One row per difference: the key columns, then ``column``, ``before``, ``after`` and
    ``status`` - ``only_before`` / ``only_after`` (a row on one side only), ``changed`` (a cell
    beyond ``atol + rtol * |after|``; two missing values are equal), ``column_only_before`` /
    ``column_only_after``. Keys must be unique on each side.
    """
    a, b = _keyed(before, key, "before"), _keyed(after, key, "after")
    rows: list[dict] = []

    def add(idx, column: str, x, y, status: str) -> None:
        values = idx if isinstance(idx, tuple) else (idx,)
        rows.append(
            dict(zip(key, values, strict=True))
            | dict(zip(DIFF_COLS, (column, x, y, status), strict=True))
        )

    for c in a.columns.difference(b.columns, sort=False):
        add((None,) * len(key), c, None, None, "column_only_before")
    for c in b.columns.difference(a.columns, sort=False):
        add((None,) * len(key), c, None, None, "column_only_after")
    for idx in a.index.difference(b.index, sort=False):
        add(idx, "", None, None, "only_before")
    for idx in b.index.difference(a.index, sort=False):
        add(idx, "", None, None, "only_after")
    shared = a.index.intersection(b.index, sort=False)
    for c in a.columns.intersection(b.columns, sort=False):
        x, y = a.loc[shared, c], b.loc[shared, c]
        for i in np.flatnonzero(~_same(x, y, atol, rtol)):
            add(shared[i], c, x.iloc[i], y.iloc[i], "changed")
    return pd.DataFrame(rows, columns=[*key, *DIFF_COLS])
