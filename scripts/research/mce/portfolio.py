"""
Daily portfolio backtester for target-weight strategies (trend ensembles, volatility targeting).

Timing: ``weights`` row t is decided at the close of day t and traded at that close, so it earns
the return from close t to close t+1. ``lag=1`` trades one day later (late-execution check).
Costs: ``fee`` per unit of traded notional (one side), on turnover measured against the weights
after they drifted with the day's returns. Funding: a long position pays the funding printed
during the holding day (positive rate = longs pay), a short receives it.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats


DAYS = 365


def vol_target_scalar(
    close: pd.DataFrame, target: float, window: int = 90, cap: float = 2.0
) -> pd.DataFrame:
    """target / annualised rolling vol of daily returns, capped. Causal (uses closes <= t)."""
    vol = close.pct_change().rolling(window, min_periods=window).std() * np.sqrt(DAYS)
    return (target / vol).clip(upper=cap)


def daily_funding(funding: pd.DataFrame, index: pd.DatetimeIndex) -> pd.DataFrame:
    """
    Funding paid while holding the position opened at the close of daily row t.

    A daily candle dated D closes at D+1 00:00, so the holding period of row D is
    (D+1 00:00, D+2 00:00]. Prints inside it are summed onto row D.
    ``funding``: rows = print timestamps (UTC, tz-aware), columns = pairs.
    """
    key = funding.index.ceil("D") - pd.Timedelta(days=2)
    return funding.groupby(key).sum().reindex(index).fillna(0.0)


@dataclass
class Result:
    daily: pd.DataFrame  # gross, fee, funding, net, equity, turnover, gross_exposure
    contrib: pd.DataFrame  # per-pair net contribution per day


def simulate(
    weights: pd.DataFrame,
    close: pd.DataFrame,
    fee: float = 0.0006,
    funding: pd.DataFrame | None = None,
    lag: int = 0,
) -> Result:
    """
    Run the backtest. All frames share the same daily DatetimeIndex and pair columns.
    ``funding`` must already be aligned to holding days (see ``daily_funding``).
    """
    w = weights.reindex_like(close).fillna(0.0).shift(lag).fillna(0.0)
    ret = close.pct_change().shift(-1)  # return earned by holding from t to t+1
    fund = (funding if funding is not None else close * 0).reindex_like(close).fillna(0.0)
    w_np, r_np, f_np = w.to_numpy(), ret.fillna(0.0).to_numpy(), fund.to_numpy()

    n, _ = w_np.shape
    drifted = np.zeros(w_np.shape[1])
    out = np.zeros((n, 4))  # gross, fee, funding, turnover
    contrib = np.zeros_like(w_np)
    for t in range(n):
        target = w_np[t]
        turnover = np.abs(target - drifted).sum()
        pair_gross = target * r_np[t]
        pair_fund = target * f_np[t]
        gross = pair_gross.sum()
        cost = fee * turnover
        out[t] = (gross, cost, pair_fund.sum(), turnover)
        contrib[t] = pair_gross - pair_fund - fee * np.abs(target - drifted)
        growth = 1 + gross
        drifted = target * (1 + r_np[t]) / growth if growth > 0 else np.zeros_like(target)

    daily = pd.DataFrame(out, index=close.index, columns=["gross", "fee", "funding", "turnover"])
    daily["net"] = daily["gross"] - daily["fee"] - daily["funding"]
    daily["equity"] = (1 + daily["net"]).cumprod()
    daily["gross_exposure"] = w.abs().sum(axis=1)
    return Result(daily, pd.DataFrame(contrib, index=close.index, columns=close.columns))


def metrics(net: pd.Series) -> dict[str, float]:
    net = net.dropna()
    equity = (1 + net).cumprod()
    years = len(net) / DAYS
    vol = net.std() * np.sqrt(DAYS)
    downside = net[net < 0].std() * np.sqrt(DAYS)
    dd = equity / equity.cummax() - 1
    cagr = equity.iloc[-1] ** (1 / years) - 1 if years > 0 and equity.iloc[-1] > 0 else np.nan
    return {
        "cagr": float(cagr),
        "vol": float(vol),
        "sharpe": float(net.mean() * DAYS / vol) if vol > 0 else np.nan,
        "sortino": float(net.mean() * DAYS / downside) if downside > 0 else np.nan,
        "max_dd": float(dd.min()),
        "calmar": float(cagr / abs(dd.min())) if dd.min() < 0 else np.nan,
        "total_return": float(equity.iloc[-1] - 1),
    }


def yearly_returns(net: pd.Series) -> pd.Series:
    return (1 + net).groupby(net.index.year).prod() - 1


def block_bootstrap_sharpe(
    net: pd.Series,
    block: int = 20,
    n_boot: int = 2000,
    seed: int = 0,
    quantiles: tuple[float, float] = (0.05, 0.95),
) -> dict[str, float]:
    """
    Moving-block bootstrap of the annualised Sharpe ratio of daily returns. Blocks of ``block``
    consecutive days keep volatility clustering and short autocorrelation inside each sample.
    """
    x = net.dropna().to_numpy()
    t = len(x)
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, t - block + 1, size=(n_boot, -(-t // block)))
    idx = (starts[:, :, None] + np.arange(block)).reshape(n_boot, -1)[:, :t]
    sample = x[idx]
    boot = sample.mean(axis=1) / sample.std(axis=1, ddof=1) * np.sqrt(DAYS)
    lo, hi = np.quantile(boot, quantiles)
    return {
        "sharpe": float(x.mean() / x.std(ddof=1) * np.sqrt(DAYS)),
        "lo": float(lo),
        "median": float(np.median(boot)),
        "hi": float(hi),
    }


def deflated_sharpe(net: pd.Series, n_trials: int, sr_std_trials: float) -> dict[str, float]:
    """
    Deflated Sharpe Ratio (Bailey & Lopez de Prado, 2014) on daily returns.
    ``sr_std_trials``: std of the (daily, non-annualised) Sharpe ratios across all trials.
    Returns the expected max Sharpe under the null (sr0, daily) and DSR = P(true SR > sr0).
    """
    x = net.dropna().to_numpy()
    t = len(x)
    sr = x.mean() / x.std(ddof=1)
    skew = stats.skew(x)
    kurt = stats.kurtosis(x, fisher=False)
    gamma = 0.5772156649
    if n_trials > 1:
        z1 = stats.norm.ppf(1 - 1 / n_trials)
        z2 = stats.norm.ppf(1 - 1 / (n_trials * np.e))
        sr0 = sr_std_trials * ((1 - gamma) * z1 + gamma * z2)
    else:
        sr0 = 0.0
    denom = np.sqrt(1 - skew * sr + (kurt - 1) / 4 * sr**2)
    dsr = stats.norm.cdf((sr - sr0) * np.sqrt(t - 1) / denom)
    return {"sr_daily": float(sr), "sr0_daily": float(sr0), "dsr": float(dsr), "n_obs": t}
