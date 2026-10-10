"""Rolling walk-forward splits with purge and embargo (positions on a time-sorted frame)."""

from collections.abc import Iterator

import numpy as np


def purged_walk_forward(
    n: int,
    train_size: int,
    test_size: int,
    label_horizon: int,
    embargo: int = 0,
    step: int | None = None,
    expanding: bool = False,
) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    """
    Yield (train_idx, test_idx). Training rows whose label window (t .. t+label_horizon)
    reaches into the test block are purged; ``embargo`` extra rows before the test block
    are dropped as well, so no training label overlaps test outcomes.
    """
    step = step or test_size
    start = 0
    test_start = train_size
    while test_start + test_size <= n:
        train_end = test_start - label_horizon - embargo  # exclusive
        lo = 0 if expanding else start
        if train_end > lo:
            yield np.arange(lo, train_end), np.arange(test_start, test_start + test_size)
        start += step
        test_start += step
