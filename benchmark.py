"""Comparable SPY benchmarks for the overnight equity study.

The old ``benchmark_spy`` traded SPY close-to-open every session.  Its
turnover made the benchmark lose money to modeled friction even when SPY rose.
This module supplies a no-turnover SPY total-return benchmark and a volatility
matched version with explicit cash and borrowing assumptions.
"""
from __future__ import annotations

import math
import statistics
from typing import Iterable

from power import DEFAULT_CASH_RATE, metrics


BORROW_SPREAD = 0.005


def aligned_spy_returns(spy_rows: dict[str, dict], dates: Iterable[str], next_date_key: str = "next_date") -> list[float]:
    """Close-to-close SPY returns aligned to strategy decision dates."""
    values: list[float] = []
    for date in dates:
        next_date = date.get(next_date_key) if isinstance(date, dict) else None
        if next_date is None:
            continue
        first = spy_rows.get(date["date"] if isinstance(date, dict) else date)
        second = spy_rows.get(next_date)
        if not first or not second or not first.get("adj_close") or not second.get("adj_close"):
            continue
        values.append(float(second["adj_close"]) / float(first["adj_close"]) - 1.0)
    return values


def close_to_close_returns(spy_rows: dict[str, dict], dates: list[str]) -> list[float]:
    values: list[float] = []
    for first, second in zip(dates, dates[1:]):
        if first not in spy_rows or second not in spy_rows:
            continue
        p0 = float(spy_rows[first]["adj_close"])
        p1 = float(spy_rows[second]["adj_close"])
        if p0 > 0 and p1 > 0:
            values.append(p1 / p0 - 1.0)
    return values


def buy_hold_return(spy_rows: dict[str, dict], dates: list[str]) -> float:
    usable = [date for date in dates if date in spy_rows]
    if len(usable) < 2:
        return 0.0
    first = float(spy_rows[usable[0]]["adj_close"])
    last = float(spy_rows[usable[-1]]["adj_close"])
    return last / first - 1.0 if first > 0 else 0.0


def annualized_vol(values: Iterable[float], periods_per_year: float = 252.0) -> float:
    values = [float(value) for value in values]
    return statistics.pstdev(values) * math.sqrt(periods_per_year) if len(values) > 1 else 0.0


def matched_spy_returns(
    spy_returns: Iterable[float],
    target_returns: Iterable[float],
    *,
    cash_rate: float = DEFAULT_CASH_RATE,
    borrow_spread: float = BORROW_SPREAD,
    periods_per_year: float = 252.0,
) -> tuple[list[float], dict[str, float]]:
    """Scale SPY to the target strategy's realized volatility.

    If target volatility is below SPY volatility, the uninvested balance earns
    the cash rate.  If it is above SPY volatility, the excess notional is
    financed at cash plus the configured spread.
    """
    spy = [float(value) for value in spy_returns]
    target = [float(value) for value in target_returns]
    n = min(len(spy), len(target))
    spy = spy[:n]
    target = target[:n]
    spy_vol = annualized_vol(spy, periods_per_year)
    target_vol = annualized_vol(target, periods_per_year)
    leverage = target_vol / spy_vol if spy_vol > 0 else 0.0
    cash_period = (1.0 + cash_rate) ** (1.0 / periods_per_year) - 1.0
    borrow_period = (1.0 + cash_rate + borrow_spread) ** (1.0 / periods_per_year) - 1.0
    out: list[float] = []
    for value in spy:
        if leverage <= 1.0:
            out.append(leverage * value + (1.0 - leverage) * cash_period)
        else:
            out.append(leverage * value - (leverage - 1.0) * borrow_period)
    return out, {
        "matched_leverage": leverage,
        "strategy_vol_pct": target_vol * 100.0,
        "spy_vol_pct": spy_vol * 100.0,
        "cash_rate": cash_rate,
        "borrow_rate": cash_rate + borrow_spread,
    }


def summary(
    *,
    period: str,
    model: str,
    name: str,
    returns: list[float],
    cash_rate: float = DEFAULT_CASH_RATE,
    periods_per_year: float = 252.0,
    extra: dict | None = None,
) -> dict:
    """Build a benchmark summary row with power fields."""
    row = {
        "period": period,
        "model": model,
        "name": name,
        "return_pct": (math.prod(1.0 + value for value in returns) - 1.0) * 100.0 if returns else 0.0,
        "trades": 0,
        "fee": 0.0,
        "slippage": 0.0,
        "allocation": 1.0,
        "quality": None,
    }
    row.update(metrics(returns, cash_rate=cash_rate, periods_per_year=periods_per_year))
    if extra:
        row.update(extra)
    return row
