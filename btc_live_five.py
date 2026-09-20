"""BTC-only live-style comparison using the lab's audited 4h/1h engine.

This companion study does not force a UTC midnight exit. BTC trades 24/7, so
the position may remain open across calendar days until its rule, stop, or
drawdown guard exits it. Daily returns are still recorded for review.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import random
import statistics
from dataclasses import asdict, replace
from pathlib import Path

import focused_engine as fe
from app import andean, laguerre_filter, mcginley
from focused_research import datasets
from research_weekly import iso, sha256


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "research" / "btc_live_five"
UTC = dt.timezone.utc
CAPITAL = 10_000.0


CONFIGS = [
    fe.Config("trend", 20),
    fe.Config("breakout", 20),
    fe.Config("pullback", 35),
    fe.Config("andean", 35),
    fe.Config("mcginley", 20),
]
BASE_RULES = fe.rules


def stamp(text: str) -> int:
    return int(dt.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)


def extra_features(hourly, incomplete_hours=()):
    bars = fe.aggregate(hourly, incomplete_hours)
    closes = [b.close for b in bars]
    bulls, bears = andean(bars, 35)
    lag = laguerre_filter(closes, 0.5)
    mcg = mcginley(closes, 20)
    out = {}
    for i, b in enumerate(bars):
        if i == 0:
            continue
        out[b.time + fe.FOUR] = {
            "andean_bull": bulls[i],
            "andean_bear": bears[i],
            "andean_bull_previous": bulls[i - 1],
            "andean_bear_previous": bears[i - 1],
            "laguerre": lag[i],
            "mcginley20": mcg[i],
        }
    return out


def rules(config, f):
    if config.family not in {"andean", "mcginley"}:
        return BASE_RULES(config, f)
    regime = f["close"] > f["ema200"] and f["ema200"] > f["ema200_prior6"]
    if config.family == "andean":
        bull_cross = f["andean_bull_previous"] <= f["andean_bear_previous"] and f["andean_bull"] > f["andean_bear"]
        bear_cross = f["andean_bear_previous"] <= f["andean_bull_previous"] and f["andean_bear"] > f["andean_bull"]
        entry = bull_cross and f["close"] > f["laguerre"] and regime
        exit_ = bear_cross or f["close"] < f["laguerre"] or f["close"] < f["ema200"]
    else:
        entry = f["close"] > f["mcginley20"] and regime
        exit_ = f["close"] < f["mcginley20"] or f["close"] < f["ema200"]
    return entry, exit_, regime


def daily_rows(result: dict) -> list[dict]:
    points: dict[str, float] = {}
    for t, value in zip(result["times"][1:], result["curve"][1:]):
        d = iso(t - 1)[:10]
        points[d] = value
    previous = CAPITAL
    out = []
    for d, value in sorted(points.items()):
        out.append({"date": d, "equity": value, "return_pct": 100.0 * (value / previous - 1.0)})
        previous = value
    return out


def bootstrap(rows: list[dict], seed: int = 20260918, draws: int = 2000) -> dict:
    values = [float(row["return_pct"]) / 100.0 for row in rows]
    if not values:
        return {"lower_95_pct": 0.0, "upper_95_pct": 0.0, "positive_fraction": 0.0, "days": 0}
    rng = random.Random(seed)
    samples = []
    while len(samples) < draws:
        chosen = []
        while len(chosen) < len(values):
            start = rng.randrange(len(values))
            chosen.extend(values[(start + j) % len(values)] for j in range(min(7, len(values))))
        samples.append(100.0 * (math.prod(1.0 + x for x in chosen[: len(values)]) - 1.0))
    samples.sort()
    return {"method": "Circular seven-day blocks; 2,000 resamples", "lower_95_pct": samples[50], "upper_95_pct": samples[-51], "positive_fraction": sum(x > 0 for x in samples) / len(samples), "days": len(values)}


def clean(result: dict, daily: list[dict]) -> dict:
    keys = ("model", "return_pct", "net_pnl", "ending_equity", "max_drawdown_pct", "trades", "wins", "win_rate_pct", "profit_factor", "fees", "slippage", "turnover", "hours_in_market_pct", "mean_capital_exposure_pct", "halted")
    out = {k: result.get(k) for k in keys}
    returns = [float(x["return_pct"]) for x in daily]
    out["mean_daily_return_pct"] = statistics.fmean(returns) if returns else 0.0
    out["daily_sharpe"] = (statistics.fmean(returns) / statistics.pstdev(returns) * math.sqrt(365.0)) if len(returns) > 1 and statistics.pstdev(returns) else 0.0
    out["days"] = len(daily)
    return out


def score(row: dict) -> float:
    pf = min(float(row.get("profit_factor") or 0.0), 3.0)
    return float(row["return_pct"]) - 1.5 * float(row["max_drawdown_pct"]) + 0.05 * math.log1p(pf) - (0.25 if row["trades"] < 20 else 0.0)


def run():
    data, fmap, exchange_filters, audit = datasets()
    bars = data["BTCUSDT"]
    incomplete = {stamp(x["open_utc"]) for x in next(a for a in audit["audits"] if a["symbol"] == "BTCUSDT")["shortened_candles"]}
    features = fmap["BTCUSDT"]
    for key, extra in extra_features(bars, incomplete).items():
        if key in features:
            features[key].update(extra)
    periods = {
        "development": (stamp("2021-07-01T00:00:00Z"), stamp("2023-01-01T00:00:00Z")),
        "validation": (stamp("2023-01-01T00:00:00Z"), stamp("2024-01-01T00:00:00Z")),
        "holdout": (stamp("2024-01-01T00:00:00Z"), stamp("2025-01-01T00:00:00Z")),
        "regression": (stamp("2025-01-01T00:00:00Z"), stamp("2026-09-18T00:00:00Z")),
    }
    old_rules = fe.rules
    fe.rules = rules
    all_summary = []
    all_daily = []
    all_equity = []
    all_trades = []
    all_fills = []
    all_decisions = []
    results = {}
    try:
        for period, (start, end) in periods.items():
            results[period] = {}
            for config in CONFIGS:
                result = fe.simulate(bars, features, config, exchange_filters["BTCUSDT"], start, end, collect=period == "holdout", capital=CAPITAL)
                daily = daily_rows(result)
                row = clean(result, daily)
                row["period"] = period
                row["quality"] = score(row)
                results[period][config.id] = row
                all_summary.append(row)
                all_daily.extend({"period": period, "bot": config.id, **x} for x in daily)
                all_equity.extend({"period": period, "bot": config.id, "time_utc": iso(t), "equity": v} for t, v in zip(result["times"], result["curve"]))
                if period == "holdout":
                    all_trades.extend({"period": period, "bot": config.id, **x} for x in result["trade_rows"])
                    all_fills.extend({"period": period, "bot": config.id, **x} for x in result["fill_rows"])
                    all_decisions.extend({"period": period, "bot": config.id, **x} for x in result["decision_rows"])
                print(period, config.id, round(row["return_pct"], 3), row["trades"], flush=True)
        validation_rank = sorted((dict(results["validation"][c.id]) for c in CONFIGS), key=lambda x: x["quality"], reverse=True)
        champion = validation_rank[0]["model"]
        stress = []
        gates = []
        for config in CONFIGS:
            start, end = periods["holdout"]
            base = fe.simulate(bars, features, config, exchange_filters["BTCUSDT"], start, end, capital=CAPITAL)
            zero = fe.simulate(bars, features, replace(config, fee=0.0, slip=0.0), exchange_filters["BTCUSDT"], start, end, capital=CAPITAL)
            double = fe.simulate(bars, features, replace(config, fee=config.fee * 2, slip=config.slip * 2), exchange_filters["BTCUSDT"], start, end, capital=CAPITAL)
            delayed = fe.simulate(bars, features, replace(config, delay=1), exchange_filters["BTCUSDT"], start, end, capital=CAPITAL)
            for name, r in (("base", base), ("zero_cost", zero), ("double_cost", double), ("one_hour_delay", delayed)):
                stress.append({"bot": config.id, "scenario": name, "return_pct": r["return_pct"], "net_pnl": r["net_pnl"], "max_drawdown_pct": r["max_drawdown_pct"], "trades": r["trades"], "fees": r["fees"], "slippage": r["slippage"]})
            boot = bootstrap(daily_rows(base))
            checks = {"positive_holdout": base["return_pct"] > 0, "at_least_20_trades": base["trades"] >= 20, "drawdown_at_most_15pct": base["max_drawdown_pct"] <= 15, "positive_double_cost": double["return_pct"] > 0, "positive_one_hour_delay": delayed["return_pct"] > 0, "bootstrap_lower_above_zero": boot["lower_95_pct"] > 0}
            gates.append({"bot": config.id, "passes": all(checks.values()), "checks": checks, "bootstrap": boot})
    finally:
        fe.rules = old_rules
    frozen = {"created_utc": dt.datetime.now(UTC).isoformat(), "data_hashes": {a["symbol"]: a["sha256"] for a in audit["audits"] if a.get("symbol") == "BTCUSDT"}, "engine_sha256": sha256(ROOT / "focused_engine.py"), "configs": [asdict(c) for c in CONFIGS], "periods": {k: {"start": iso(v[0]), "end_exclusive": iso(v[1])} for k, v in periods.items()}, "champion_before_holdout": champion, "selection_note": "Five BTC-only live-style bots; champion selected from validation only."}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "frozen_config.json").write_text(json.dumps(frozen, indent=2), encoding="utf-8")
    report = {"created_utc": dt.datetime.now(UTC).isoformat(), "frozen": frozen, "summary": all_summary, "stress": stress, "gates": gates, "results_by_period": results, "model_catalog": [asdict(c) for c in CONFIGS], "no_live_orders": True, "no_kimi": True, "notes": ["BTC is treated as a 24/7 market; positions are not force-flattened at UTC midnight.", "The data are Binance spot candles used with a long/cash engine; shorts are not simulated in this live-style companion.", "The regression period overlaps previously inspected data and is descriptive, not untouched validation."]}
    (OUT / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    from btc_five_research import write_csv
    write_csv(OUT / "summary.csv", all_summary)
    write_csv(OUT / "daily_results.csv", all_daily)
    write_csv(OUT / "equity.csv", all_equity)
    write_csv(OUT / "stress.csv", stress)
    write_csv(OUT / "trade_log.csv", all_trades)
    write_csv(OUT / "fills.csv", all_fills)
    write_csv(OUT / "decisions.csv", all_decisions)
    write_csv(OUT / "model_catalog.csv", [asdict(c) for c in CONFIGS])
    print("CHAMPION", champion, "GATES", json.dumps(gates), flush=True)
    return report


if __name__ == "__main__":
    run()
