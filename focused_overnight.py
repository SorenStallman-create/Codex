"""Focused, paper-only replay for the strongest overnight model subset.

The broad overnight study contains five model families.  This runner freezes
the top fraction using the validation block only, then replays the same frozen
models over two adjacent 90-session windows at the end of the available data.
The default is the top 30% (two of five models): overnight momentum and
intraday reversal.  A 50%-maximum portfolio version gives each selected model
25% notional and leaves the rest in cash.

This is a historical shadow replay, not a claim of live profitability.  The
separate Alpaca adapter remains paper-only and requires credentials before it
can collect quote-aware fills and news in forward time.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import statistics
from pathlib import Path

import overnight_equities as oe


OUT = oe.OUT / "focused_top30"
CAPITAL = oe.CAPITAL
DEFAULT_FRACTION = 0.30
DEFAULT_SESSIONS = 90
DEFAULT_TOTAL_ALLOCATION = 0.50


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def local_data() -> dict[str, list[dict]]:
    data: dict[str, list[dict]] = {}
    for path in sorted(oe.DATA.glob("*.jsonl")):
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if rows:
            data[path.stem] = rows
    if oe.BENCHMARK not in data:
        raise SystemExit(f"Missing benchmark data: {oe.DATA / (oe.BENCHMARK + '.jsonl')}")
    return data


def benchmark_rows(data: dict[str, list[dict]]) -> dict[str, dict]:
    rows = {row["date"]: row.copy() for row in data[oe.BENCHMARK]}
    closes = [row["adj_close"] for row in data[oe.BENCHMARK]]
    dates = [row["date"] for row in data[oe.BENCHMARK]]
    for index, date in enumerate(dates):
        ma50 = oe.rolling_mean(closes, index, 50)
        ma200 = oe.rolling_mean(closes, index, 200)
        rows[date]["regime"] = bool(ma50 is not None and ma200 is not None and ma50 > ma200)
    return rows


def select_models(results: dict, fraction: float) -> tuple[list[dict], list[dict]]:
    model_ids = {model.id for model in oe.MODELS}
    candidates = [
        row for row in results.get("summary", [])
        if row.get("period") == "validation" and row.get("model") in model_ids
    ]
    if not candidates:
        raise SystemExit("No validation model results found; run overnight_equities.py first")
    candidates.sort(key=lambda row: (-float(row.get("quality", float("-inf"))), row["model"]))
    count = max(1, min(len(candidates), math.ceil(len(candidates) * fraction)))
    ranked = []
    for index, row in enumerate(candidates, start=1):
        ranked.append({
            "rank": index,
            "model": row["model"],
            "name": row["name"],
            "validation_return_pct": row["return_pct"],
            "validation_drawdown_pct": row["max_drawdown_pct"],
            "validation_quality": row["quality"],
            "selected": index <= count,
        })
    selected_ids = {row["model"] for row in ranked if row["selected"]}
    selected = [row for row in ranked if row["model"] in selected_ids]
    return selected, ranked


def common_dates(features: dict[str, dict[str, dict]], benchmark: dict[str, dict]) -> list[str]:
    dates = set(benchmark)
    for rows in features.values():
        dates.update(rows)
    # A date is usable only when every selected model can see at least one
    # candidate and the benchmark has a bar.  The simulator applies its own
    # feature eligibility checks afterward.
    return sorted(set(benchmark) & set().union(*(set(rows) for rows in features.values())))


def window_dates(all_dates: list[str], sessions: int, which: str) -> list[str]:
    # Passing sessions+1 dates gives the simulator an exit date for each of the
    # requested sessions.  The final available bar (a Friday here) is only an
    # exit, never an entry with a missing next session.
    if len(all_dates) < sessions + 1:
        raise SystemExit(f"Need at least {sessions + 1} complete sessions; found {len(all_dates)}")
    if which == "recent_90":
        return all_dates[-(sessions + 1):]
    if which == "prior_90":
        end = len(all_dates) - sessions
        start = end - (sessions + 1)
        if start < 0:
            raise SystemExit("Not enough history for prior window")
        return all_dates[start:end]
    raise ValueError(which)


def model_replay(
    features: dict[str, dict[str, dict]],
    benchmark: dict[str, dict],
    dates: list[str],
    spec: oe.ModelSpec,
    fee: float,
    slip: float,
) -> tuple[dict, list[dict], list[dict], list[dict]]:
    return oe.simulate(features, benchmark, dates, spec, fee=fee, slip=slip, allocation=1.0)


def portfolio_replay(
    model_payloads: dict[str, tuple[dict, list[dict], list[dict], list[dict]]],
    total_allocation: float,
    window: str,
    fee: float,
    slip: float,
) -> tuple[dict, list[dict]]:
    model_ids = list(model_payloads)
    daily_by_model = {
        model: {row["date"]: row for row in payload[1]}
        for model, payload in model_payloads.items()
    }
    dates = sorted(set.intersection(*(set(rows) for rows in daily_by_model.values()))) if daily_by_model else []
    weight = total_allocation / len(model_ids) if model_ids else 0.0
    equity = CAPITAL
    peak = CAPITAL
    max_dd = 0.0
    returns: list[float] = []
    rows: list[dict] = []
    for date in dates:
        legs = [daily_by_model[model][date] for model in model_ids]
        leg_returns = [float(leg["net_return_pct"]) / 100.0 for leg in legs]
        portfolio_return = sum(weight * value for value in leg_returns)
        before = equity
        equity *= 1.0 + portfolio_return
        peak = max(peak, equity)
        max_dd = max(max_dd, 1.0 - equity / peak)
        returns.append(portfolio_return)
        rows.append({
            "window": window,
            "date": date,
            "next_date": legs[0]["next_date"],
            "symbols": ",".join(f"{model}:{leg['symbol']}" for model, leg in zip(model_ids, legs)),
            "net_return_pct": portfolio_return * 100.0,
            "equity_before": before,
            "equity_after": equity,
            "allocation_total": total_allocation,
            "allocation_per_model": weight,
        })
    wins = sum(1 for value in returns if value > 0)
    gains = sum(value for value in returns if value > 0)
    losses = -sum(value for value in returns if value < 0)
    summary = {
        "window": window,
        "model": "focused_portfolio",
        "name": "Focused top-30% portfolio",
        "return_pct": (equity / CAPITAL - 1.0) * 100.0,
        "ending_equity": equity,
        "net_pnl": equity - CAPITAL,
        "max_drawdown_pct": max_dd * 100.0,
        "trades": len(rows) * len(model_ids),
        "sessions": len(rows),
        "wins": wins,
        "win_rate_pct": (wins / len(returns) * 100.0) if returns else 0.0,
        "profit_factor": gains / losses if losses > 0 else (float("inf") if gains > 0 else 0.0),
        "mean_daily_return_pct": statistics.fmean(returns) * 100.0 if returns else 0.0,
        "daily_sharpe": (statistics.fmean(returns) / statistics.pstdev(returns) * math.sqrt(252.0)) if len(returns) > 1 and statistics.pstdev(returns) else 0.0,
        "bootstrap_lower_95_pct": oe.bootstrap_lower(returns) * 100.0,
        "fee": fee,
        "slippage": slip,
        "allocation": total_allocation,
    }
    return summary, rows


def latest_signal(features: dict[str, dict[str, dict]], benchmark: dict[str, dict], specs: list[oe.ModelSpec], latest_date: str) -> dict:
    rows = [features[symbol][latest_date] for symbol in sorted(features) if latest_date in features[symbol]]
    models = []
    for spec in specs:
        scored = oe.score_candidates(rows, spec)
        pick = scored[0] if scored else None
        models.append({
            "model": spec.id,
            "name": spec.name,
            "symbol": pick["symbol"] if pick else "CASH",
            "score": pick["score"] if pick else None,
            "market_regime": benchmark[latest_date].get("regime", False),
            "top_candidates": [{"symbol": row["symbol"], "score": row["score"]} for row in scored[:5]],
            "next_session_available": False,
        })
    return {"date": latest_date, "models": models, "next_session_available": False, "news_status": "not available in historical replay"}


def run(fraction: float = DEFAULT_FRACTION, sessions: int = DEFAULT_SESSIONS, total_allocation: float = DEFAULT_TOTAL_ALLOCATION, slip: float = oe.SLIP) -> dict:
    if not 0 < fraction <= 1:
        raise SystemExit("--fraction must be between 0 and 1")
    if not 0 < total_allocation <= 1:
        raise SystemExit("--total-allocation must be between 0 and 1")
    data = local_data()
    features = oe.feature_rows(data)
    benchmark = benchmark_rows(data)
    dates = common_dates(features, benchmark)
    if len(dates) < sessions * 2 + 1:
        raise SystemExit(f"Need at least {sessions * 2 + 1} sessions for two windows; found {len(dates)}")
    broad_results = json.loads((oe.OUT / "results.json").read_text(encoding="utf-8"))
    selected_rows, ranked_rows = select_models(broad_results, fraction)
    specs = [next(spec for spec in oe.MODELS if spec.id == row["model"]) for row in selected_rows]

    summaries: list[dict] = []
    model_daily: list[dict] = []
    trades: list[dict] = []
    decisions: list[dict] = []
    portfolio_daily: list[dict] = []
    stress: list[dict] = []
    window_inputs: dict[str, list[str]] = {}
    for window in ("prior_90", "recent_90"):
        sim_dates = window_dates(dates, sessions, window)
        window_inputs[window] = sim_dates
        payloads: dict[str, tuple[dict, list[dict], list[dict], list[dict]]] = {}
        for spec in specs:
            payload = model_replay(features, benchmark, sim_dates, spec, fee=oe.FEE, slip=slip)
            payloads[spec.id] = payload
            summary, daily, model_trades, model_decisions = payload
            summary = {"window": window, **summary, "selection_rank": next(row["rank"] for row in selected_rows if row["model"] == spec.id)}
            summaries.append(summary)
            model_daily.extend({"window": window, "model": spec.id, **row} for row in daily)
            trades.extend({"window": window, **row} for row in model_trades)
            decisions.extend({"window": window, **row} for row in model_decisions)
        portfolio_summary, portfolio_rows = portfolio_replay(payloads, total_allocation, window, oe.FEE, slip)
        summaries.append(portfolio_summary)
        portfolio_daily.extend(portfolio_rows)
        for scenario, allocation, scenario_slip in (
            ("25pct_total", total_allocation / 2.0, slip),
            ("50pct_total", total_allocation, slip),
            ("double_cost_50pct", total_allocation, slip * 2.0),
        ):
            result, _ = portfolio_replay(
                {model: model_replay(features, benchmark, sim_dates, spec, oe.FEE, scenario_slip) for model, spec in ((s.id, s) for s in specs)},
                allocation,
                window,
                oe.FEE,
                scenario_slip,
            )
            stress.append({"window": window, "scenario": scenario, **result})

    latest = dates[-1]
    selection = {
        "method": "top validation quality, frozen before holdout and recent windows",
        "fraction": fraction,
        "candidate_count": len(ranked_rows),
        "selected_count": len(selected_rows),
        "selected_models": selected_rows,
        "all_validation_candidates": ranked_rows,
    }
    report = {
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "selection": selection,
        "run": {
            "sessions_per_window": sessions,
            "total_allocation": total_allocation,
            "allocation_per_model": total_allocation / len(specs),
            "fee_each_side": oe.FEE,
            "slippage_each_side": slip,
            "capital": CAPITAL,
            "entry": "regular-session close",
            "exit": "next regular-session open",
            "data_end": latest,
            "windows": {name: {"start": values[0], "last_entry": values[-2], "exit": values[-1]} for name, values in window_inputs.items()},
        },
        "summary": summaries,
        "stress": stress,
        "latest_signal": latest_signal(features, benchmark, specs, latest),
        "no_live_orders": True,
        "news_used": False,
        "quote_aware_fills": False,
        "notes": [
            "The two selected models are the top 30% of the five broad-study models by validation quality.",
            "The recent windows are a historical shadow replay; they are not a forward guarantee and use daily bars.",
            "The focused portfolio uses 50% total notional (25% per model) and leaves the rest in cash.",
            "Alpaca quote/spread and Benzinga-backed news checks are required for a true paper-forward run.",
            "No order endpoint, credential, or live/paper order was used by this run.",
        ],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "selection.json").write_text(json.dumps(selection, indent=2, sort_keys=True), encoding="utf-8")
    (OUT / "results.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    (OUT / "frozen_config.json").write_text(json.dumps({"selection": selection, "run": report["run"]}, indent=2, sort_keys=True), encoding="utf-8")
    write_csv(OUT / "summary.csv", summaries)
    write_csv(OUT / "stress.csv", stress)
    write_csv(OUT / "model_daily.csv", model_daily)
    write_csv(OUT / "portfolio_daily.csv", portfolio_daily)
    write_csv(OUT / "trade_log.csv", trades)
    write_csv(OUT / "decision_log.csv", decisions)
    (OUT / "latest_signal.json").write_text(json.dumps(report["latest_signal"], indent=2, sort_keys=True), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the focused top-model overnight shadow replay")
    parser.add_argument("--fraction", type=float, default=DEFAULT_FRACTION, help="fraction of five models to keep; default 0.30")
    parser.add_argument("--sessions", type=int, default=DEFAULT_SESSIONS, help="sessions per historical window")
    parser.add_argument("--total-allocation", type=float, default=DEFAULT_TOTAL_ALLOCATION, help="total notional allocated across selected models")
    parser.add_argument("--slippage", type=float, default=oe.SLIP, help="adverse slippage per side, decimal")
    parser.add_argument("--allow-legacy-rank-selection", action="store_true", help="explicitly re-run the archived top-N selector; Phase 1 disables it by default")
    args = parser.parse_args()
    if not args.allow_legacy_rank_selection:
        raise SystemExit("The archived top-N selector is disabled by Phase 1. The current report keeps all model lines and uses no champion selection.")
    report = run(args.fraction, args.sessions, args.total_allocation, args.slippage)
    print(json.dumps({
        "selected_models": [row["model"] for row in report["selection"]["selected_models"]],
        "summary": report["summary"],
        "stress": report["stress"],
        "latest_signal": report["latest_signal"],
    }, indent=2))


if __name__ == "__main__":
    main()
