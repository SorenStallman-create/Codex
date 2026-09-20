"""Statistical power helpers shared by the Trading Lab research runners.

The project intentionally avoids a NumPy dependency.  The formulas mirror the
Lo (2002) annualized Sharpe standard error and the 95%/80% minimum-detectable
Sharpe calculation in the research brief, using Python's standard library.
"""
from __future__ import annotations

import math
import statistics
from typing import Iterable


DEFAULT_PERIODS_PER_YEAR = 252.0
DEFAULT_CASH_RATE = 0.04


def _values(values: Iterable[float]) -> list[float]:
    out = [float(value) for value in values if math.isfinite(float(value))]
    return out


def daily_cash_return(cash_rate: float = DEFAULT_CASH_RATE, periods_per_year: float = DEFAULT_PERIODS_PER_YEAR) -> float:
    """Convert an annual effective cash rate to a per-period rate."""
    if periods_per_year <= 0:
        raise ValueError("periods_per_year must be positive")
    return (1.0 + float(cash_rate)) ** (1.0 / float(periods_per_year)) - 1.0


def excess_returns(returns: Iterable[float], cash_rate: float = DEFAULT_CASH_RATE, periods_per_year: float = DEFAULT_PERIODS_PER_YEAR) -> list[float]:
    rf = daily_cash_return(cash_rate, periods_per_year)
    return [value - rf for value in _values(returns)]


def annualized_sharpe(excess: Iterable[float], periods_per_year: float = DEFAULT_PERIODS_PER_YEAR) -> float:
    """Annualized arithmetic Sharpe from per-period excess returns."""
    values = _values(excess)
    if len(values) < 2:
        return 0.0
    sigma = statistics.pstdev(values)
    if sigma <= 0.0:
        return 0.0
    return float(math.sqrt(periods_per_year) * statistics.fmean(values) / sigma)


def sharpe_se(excess_returns: Iterable[float], periods_per_year: float = DEFAULT_PERIODS_PER_YEAR) -> float:
    """Lo (2002) standard error of an annualized Sharpe estimate."""
    values = _values(excess_returns)
    n = len(values)
    if n < 2:
        return float("inf")
    sigma = statistics.pstdev(values)
    if sigma <= 0.0:
        return float("inf")
    s = statistics.fmean(values) / sigma
    return float(math.sqrt(periods_per_year) * math.sqrt((1.0 + s * s / 2.0) / n))


def min_detectable_sharpe(n_obs: int, periods_per_year: float = DEFAULT_PERIODS_PER_YEAR) -> float:
    """Smallest true Sharpe distinguishable from zero at 95%/80% power."""
    if n_obs <= 0 or periods_per_year <= 0:
        return float("inf")
    return float((1.96 + 0.84) / math.sqrt(float(n_obs) / float(periods_per_year)))


def metrics(
    returns: Iterable[float],
    *,
    cash_rate: float = DEFAULT_CASH_RATE,
    periods_per_year: float = DEFAULT_PERIODS_PER_YEAR,
) -> dict[str, float | int | str]:
    """Return the complete scoreboard line for a return series."""
    values = _values(returns)
    excess = excess_returns(values, cash_rate=cash_rate, periods_per_year=periods_per_year)
    n = len(excess)
    sharpe = annualized_sharpe(excess, periods_per_year)
    se = sharpe_se(excess, periods_per_year)
    if math.isfinite(se):
        ci_low = sharpe - 1.96 * se
        ci_high = sharpe + 1.96 * se
    else:
        ci_low = float("-inf")
        ci_high = float("inf")
    return {
        "sharpe": sharpe,
        "sharpe_se": se,
        "sharpe_ci_low": ci_low,
        "sharpe_ci_high": ci_high,
        "n_obs": n,
        "min_detectable_sharpe": min_detectable_sharpe(n, periods_per_year),
        "power_verdict": "INCONCLUSIVE" if ci_low <= 0.0 else "CI_ABOVE_ZERO",
        "cash_rate": cash_rate,
        "periods_per_year": periods_per_year,
    }
