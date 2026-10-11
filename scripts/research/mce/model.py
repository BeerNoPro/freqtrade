"""
Probability models for trade setups: L2-regularised logistic regression (numpy/scipy only) and
walk-forward prediction on a panel of (time, coin) rows.

Each fold fits on its training rows only, including the standardisation statistics. Splits run on
the panel's sorted unique timestamps with purge and embargo (``mce.walkforward``), so no training
label window reaches into a test block.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import optimize

from mce.walkforward import purged_walk_forward


Z_CLIP = 5.0


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -35.0, 35.0)))


@dataclass
class Standardizer:
    mean: np.ndarray
    std: np.ndarray

    @classmethod
    def fit(cls, x: np.ndarray) -> "Standardizer":
        std = x.std(axis=0)
        return cls(x.mean(axis=0), np.where(std > 0, std, 1.0))

    def transform(self, x: np.ndarray) -> np.ndarray:
        """z-scores with the fitted statistics, clipped to +-Z_CLIP (outliers cannot dominate)."""
        return np.clip((x - self.mean) / self.std, -Z_CLIP, Z_CLIP)


@dataclass
class LogisticModel:
    scaler: Standardizer
    coef: np.ndarray  # intercept first, then one weight per standardised feature
    features: list[str]

    def predict(self, x: np.ndarray) -> np.ndarray:
        z = self.scaler.transform(np.asarray(x, dtype=float))
        return _sigmoid(self.coef[0] + z @ self.coef[1:])

    def weights(self) -> pd.Series:
        """Weight per feature (change in log-odds for +1 standard deviation)."""
        return pd.Series(self.coef[1:], index=self.features)


def fit_logistic(
    x: np.ndarray, y: np.ndarray, c: float = 1.0, features: Sequence[str] | None = None
) -> LogisticModel:
    """
    Minimise sum(log loss) + ||w||^2 / (2c) on standardised inputs, intercept not penalised:
    the objective of scikit-learn's ``LogisticRegression(C=c)``.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    scaler = Standardizer.fit(x)
    design = np.column_stack([np.ones(len(x)), scaler.transform(x)])

    def objective(w: np.ndarray) -> tuple[float, np.ndarray]:
        s = design @ w
        grad = design.T @ (_sigmoid(s) - y)
        grad[1:] += w[1:] / c
        loss = np.sum(np.logaddexp(0.0, s) - y * s) + w[1:] @ w[1:] / (2 * c)
        return float(loss), grad

    res = optimize.minimize(objective, np.zeros(design.shape[1]), jac=True, method="L-BFGS-B")
    names = list(features) if features is not None else [f"x{i}" for i in range(x.shape[1])]
    return LogisticModel(scaler, res.x, names)


def panel_splits(
    times: pd.DatetimeIndex | pd.Series,
    first_test: pd.Timestamp,
    test_bars: int,
    label_horizon: int,
    embargo: int = 0,
) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    """
    Row positions (train, test) for a panel. Walk-forward runs on the sorted unique timestamps:
    expanding training window, test blocks of ``test_bars`` timestamps starting at
    ``first_test``; training rows within ``label_horizon + embargo`` bars of a block are purged.
    """
    t = pd.DatetimeIndex(times)
    uniq = t.unique().sort_values()
    pos = uniq.get_indexer(t)
    train_size = int(uniq.searchsorted(first_test))
    for fit_t, test_t in purged_walk_forward(
        len(uniq), train_size, test_bars, label_horizon, embargo, expanding=True
    ):
        train = np.flatnonzero((pos >= fit_t[0]) & (pos <= fit_t[-1]))
        test = np.flatnonzero((pos >= test_t[0]) & (pos <= test_t[-1]))
        yield train, test


@dataclass
class Fold:
    train: np.ndarray  # row positions actually used to fit (complete features and label)
    test: np.ndarray  # row positions predicted (complete features)
    model: LogisticModel


def walk_forward_predict(
    panel: pd.DataFrame,
    features: list[str],
    label: str,
    splits: Sequence[tuple[np.ndarray, np.ndarray]],
    c: float = 1.0,
) -> tuple[np.ndarray, list[Fold]]:
    """
    Out-of-sample probability for every test row (NaN elsewhere) and the fitted folds. Training
    skips rows with a missing feature or label; test rows only need complete features.
    """
    x = panel[features].to_numpy(dtype=float)
    y = panel[label].to_numpy(dtype=float)
    complete = np.isfinite(x).all(axis=1)
    pred = np.full(len(panel), np.nan)
    folds = []
    for train, test in splits:
        fit_rows = train[complete[train] & np.isfinite(y[train])]
        test_rows = test[complete[test]]
        model = fit_logistic(x[fit_rows], y[fit_rows], c, features)
        pred[test_rows] = model.predict(x[test_rows])
        folds.append(Fold(fit_rows, test_rows, model))
    return pred, folds
