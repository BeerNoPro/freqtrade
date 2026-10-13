"""Statistical power of the pass rules: an edge planted into outcome-blind noise is detected at
the rate theory predicts, a zero edge at the false-positive rate, and no output depends on the
mean of the data it is given."""

import math

import numpy as np
import pandas as pd
import pytest
from mce.power import (
    block_resample,
    demeaned,
    event_power,
    joint_pass,
    portfolio_power,
)
from scipy import stats


def _gauss_days(n: int, seed: int = 0) -> np.ndarray:
    return np.random.default_rng(seed).normal(0.0, 0.02, n)


def _events(n: int, weeks: int, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    return rng.standard_t(5, n) / math.sqrt(5 / 3), rng.integers(0, weeks, n)


def test_demeaned_and_block_resample() -> None:
    x = np.array([1.0, 2.0, np.nan, 5.0])
    out = demeaned(x)
    assert np.isnan(out[2]) and np.nansum(out) == pytest.approx(0.0)
    y = np.arange(100.0)
    s = block_resample(y, 10, np.random.default_rng(0))
    assert len(s) == 100
    steps = np.diff(s)
    assert (steps == 1).sum() >= 80  # runs of consecutive days inside each block


def test_portfolio_power_matches_theory_and_false_positive_rate() -> None:
    days = _gauss_days(730)
    out = portfolio_power(days, sharpes=(0.0, 0.5, 3.0), n_sims=300, n_boot=400, seed=1)
    out = out.set_index("sharpe")
    years = 730 / 365
    # zero edge: the bootstrap lower bound is above 0 about 5% of the time
    assert 0.01 <= out.loc[0.0, "ci_lo_above_0"] <= 0.10
    # a positive sample Sharpe: Phi(S * sqrt(T)) for iid returns
    expected = stats.norm.cdf(0.5 * math.sqrt(years))
    assert out.loc[0.5, "positive"] == pytest.approx(expected, abs=0.07)
    assert out.loc[3.0, "ci_lo_above_0"] >= 0.95 and out.loc[3.0, "positive"] == 1.0
    assert (out["pass"] <= out["ci_lo_above_0"]).all()  # the full gate adds conditions


def test_event_power_matches_theory_and_needs_enough_events() -> None:
    x, weeks = _events(300, 60)
    out = event_power(x, weeks, effects=(0.0, 0.1, 0.4), n_sims=300, n_boot=500, seed=2)
    out = out.set_index("effect")
    # two-sided 5% test and a positive net mean: about 2.5% with no edge
    assert out.loc[0.0, "pass"] <= 0.06
    # effect 0.1 sd with 300 events: z = 0.1 * sqrt(300) = 1.73 -> power Phi(1.73 - 1.96) ~ 0.41
    expected = stats.norm.cdf(0.1 * math.sqrt(300) - 1.96)
    assert out.loc[0.1, "pass"] == pytest.approx(expected, abs=0.1)
    assert out.loc[0.4, "pass"] >= 0.99
    few = event_power(x[:150], weeks[:150], effects=(0.4,), n_sims=50, seed=2)
    assert few["pass"].iloc[0] == 0.0  # below the 200-event gate
    # net of cost: an edge equal to the cost passes about half the time, far below it never
    even = event_power(x, weeks, effects=(0.4,), cost=0.4, n_sims=200, n_boot=300, seed=2)
    assert 0.35 <= even["pass"].iloc[0] <= 0.65
    costly = event_power(x, weeks, effects=(0.4,), cost=1.0, n_sims=50, seed=2)
    assert costly["pass"].iloc[0] == 0.0
    three = event_power(x, weeks, effects=(0.1,), n_tests=3, n_sims=300, n_boot=500, seed=2)
    assert three["pass"].iloc[0] < out.loc[0.1, "pass"]  # Benjamini-Hochberg over 3 tests


def test_outputs_do_not_depend_on_the_mean_of_the_data() -> None:
    days = _gauss_days(400, seed=3)
    a = portfolio_power(days, sharpes=(0.0, 1.0), n_sims=40, n_boot=200, seed=4)
    b = portfolio_power(days + 0.01, sharpes=(0.0, 1.0), n_sims=40, n_boot=200, seed=4)
    pd.testing.assert_frame_equal(a, b)
    x, weeks = _events(250, 50, seed=5)
    a = event_power(x, weeks, effects=(0.0, 0.2), n_sims=40, n_boot=200, seed=6)
    b = event_power(x - 3.0, weeks, effects=(0.0, 0.2), n_sims=40, n_boot=200, seed=6)
    pd.testing.assert_frame_equal(a, b)


def test_joint_pass_rule() -> None:
    assert joint_pass(0.5, 0.8) == pytest.approx(0.4)
