"""One-stock-per-session U.S. equity overnight research.

This module deliberately uses only standard-library Python so the study can be
rerun on a clean machine. It downloads split/dividend-adjusted daily Yahoo
bars for a fixed liquid universe when no cache is present, then simulates a
long position entered near the close and sold at the next regular-session open.

The historical research is price/volume-only. A separate Alpaca adapter adds
authenticated bars, Benzinga-backed news, paper orders, and a live decision
ledger without putting credentials or order endpoints into the backtest.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import random
import ssl
import statistics
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from pathlib import Path
from zoneinfo import ZoneInfo

from benchmark import BORROW_SPREAD, buy_hold_return, close_to_close_returns, matched_spy_returns, summary as benchmark_summary
from power import metrics as power_metrics


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "research" / "overnight_equities"
DATA = OUT / "data"
NY = ZoneInfo("America/New_York")
UTC = dt.timezone.utc
CAPITAL = 10_000.0
BENCHMARK = "SPY"
START = dt.date(2015, 1, 1)
END = dt.date(2026, 9, 19)
FEE = 0.0000
SLIP = 0.0005
ALLOCATION = 1.0
CASH_RATE = 0.04
PERIODS_PER_YEAR = 252.0
MIN_PRICE = 10.0
MIN_DOLLAR_VOLUME = 50_000_000.0
USER_AGENT = "overnight-equities-research/1.0"
SSL_CONTEXT = ssl._create_unverified_context()

# This is a deliberately fixed, liquid large-cap universe. It is not a
# survivorship-bias-free CRSP universe, so the report calls that limitation out.
UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "AVGO", "TSLA",
    "JPM", "WMT", "LLY", "V", "ORCL", "MA", "XOM", "COST", "NFLX",
    "JNJ", "HD", "PG", "BAC", "ABBV", "KO", "CRM", "CVX", "MRK",
    "AMD", "PEP", "TMO", "MCD", "LIN", "DIS", "CSCO", "IBM", "GE",
    "CAT", "AMAT", "QCOM", "TXN", "INTU", "NOW", "UBER", "PLTR", "GS",
    "ISRG", "BKNG", "IWM",
]

PERIODS = {
    "development": (dt.date(2015, 1, 1), dt.date(2020, 1, 1)),
    "validation": (dt.date(2020, 1, 1), dt.date(2022, 1, 1)),
    "holdout": (dt.date(2022, 1, 1), dt.date(2025, 1, 1)),
    "recent": (dt.date(2025, 1, 1), dt.date(2026, 9, 19)),
}


@dataclass(frozen=True)
class ModelSpec:
    id: str
    name: str
    thesis: str
    regime: bool
    weights: dict[str, float]


MODELS = (
    ModelSpec(
        "overnight_momentum",
        "Overnight momentum",
        "Favor stocks with persistent close-to-open strength and medium-term relative strength.",
        True,
        {"overnight_mean60": 0.45, "mom20": 0.30, "resid_mom20": 0.15, "volume_ratio20": 0.10, "vol20": -0.10},
    ),
    ModelSpec(
        "overnight_reversal",
        "Intraday reversal into the close",
        "Buy liquid stocks that were weak during the day but retain a positive overnight history.",
        False,
        {"day_return": -0.55, "overnight_mean20": 0.25, "close_location": -0.10, "vol20": -0.10, "mom5": -0.10},
    ),
    ModelSpec(
        "close_strength",
        "Close strength",
        "Follow a strong close, high close location, and unusual volume into the next open.",
        True,
        {"day_return": 0.45, "close_location": 0.30, "volume_ratio20": 0.20, "mom5": 0.15, "vol20": -0.10},
    ),
    ModelSpec(
        "relative_strength",
        "Market-relative strength",
        "Buy stocks whose recent return is strong after removing the benchmark move.",
        True,
        {"resid_mom20": 0.55, "mom60": 0.25, "overnight_mean20": 0.20, "vol20": -0.10},
    ),
    ModelSpec(
        "ensemble",
        "Risk-adjusted ensemble",
        "Blend momentum, reversal, close strength, and relative strength with a volatility penalty.",
        True,
        {"overnight_mean60": 0.25, "resid_mom20": 0.25, "day_return": 0.15, "close_location": 0.10, "mom20": 0.20, "volume_ratio20": 0.10, "vol20": -0.20},
    ),
)


def iso_date(value: dt.date | dt.datetime) -> str:
    return value.date().isoformat() if isinstance(value, dt.datetime) else value.isoformat()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def yahoo_url(symbol: str) -> str:
    p1 = int(dt.datetime.combine(START, dt.time(), UTC).timestamp())
    p2 = int(dt.datetime.combine(END, dt.time(), UTC).timestamp())
    query = urllib.parse.urlencode({
        "period1": p1,
        "period2": p2,
        "interval": "1d",
        "events": "div,splits",
        "includeAdjustedClose": "true",
    })
    return f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol, safe='')}?{query}"


def fetch_yahoo(symbol: str, retries: int = 3) -> tuple[str, list[dict]]:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(yahoo_url(symbol), headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=30, context=SSL_CONTEXT) as response:
                payload = json.loads(response.read().decode("utf-8"))
            result = (payload.get("chart") or {}).get("result") or []
            if not result:
                raise ValueError(f"Yahoo returned no chart for {symbol}")
            chart = result[0]
            timestamps = chart.get("timestamp") or []
            quote = ((chart.get("indicators") or {}).get("quote") or [{}])[0]
            adjusted = ((chart.get("indicators") or {}).get("adjclose") or [{}])[0].get("adjclose") or []
            meta = chart.get("meta") or {}
            exchange_tz = ZoneInfo(meta.get("exchangeTimezoneName") or "America/New_York")
            rows: list[dict] = []
            for i, stamp in enumerate(timestamps):
                vals = {key: (quote.get(key) or [None] * len(timestamps))[i] for key in ("open", "high", "low", "close", "volume")}
                if any(vals[k] is None for k in ("open", "high", "low", "close")):
                    continue
                raw_close = float(vals["close"])
                adj_close = float(adjusted[i]) if i < len(adjusted) and adjusted[i] is not None else raw_close
                factor = adj_close / raw_close if raw_close else 1.0
                local_date = dt.datetime.fromtimestamp(int(stamp), UTC).astimezone(exchange_tz).date()
                if local_date < START or local_date >= END:
                    continue
                rows.append({
                    "symbol": symbol,
                    "date": local_date.isoformat(),
                    "open": float(vals["open"]),
                    "high": float(vals["high"]),
                    "low": float(vals["low"]),
                    "close": raw_close,
                    "adj_open": float(vals["open"]) * factor,
                    "adj_high": float(vals["high"]) * factor,
                    "adj_low": float(vals["low"]) * factor,
                    "adj_close": adj_close,
                    "volume": float(vals["volume"] or 0.0),
                    "dollar_volume": raw_close * float(vals["volume"] or 0.0),
                })
            rows.sort(key=lambda r: r["date"])
            if len(rows) < 500:
                raise ValueError(f"Yahoo returned only {len(rows)} rows for {symbol}")
            return symbol, rows
        except Exception as exc:  # network feeds can fail transiently
            last_error = exc
            time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(f"failed to fetch {symbol}: {last_error}")


def fetch_all(force: bool = False) -> tuple[dict[str, list[dict]], dict]:
    DATA.mkdir(parents=True, exist_ok=True)
    symbols = [BENCHMARK] + [s for s in UNIVERSE if s != BENCHMARK]
    data: dict[str, list[dict]] = {}
    cached: list[str] = []
    missing: list[str] = []
    for symbol in symbols:
        path = DATA / f"{symbol}.jsonl"
        if path.exists() and not force:
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            if rows:
                data[symbol] = rows
                cached.append(symbol)
            else:
                missing.append(symbol)
        else:
            missing.append(symbol)
    failures: dict[str, str] = {}
    if missing:
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(fetch_yahoo, symbol): symbol for symbol in missing}
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    _, rows = future.result()
                    data[symbol] = rows
                    (DATA / f"{symbol}.jsonl").write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")
                except Exception as exc:
                    failures[symbol] = str(exc)
    for symbol in data:
        path = DATA / f"{symbol}.jsonl"
        if not path.exists():
            path.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in data[symbol]), encoding="utf-8")
    audit = {
        "created_utc": dt.datetime.now(UTC).isoformat(),
        "provider": "Yahoo Finance chart endpoint; historical research feed, not a broker execution quote",
        "start": START.isoformat(),
        "end_exclusive": END.isoformat(),
        "requested_symbols": symbols,
        "cached_symbols": sorted(cached),
        "downloaded_symbols": sorted(set(missing) - set(failures)),
        "failures": failures,
        "symbols": {
            symbol: {
                "rows": len(rows),
                "first": rows[0]["date"],
                "last": rows[-1]["date"],
                "duplicates": len(rows) - len({r["date"] for r in rows}),
                "sha256": sha256(DATA / f"{symbol}.jsonl"),
            }
            for symbol, rows in sorted(data.items())
        },
    }
    (OUT / "data_audit.json").write_text(json.dumps(audit, indent=2, sort_keys=True), encoding="utf-8")
    return data, audit


def rolling_mean(values: list[float | None], end: int, width: int) -> float | None:
    chunk = [float(x) for x in values[max(0, end - width + 1): end + 1] if x is not None and math.isfinite(float(x))]
    return statistics.fmean(chunk) if len(chunk) == width else None


def rolling_sum(values: list[float | None], end: int, width: int) -> float | None:
    chunk = [float(x) for x in values[max(0, end - width + 1): end + 1] if x is not None and math.isfinite(float(x))]
    return sum(chunk) if len(chunk) == width else None


def rolling_std(values: list[float | None], end: int, width: int) -> float | None:
    chunk = [float(x) for x in values[max(0, end - width + 1): end + 1] if x is not None and math.isfinite(float(x))]
    return statistics.pstdev(chunk) if len(chunk) == width else None


def feature_rows(data: dict[str, list[dict]]) -> dict[str, dict[str, dict]]:
    benchmark = {r["date"]: r for r in data[BENCHMARK]}
    spy_close = [r["adj_close"] for r in data[BENCHMARK]]
    spy_dates = [r["date"] for r in data[BENCHMARK]]
    spy_index = {d: i for i, d in enumerate(spy_dates)}
    spy_c2c: list[float | None] = [None]
    for i in range(1, len(spy_close)):
        spy_c2c.append(spy_close[i] / spy_close[i - 1] - 1.0)
    spy_ma50: list[float | None] = []
    spy_ma200: list[float | None] = []
    for i in range(len(spy_close)):
        spy_ma50.append(rolling_mean(spy_close, i, 50))
        spy_ma200.append(rolling_mean(spy_close, i, 200))
    out: dict[str, dict[str, dict]] = {}
    for symbol, rows in data.items():
        if symbol == BENCHMARK:
            continue
        closes = [r["adj_close"] for r in rows]
        opens = [r["adj_open"] for r in rows]
        volumes = [r["volume"] for r in rows]
        c2c: list[float | None] = [None]
        overnight: list[float | None] = [None]
        day: list[float | None] = [None]
        for i in range(1, len(rows)):
            c2c.append(closes[i] / closes[i - 1] - 1.0)
            overnight.append(opens[i] / closes[i - 1] - 1.0)
            day.append(closes[i] / opens[i] - 1.0 if opens[i] else None)
        by_date: dict[str, dict] = {}
        for i, row in enumerate(rows):
            date = row["date"]
            bi = spy_index.get(date)
            if bi is None:
                continue
            range_ = row["adj_high"] - row["adj_low"]
            prior_20_volume = rolling_mean(volumes, i - 1, 20) if i >= 20 else None
            residuals: list[float | None] = []
            for j in range(max(0, i - 19), i + 1):
                if c2c[j] is None or j >= len(spy_c2c) or spy_c2c[j] is None:
                    residuals.append(None)
                else:
                    residuals.append(c2c[j] - spy_c2c[j])
            by_date[date] = {
                **row,
                "c2c_1": c2c[i],
                "overnight_1": overnight[i],
                "day_return": day[i],
                "mom5": (closes[i] / closes[i - 5] - 1.0) if i >= 5 else None,
                "mom20": (closes[i] / closes[i - 20] - 1.0) if i >= 20 else None,
                "mom60": (closes[i] / closes[i - 60] - 1.0) if i >= 60 else None,
                "overnight_mean20": rolling_mean(overnight, i, 20),
                "overnight_mean60": rolling_mean(overnight, i, 60),
                "vol20": rolling_std(c2c, i, 20),
                "resid_mom20": sum(x for x in residuals if x is not None) if len([x for x in residuals if x is not None]) == 20 else None,
                "volume_ratio20": (volumes[i] / prior_20_volume) if prior_20_volume and prior_20_volume > 0 else None,
                "close_location": ((row["adj_close"] - row["adj_low"]) / range_ * 2.0 - 1.0) if range_ > 0 else 0.0,
                "market_regime": bool(spy_ma50[bi] is not None and spy_ma200[bi] is not None and spy_ma50[bi] > spy_ma200[bi]),
                "benchmark_close": benchmark[date]["adj_close"],
            }
        out[symbol] = by_date
    return out


def rank_percentile(values: list[float], value: float, higher: bool = True) -> float:
    ordered = sorted(values)
    if not higher:
        value = -value
        ordered = sorted(-x for x in values)
    if len(ordered) <= 1:
        return 0.5
    below = sum(1 for x in ordered if x <= value)
    return (below - 1) / (len(ordered) - 1)


def score_candidates(rows: list[dict], spec: ModelSpec) -> list[dict]:
    needed = set(spec.weights)
    eligible = [
        row for row in rows
        if row["adj_close"] >= MIN_PRICE
        and row["dollar_volume"] >= MIN_DOLLAR_VOLUME
        and all(row.get(key) is not None and math.isfinite(float(row[key])) for key in needed)
    ]
    if spec.regime:
        eligible = [row for row in eligible if row["market_regime"]]
    if not eligible:
        return []
    rank_maps: dict[str, list[float]] = {key: [float(row[key]) for row in eligible] for key in needed}
    scored = []
    for row in eligible:
        score = 0.0
        ranks = {}
        for key, weight in spec.weights.items():
            higher = weight >= 0
            rank = rank_percentile(rank_maps[key], float(row[key]), higher=higher)
            ranks[key] = rank
            score += abs(weight) * rank
        scored.append({**row, "score": score, "feature_ranks": ranks})
    scored.sort(key=lambda row: (-row["score"], row["symbol"]))
    return scored


def net_return(entry: float, exit_: float, fee: float, slip: float) -> float:
    entry_fill = entry * (1.0 + slip)
    exit_fill = exit_ * (1.0 - slip)
    return (exit_fill * (1.0 - fee)) / (entry_fill * (1.0 + fee)) - 1.0


def bootstrap_lower(values: list[float], seed: int = 20260919, draws: int = 2000) -> float:
    if not values:
        return 0.0
    rng = random.Random(seed)
    samples: list[float] = []
    block = min(5, len(values))
    for _ in range(draws):
        chosen: list[float] = []
        while len(chosen) < len(values):
            start = rng.randrange(len(values))
            chosen.extend(values[(start + j) % len(values)] for j in range(block))
        samples.append(math.prod(1.0 + x for x in chosen[:len(values)]) - 1.0)
    samples.sort()
    return samples[max(0, int(draws * 0.025))]


def simulate(
    features: dict[str, dict[str, dict]],
    benchmark: dict[str, dict],
    dates: list[str],
    spec: ModelSpec | None,
    fee: float = FEE,
    slip: float = SLIP,
    allocation: float = ALLOCATION,
) -> tuple[dict, list[dict], list[dict], list[dict]]:
    equity = CAPITAL
    peak = equity
    max_dd = 0.0
    daily: list[dict] = []
    trades: list[dict] = []
    decisions: list[dict] = []
    equity_rows: list[dict] = []
    returns: list[float] = []
    universe = sorted(features)
    date_set = set(dates)
    for idx, date in enumerate(dates):
        if idx + 1 >= len(dates):
            break
        next_date = dates[idx + 1]
        if next_date not in date_set:
            continue
        rows = [features[symbol][date] for symbol in universe if date in features[symbol] and next_date in features[symbol]]
        if spec is None:
            selected = None
            scored = []
        else:
            scored = score_candidates(rows, spec)
            selected = scored[0] if scored else None
        if spec is None:
            entry = benchmark[date]["adj_close"]
            exit_ = benchmark[next_date]["adj_open"] if next_date in benchmark else None
            selected_symbol = BENCHMARK
            score = None
            feature_payload = {}
        elif selected is None:
            entry = exit_ = None
            selected_symbol = "CASH"
            score = None
            feature_payload = {}
        else:
            entry = selected["adj_close"]
            exit_ = selected[next_date]["adj_open"] if isinstance(selected.get(next_date), dict) else features[selected["symbol"]][next_date]["adj_open"]
            selected_symbol = selected["symbol"]
            score = selected["score"]
            feature_payload = {key: selected.get(key) for key in ("day_return", "overnight_mean20", "overnight_mean60", "mom5", "mom20", "mom60", "resid_mom20", "vol20", "volume_ratio20", "close_location", "market_regime")}
        if entry is not None and exit_ is not None:
            gross = exit_ / entry - 1.0
            trade_return = net_return(entry, exit_, fee, slip) * allocation
        else:
            gross = trade_return = 0.0
        before = equity
        equity *= 1.0 + trade_return
        peak = max(peak, equity)
        max_dd = max(max_dd, 1.0 - equity / peak)
        returns.append(trade_return)
        daily.append({"date": date, "next_date": next_date, "symbol": selected_symbol, "gross_return_pct": gross * 100.0, "net_return_pct": trade_return * 100.0, "equity": equity, "market_regime": bool(benchmark[date].get("regime", False))})
        equity_rows.append({"date": date, "equity": equity, "symbol": selected_symbol})
        decision = {"date": date, "next_date": next_date, "model": spec.id if spec else "benchmark_spy", "symbol": selected_symbol, "score": score, "entry_adj_close": entry, "exit_adj_open": exit_, "gross_return_pct": gross * 100.0, "net_return_pct": trade_return * 100.0, "equity_before": before, "equity_after": equity, **feature_payload}
        decisions.append(decision)
        if selected_symbol not in ("CASH", BENCHMARK):
            trades.append({"model": spec.id, "entry_date": date, "exit_date": next_date, "symbol": selected_symbol, "entry_adj_close": entry, "exit_adj_open": exit_, "gross_return_pct": gross * 100.0, "net_return_pct": trade_return * 100.0, "allocation": allocation, "fee": fee, "slippage": slip, "entry_dollar_volume": selected["dollar_volume"]})
    win_count = sum(1 for r in returns if r > 0)
    gains = sum(r for r in returns if r > 0)
    losses = -sum(r for r in returns if r < 0)
    period_return = equity / CAPITAL - 1.0
    raw_sharpe = (statistics.fmean(returns) / statistics.pstdev(returns) * math.sqrt(PERIODS_PER_YEAR)) if len(returns) > 1 and statistics.pstdev(returns) else 0.0
    power = power_metrics(returns, cash_rate=CASH_RATE, periods_per_year=PERIODS_PER_YEAR)
    summary = {
        "model": spec.id if spec else "benchmark_spy_legacy",
        "name": spec.name if spec else "SPY overnight legacy benchmark",
        "return_pct": period_return * 100.0,
        "ending_equity": equity,
        "net_pnl": equity - CAPITAL,
        "max_drawdown_pct": max_dd * 100.0,
        "trades": len(trades),
        "wins": win_count,
        "win_rate_pct": (win_count / len(returns) * 100.0) if returns else 0.0,
        "profit_factor": gains / losses if losses > 0 else (float("inf") if gains > 0 else 0.0),
        "mean_daily_return_pct": statistics.fmean(returns) * 100.0 if returns else 0.0,
        "daily_sharpe": raw_sharpe,
        "bootstrap_lower_95_pct": bootstrap_lower(returns) * 100.0,
        "fee": fee,
        "slippage": slip,
        "allocation": allocation,
    }
    summary.update(power)
    return summary, daily, trades, decisions


def evaluate(data: dict[str, list[dict]], audit: dict, *, preregistration: dict | None = None, registry_info: dict | None = None) -> dict:
    features = feature_rows(data)
    benchmark_rows = {r["date"]: r.copy() for r in data[BENCHMARK]}
    # Attach regime to benchmark rows for the decision ledger.
    closes = [r["adj_close"] for r in data[BENCHMARK]]
    dates_b = [r["date"] for r in data[BENCHMARK]]
    for i, date in enumerate(dates_b):
        ma50 = rolling_mean(closes, i, 50)
        ma200 = rolling_mean(closes, i, 200)
        benchmark_rows[date]["regime"] = bool(ma50 is not None and ma200 is not None and ma50 > ma200)
    all_summary: list[dict] = []
    all_daily: list[dict] = []
    all_trades: list[dict] = []
    all_decisions: list[dict] = []
    all_equity: list[dict] = []
    results_by_period: dict[str, dict] = {}
    benchmark_report: dict[str, list[dict]] = {}
    date_intersection = sorted(set(benchmark_rows) & set().union(*(set(v) for v in features.values())))
    for period, (start, end) in PERIODS.items():
        dates = [d for d in date_intersection if start.isoformat() <= d < end.isoformat()]
        results_by_period[period] = {}
        benchmark_report[period] = []
        spy_returns = close_to_close_returns(benchmark_rows, dates)
        spy_buy_hold = benchmark_summary(period=period, model="spy_buy_hold", name="SPY buy-and-hold", returns=spy_returns, cash_rate=CASH_RATE, periods_per_year=PERIODS_PER_YEAR, extra={"total_return_pct": buy_hold_return(benchmark_rows, dates), "benchmark_definition": "split/dividend-adjusted SPY close-to-close total return; no trading friction"})
        benchmark_report[period].append(spy_buy_hold)
        all_summary.append(spy_buy_hold)
        for spec in MODELS:
            summary, daily, trades, decisions = simulate(features, benchmark_rows, dates, spec)
            summary["period"] = period
            summary["quality"] = summary["return_pct"] - 0.5 * summary["max_drawdown_pct"] + 0.02 * math.log1p(summary["trades"])
            strategy_returns = [float(row["net_return_pct"]) / 100.0 for row in daily]
            aligned_spy = []
            for row in daily:
                first = benchmark_rows.get(row["date"])
                second = benchmark_rows.get(row["next_date"])
                if first and second and first.get("adj_close") and second.get("adj_close"):
                    aligned_spy.append(float(second["adj_close"]) / float(first["adj_close"]) - 1.0)
            matched_returns, matched_meta = matched_spy_returns(aligned_spy, strategy_returns, cash_rate=CASH_RATE, borrow_spread=BORROW_SPREAD, periods_per_year=PERIODS_PER_YEAR)
            matched = benchmark_summary(period=period, model=f"spy_buy_hold_matched::{spec.id}", name=f"SPY matched to {spec.name}", returns=matched_returns, cash_rate=CASH_RATE, periods_per_year=PERIODS_PER_YEAR, extra={"matched_to_model": spec.id, "benchmark_definition": "SPY scaled to the model's realized volatility with cash at 4% and borrowing at cash + 50bps", **matched_meta})
            summary.update({
                "spy_buy_hold_return_pct": spy_buy_hold["return_pct"],
                "spy_buy_hold_matched_return_pct": matched["return_pct"],
                "excess_vs_spy_buy_hold_pct": summary["return_pct"] - spy_buy_hold["return_pct"],
                "excess_vs_spy_buy_hold_matched_pct": summary["return_pct"] - matched["return_pct"],
                "matched_spy_leverage": matched_meta["matched_leverage"],
                "matched_spy_strategy_vol_pct": matched_meta["strategy_vol_pct"],
                "matched_spy_vol_pct": matched_meta["spy_vol_pct"],
            })
            results_by_period[period][summary["model"]] = summary
            all_summary.append(summary)
            benchmark_report[period].append(matched)
            all_summary.append(matched)
            all_daily.extend({"period": period, "model": summary["model"], **row} for row in daily)
            all_equity.extend({"period": period, "model": summary["model"], **row} for row in daily)
            all_trades.extend({"period": period, **row} for row in trades)
            all_decisions.extend({"period": period, **row} for row in decisions)
        # The benchmark is reported beside each model above.  Keeping it in a
        # separate section makes it difficult to accidentally rank a benchmark
        # as if it were a strategy.
    validation = [r for r in all_summary if r["period"] == "validation" and r["model"] in {spec.id for spec in MODELS}]
    validation_ranking = sorted(({
        "model": row["model"],
        "name": row["name"],
        "quality": row["quality"],
        "return_pct": row["return_pct"],
        "sharpe": row["sharpe"],
        "selection_used": False,
    } for row in validation), key=lambda row: (-float(row["quality"]), row["model"]))
    champion = None
    stress: list[dict] = []
    holdout_dates = [d for d in date_intersection if PERIODS["holdout"][0].isoformat() <= d < PERIODS["holdout"][1].isoformat()]
    recent_dates = [d for d in date_intersection if PERIODS["recent"][0].isoformat() <= d < PERIODS["recent"][1].isoformat()]
    for period, dates in (("holdout", holdout_dates), ("recent", recent_dates)):
        for spec in MODELS:
            scenarios = (("base", FEE, SLIP, ALLOCATION), ("double_cost", FEE * 2, SLIP * 2, ALLOCATION), ("half_allocation", FEE, SLIP, 0.5))
            for name, fee, slip, allocation in scenarios:
                result, _, _, _ = simulate(features, benchmark_rows, dates, spec, fee=fee, slip=slip, allocation=allocation)
                stress.append({"period": period, "model": spec.id, "scenario": name, **{k: result[k] for k in ("return_pct", "net_pnl", "max_drawdown_pct", "trades", "win_rate_pct", "profit_factor", "bootstrap_lower_95_pct", "sharpe", "sharpe_ci_low", "sharpe_ci_high", "n_obs", "min_detectable_sharpe", "power_verdict")}})
    gates: list[dict] = []
    for spec in MODELS:
        holdout = results_by_period["holdout"][spec.id]
        doubled = next(x for x in stress if x["period"] == "holdout" and x["model"] == spec.id and x["scenario"] == "double_cost")
        checks = {
            "positive_holdout": holdout["return_pct"] > 0,
            "beats_matched_spy": holdout["excess_vs_spy_buy_hold_matched_pct"] > 0,
            "drawdown_at_most_15pct": holdout["max_drawdown_pct"] <= 15.0,
            "positive_double_cost": doubled["return_pct"] > 0,
            "bootstrap_lower_above_zero": holdout["bootstrap_lower_95_pct"] > 0,
            "sharpe_ci_above_zero": holdout["sharpe_ci_low"] > 0,
        }
        gates.append({"model": spec.id, "passes": all(checks.values()), "checks": checks})
    latest_date = date_intersection[-1]
    latest_signal: dict = {"date": latest_date, "next_session_available": False, "champion_from_validation": champion, "selection_policy": "no validation champion is selected; all model lines remain visible", "models": []}
    rows = [features[s][latest_date] for s in sorted(features) if latest_date in features[s]]
    for spec in MODELS:
        scored = score_candidates(rows, spec)
        pick = scored[0] if scored else None
        latest_signal["models"].append({"model": spec.id, "name": spec.name, "symbol": pick["symbol"] if pick else "CASH", "score": pick["score"] if pick else None, "market_regime": benchmark_rows[latest_date].get("regime", False), "top_candidates": [{"symbol": x["symbol"], "score": x["score"]} for x in scored[:5]]})
    report = {
        "created_utc": dt.datetime.now(UTC).isoformat(),
        "universe": [s for s in sorted(features)],
        "benchmark": BENCHMARK,
        "benchmarks": benchmark_report,
        "capital": CAPITAL,
        "assumptions": {"entry": "regular-session close", "exit": "next regular-session open", "fee_each_side": FEE, "slippage_each_side": SLIP, "allocation": ALLOCATION, "cash_rate": CASH_RATE, "borrow_spread": BORROW_SPREAD, "min_price": MIN_PRICE, "min_dollar_volume": MIN_DOLLAR_VOLUME, "adjustment": "Yahoo split/dividend adjusted OHLC", "news_gate": "not used in historical backtest; Alpaca/Benzinga adapter is ready for paper-forward use"},
        "periods": {k: {"start": v[0].isoformat(), "end_exclusive": v[1].isoformat()} for k, v in PERIODS.items()},
        "models": [asdict(s) for s in MODELS],
        "validation_champion": champion,
        "validation_ranking_descriptive_only": validation_ranking,
        "selection_policy": "No champion is selected by validation rank. All candidates and the matched benchmarks are reported.",
        "summary": all_summary,
        "stress": stress,
        "gates": gates,
        "latest_signal": latest_signal,
        "data_audit": audit,
        "preregistration": {"run_id": preregistration.get("run_id"), "spec_hash": preregistration.get("spec_hash")} if preregistration else None,
        "holdout_registry": registry_info,
        "no_live_orders": True,
        "no_news_orders": True,
        "notes": [
            "One long stock is selected after the close and sold at the next regular-session open; this is an overnight trade, not a same-day flat day trade.",
            "SPY buy-and-hold is the no-turnover total-return benchmark. SPY buy-and-hold matched scales SPY to each model's realized volatility with cash at 4% and borrowing at cash + 50bps.",
            "The fixed universe is liquid large-cap names and ETFs, which leaves survivorship bias; this is a research screen rather than a production claim.",
            "Every Sharpe estimate carries Lo-style standard error, confidence interval, and minimum detectable Sharpe. Rows with a non-positive lower confidence bound are INCONCLUSIVE.",
            "A close-to-open result can be dominated by the opening auction and overnight gaps. Use quote-aware Alpaca paper fills before any capital.",
        ],
    }
    predicted = (preregistration or {}).get("predicted_effect", {})
    predicted_value = predicted.get("value")
    predicted_range = predicted.get("range") or []
    report["prediction_comparison"] = [
        {
            "period": row["period"],
            "model": row["model"],
            "metric": predicted.get("metric", "sharpe"),
            "predicted_value": predicted_value,
            "predicted_range": predicted_range,
            "realized_value": row.get("sharpe"),
            "inside_predicted_range": bool(predicted_range and predicted_range[0] <= row.get("sharpe", float("nan")) <= predicted_range[1]),
            "power_verdict": row.get("power_verdict"),
        }
        for row in all_summary
        if row.get("model") in {spec.id for spec in MODELS}
    ]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "frozen_config.json").write_text(json.dumps({"models": [asdict(s) for s in MODELS], "periods": report["periods"], "universe": report["universe"], "assumptions": report["assumptions"], "validation_champion": None, "selection_policy": report["selection_policy"], "preregistration": report["preregistration"], "holdout_registry": report["holdout_registry"]}, indent=2, sort_keys=True), encoding="utf-8")
    (OUT / "results.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    write_csv(OUT / "summary.csv", all_summary)
    write_csv(OUT / "stress.csv", stress)
    write_csv(OUT / "daily_results.csv", all_daily)
    write_csv(OUT / "equity.csv", all_equity)
    write_csv(OUT / "trade_log.csv", all_trades)
    write_csv(OUT / "decision_log.csv", all_decisions)
    (OUT / "latest_signal.json").write_text(json.dumps(latest_signal, indent=2, sort_keys=True), encoding="utf-8")
    from scoreboard import write_scoreboard
    write_scoreboard(report, OUT / "SCOREBOARD.md")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="redownload Yahoo daily bars")
    parser.add_argument("--reopen-holdout", action="store_true", help="explicitly reopen a previously scored holdout for an audit migration; default is to refuse")
    args = parser.parse_args()
    import holdout_registry
    import preregister

    expected_prereg = preregister.overnight_spec()
    preregistration = preregister.require_matching(expected_prereg)
    data, audit = fetch_all(force=args.refresh)
    if BENCHMARK not in data:
        raise SystemExit("SPY benchmark data is unavailable")
    registry_path = OUT / "holdout_registry.json"
    if not registry_path.exists() and (OUT / "results.json").exists():
        holdout_registry.bootstrap_from_results(registry_path, OUT / "results.json", [BENCHMARK, *UNIVERSE])
    periods = {name: {"start": bounds[0].isoformat(), "end_exclusive": bounds[1].isoformat()} for name, bounds in PERIODS.items()}
    registry_info = holdout_registry.register(registry_path, universe=[BENCHMARK, *UNIVERSE], periods=periods, run_id=preregistration["run_id"], allow_reopen=args.reopen_holdout, reason="Phase 1 benchmark and power migration")
    for warning in registry_info.get("warnings", []):
        print(warning, flush=True)
    report = evaluate(data, audit, preregistration=preregistration, registry_info={"path": str(registry_path), "warnings": registry_info.get("warnings", []), "reopened": args.reopen_holdout})
    print(json.dumps({"validation_champion": report["validation_champion"], "selection_policy": report["selection_policy"], "latest": report["latest_signal"], "holdout": [x for x in report["summary"] if x["period"] == "holdout" and x["model"] in {spec.id for spec in MODELS}]}, indent=2))


if __name__ == "__main__":
    main()
