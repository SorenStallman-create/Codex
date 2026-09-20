"""Frozen-signal paper-forward loop for the overnight equity experiment.

The default command writes a signal only.  ``--record-paper`` enables the
quote-aware ledger and remains simulated at the adverse side of the quote;
there is no live endpoint in this runner.  A future broker submission must be
an explicit, separately reviewed step after the measurement gates clear.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

from alpaca_overnight import historical_bars, historical_news, latest_quotes, submit_paper_order
from overnight_equities import BENCHMARK, DATA, MODELS, OUT, feature_rows, score_candidates
from paper_execution import (
    DEFAULT_MAX_MODEL_ALLOCATION,
    DEFAULT_MAX_QUOTE_AGE_MS,
    DEFAULT_MAX_SPREAD_BPS,
    DEFAULT_MAX_TOTAL_ALLOCATION,
    allocation_plan,
    append_fill,
    conservative_fill_price,
    measurement_summary,
    normalise_quote,
    paper_forward_spec,
    quote_gate,
    slippage_vs_official_bps,
    validate_allocation,
    write_measurement_state,
)
import preregister


NY = ZoneInfo("America/New_York")
UTC = dt.timezone.utc
SEVERE_NEGATIVE = ("fraud", "bankruptcy", "chapter 11", "sec charges", "doj", "indictment", "restatement", "going concern", "offering", "halts trading", "clinical trial halt")
REVIEW_TERMS = ("earnings", "guidance", "downgrade", "lawsuit", "investigation", "recall", "layoffs", "tariff", "acquisition", "merger")
FOCUSED_OUT = OUT / "focused_top30"
PAPER_OUT = OUT / "paper"
PAPER_FILLS = PAPER_OUT / "fills.csv"
PAPER_PENDING = PAPER_OUT / "pending.json"
PAPER_STATE = PAPER_OUT / "measurement_state.json"


def model_selection(selection: str) -> tuple[list, dict]:
    """Return the frozen paper model set and its ranking evidence.

    ``top30`` is a frozen selection file created before the holdout windows.
    It is allowed in Phase 2 without re-ranking.  ``top50`` still requires the
    explicit legacy flag because no frozen top-50 contract exists.
    """
    if selection == "all":
        return list(MODELS), {"method": "all broad-study models", "fraction": 1.0, "selected_models": [m.id for m in MODELS]}
    fraction = 0.30 if selection == "top30" else 0.50
    selection_path = OUT / "focused_top30" / "selection.json"
    if selection_path.exists():
        payload = json.loads(selection_path.read_text(encoding="utf-8"))
        ids = [row["model"] for row in payload.get("selected_models", [])]
        if ids and abs(float(payload.get("fraction", fraction)) - fraction) < 1e-9:
            specs = [model for model in MODELS if model.id in ids]
            return specs, {**payload, "selection_frozen": True, "method": "frozen selection file; no runtime rank selection"}
    results_path = OUT / "results.json"
    if not results_path.exists():
        raise SystemExit("Run overnight_equities.py before using --selection top30/top50")
    results = json.loads(results_path.read_text(encoding="utf-8"))
    model_ids = {model.id for model in MODELS}
    candidates = [row for row in results.get("summary", []) if row.get("period") == "validation" and row.get("model") in model_ids]
    candidates.sort(key=lambda row: (-float(row.get("quality", float("-inf"))), row["model"]))
    count = max(1, min(len(candidates), math.ceil(len(candidates) * fraction)))
    ids = {row["model"] for row in candidates[:count]}
    specs = [model for model in MODELS if model.id in ids]
    payload = {"method": "top validation quality", "fraction": fraction, "selected_models": [row for row in candidates[:count]]}
    return specs, payload


def load_quote_fixture(path: Path) -> dict[str, dict]:
    """Read a quote fixture for deterministic dry runs and unit tests."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload = payload.get("quotes", payload)
    return {symbol: normalise_quote(symbol, raw) for symbol, raw in payload.items() if isinstance(raw, dict)}


def fetch_quotes(symbols: list[str], fixture: Path | None = None, feed: str = "iex") -> dict[str, dict]:
    if fixture:
        return load_quote_fixture(fixture)
    raw = latest_quotes(symbols, feed=feed)
    return {symbol: normalise_quote(symbol, payload) for symbol, payload in raw.items() if isinstance(payload, dict)}


def _news_default() -> dict:
    return {"news_clear": False, "article_count": 0, "severe_negative": [], "review": []}


def _paper_preregistration(model_ids: list[str]) -> dict:
    """Refuse paper measurement if its immutable contract is absent."""
    expected = paper_forward_spec(model_ids)
    return preregister.require_matching(expected, OUT / "prereg")


def _record_entry(
    *,
    picks: list[dict],
    current_rows: dict[str, dict],
    quotes: dict[str, dict],
    equity: float,
    asof: str,
    ledger: Path,
    pending_path: Path,
    max_total_allocation: float,
    max_model_allocation: float,
    max_quote_age_ms: int,
    max_spread_bps: float,
) -> dict:
    allocations = allocation_plan(picks, equity, max_total_allocation=max_total_allocation, max_model_allocation=max_model_allocation)
    allocation_check = validate_allocation(allocations, equity, max_total_allocation=max_total_allocation, max_model_allocation=max_model_allocation)
    if not allocation_check["ok"]:
        raise RuntimeError("allocation reconciliation failed: " + ",".join(allocation_check["errors"]))
    pending: list[dict] = []
    rows: list[dict] = []
    blocked: list[dict] = []
    for item in allocations:
        symbol = item["symbol"]
        model = item["model"]
        quote = quotes.get(symbol, {})
        gate = quote_gate(quote, max_age_ms=max_quote_age_ms, max_spread_bps=max_spread_bps)
        news = item.get("news") or _news_default()
        reasons = []
        if not news.get("news_clear", False):
            reasons.append("severe_negative_news_or_missing_news")
        if not gate["eligible"]:
            reasons.extend(gate["reasons"])
        official_close = (current_rows.get(symbol) or {}).get("close")
        if official_close in (None, ""):
            reasons.append("missing_official_close")
        trade_id = f"{asof}::{model}::{symbol}"
        fill_price = conservative_fill_price("buy", gate) if not reasons else None
        error = ",".join(reasons)
        row = {
            "date": asof,
            "symbol": symbol,
            "model": model,
            "side": "buy",
            "intended_price": official_close,
            "official_close": official_close,
            "official_open": "",
            "quoted_bid": gate.get("bid"),
            "quoted_ask": gate.get("ask"),
            "spread_bps": gate.get("spread_bps"),
            "fill_price": fill_price,
            "slippage_vs_official_bps": slippage_vs_official_bps("buy", fill_price, official_close),
            "notional": item["notional"] if not error else 0.0,
            "news_flag": "clear" if news.get("news_clear") else "severe_or_missing",
            "quote_age_ms": gate.get("quote_age_ms"),
            "partial_fill": "false",
            "error": error,
            "trade_id": trade_id,
            "session_date": asof,
            "leg": "entry",
            "timestamp_utc": dt.datetime.now(UTC).isoformat(),
            "gate_reason": ",".join(gate.get("reasons", [])),
            "order_id": "",
            "fill_source": "Alpaca IEX quote; adverse-side simulated paper fill",
        }
        rows.append(append_fill(ledger, row))
        if error:
            blocked.append({"trade_id": trade_id, "symbol": symbol, "model": model, "error": error})
        else:
            pending.append({"trade_id": trade_id, "session_date": asof, "symbol": symbol, "model": model, "notional": item["notional"], "entry_official_close": official_close})
    pending_path.parent.mkdir(parents=True, exist_ok=True)
    pending_path.write_text(json.dumps(pending, indent=2, sort_keys=True), encoding="utf-8")
    return {"rows": rows, "pending": pending, "blocked": blocked, "allocation": allocation_check}


def _record_exit(
    *,
    pending_path: Path,
    current_rows: dict[str, dict],
    quotes: dict[str, dict],
    news: dict[str, dict],
    asof: str,
    ledger: Path,
    max_quote_age_ms: int,
    max_spread_bps: float,
) -> dict:
    pending = json.loads(pending_path.read_text(encoding="utf-8")) if pending_path.exists() else []
    if not pending:
        return {"rows": [], "closed": [], "halted": False, "message": "no pending entry fills"}
    rows: list[dict] = []
    closed: list[dict] = []
    halted = False
    for item in pending:
        symbol = item["symbol"]
        quote = quotes.get(symbol, {})
        gate = quote_gate(quote, max_age_ms=max_quote_age_ms, max_spread_bps=max_spread_bps)
        news_item = news.get(symbol) or _news_default()
        reasons = []
        if not news_item.get("news_clear", False):
            reasons.append("severe_negative_news_or_missing_news")
        if not gate["eligible"]:
            reasons.extend(gate["reasons"])
        official_open = (current_rows.get(symbol) or {}).get("open")
        if official_open in (None, ""):
            reasons.append("missing_official_open")
        fill_price = conservative_fill_price("sell", gate) if not reasons else None
        error = ",".join(reasons)
        row = {
            "date": asof,
            "symbol": symbol,
            "model": item["model"],
            "side": "sell",
            "intended_price": official_open,
            "official_close": "",
            "official_open": official_open,
            "quoted_bid": gate.get("bid"),
            "quoted_ask": gate.get("ask"),
            "spread_bps": gate.get("spread_bps"),
            "fill_price": fill_price,
            "slippage_vs_official_bps": slippage_vs_official_bps("sell", fill_price, official_open),
            "notional": item["notional"] if not error else 0.0,
            "news_flag": "clear" if news_item.get("news_clear") else "severe_or_missing",
            "quote_age_ms": gate.get("quote_age_ms"),
            "partial_fill": "false",
            "error": error,
            "trade_id": item["trade_id"],
            "session_date": item["session_date"],
            "leg": "exit",
            "timestamp_utc": dt.datetime.now(UTC).isoformat(),
            "gate_reason": ",".join(gate.get("reasons", [])),
            "order_id": "",
            "fill_source": "Alpaca IEX quote; adverse-side simulated paper fill",
        }
        rows.append(append_fill(ledger, row))
        if error:
            halted = True
        else:
            closed.append(item)
    # Never retry an errored exit automatically.  A human must reconcile it.
    pending_path.write_text(json.dumps([] if not halted else [item for item in pending if item not in closed], indent=2, sort_keys=True), encoding="utf-8")
    return {"rows": rows, "closed": closed, "halted": halted, "message": "exit complete" if not halted else "halted on exit gate/error"}


def local_rows() -> dict[str, list[dict]]:
    result = {}
    for path in sorted(DATA.glob("*.jsonl")):
        result[path.stem] = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return result


def alpaca_to_rows(symbol: str, rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        stamp = row.get("t")
        if not stamp:
            continue
        if isinstance(stamp, str):
            when = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        else:
            when = dt.datetime.fromtimestamp(float(stamp), UTC)
        day = when.astimezone(NY).date().isoformat()
        close = float(row["c"])
        out.append({"symbol": symbol, "date": day, "open": float(row["o"]), "high": float(row["h"]), "low": float(row["l"]), "close": close, "adj_open": float(row["o"]), "adj_high": float(row["h"]), "adj_low": float(row["l"]), "adj_close": close, "volume": float(row.get("v", 0.0)), "dollar_volume": close * float(row.get("v", 0.0))})
    return out


def merge_rows(old: list[dict], new: list[dict]) -> list[dict]:
    merged = {row["date"]: row for row in old}
    merged.update({row["date"]: row for row in new})
    return [merged[d] for d in sorted(merged)]


def classify_news(articles: list[dict]) -> dict:
    severe = []
    review = []
    for article in articles:
        text = " ".join(str(article.get(key) or "") for key in ("headline", "summary", "content")).lower()
        item = {"id": article.get("id"), "created_at": article.get("created_at"), "headline": article.get("headline"), "url": article.get("url")}
        if any(term in text for term in SEVERE_NEGATIVE):
            severe.append(item)
        elif any(term in text for term in REVIEW_TERMS):
            review.append(item)
    return {"article_count": len(articles), "severe_negative": severe, "review": review, "news_clear": not severe}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asof", help="New York session date to evaluate; default is latest Alpaca bar")
    parser.add_argument("--selection", choices=("all", "top30", "top50"), default="top30", help="model set; top30 is the frozen focused set")
    parser.add_argument("--allow-legacy-rank-selection", action="store_true", help="explicitly enable the pre-Phase-1 top-N selector; disabled by default")
    parser.add_argument("--submit-paper", metavar="SYMBOL", help="legacy explicit paper buy; requires --allow-order-submit and remains paper-only")
    parser.add_argument("--allow-order-submit", action="store_true", help="explicitly permit the legacy single-order paper endpoint")
    parser.add_argument("--notional", type=float, default=1000.0)
    parser.add_argument("--total-allocation", type=float, default=DEFAULT_MAX_TOTAL_ALLOCATION, help="paper notional cap; hard maximum is 50 percent")
    parser.add_argument("--max-model-allocation", type=float, default=DEFAULT_MAX_MODEL_ALLOCATION, help="hard per-model notional cap")
    parser.add_argument("--record-paper", action="store_true", help="record a quote-aware simulated paper entry/exit in paper/fills.csv")
    parser.add_argument("--paper-leg", choices=("entry", "exit"), default="entry", help="entry after close or exit at next open")
    parser.add_argument("--paper-equity", type=float, default=10_000.0)
    parser.add_argument("--paper-ledger", type=Path, default=PAPER_FILLS)
    parser.add_argument("--paper-pending", type=Path, default=PAPER_PENDING)
    parser.add_argument("--quote-fixture", type=Path, help="offline quote JSON fixture; otherwise use Alpaca latest quotes")
    parser.add_argument("--max-quote-age-ms", type=int, default=DEFAULT_MAX_QUOTE_AGE_MS)
    parser.add_argument("--max-spread-bps", type=float, default=DEFAULT_MAX_SPREAD_BPS)
    args = parser.parse_args()
    if args.selection == "top50" and not args.allow_legacy_rank_selection:
        raise SystemExit("No frozen top-50 contract exists. Use the frozen --selection top30 set or explicitly enable the legacy selector after preregistration.")
    if args.total_allocation > DEFAULT_MAX_TOTAL_ALLOCATION + 1e-9:
        raise SystemExit("The paper safety cap is 50% total notional.")
    if args.max_model_allocation > DEFAULT_MAX_MODEL_ALLOCATION + 1e-9:
        raise SystemExit("The paper safety cap is 25% per model.")
    if args.record_paper and args.submit_paper:
        raise SystemExit("Use either the quote-aware ledger or the legacy order preview, never both in one run.")
    specs, selection_meta = model_selection(args.selection)
    if args.selection == "top30" and not selection_meta.get("selection_frozen") and not args.allow_legacy_rank_selection:
        raise SystemExit("The Phase 2 top-30 run requires a frozen selection.json. Runtime rank selection is disabled.")
    local = local_rows()
    symbols = sorted(local)
    end = dt.date.fromisoformat(args.asof) + dt.timedelta(days=1) if args.asof else dt.datetime.now(NY).date() + dt.timedelta(days=1)
    start = end - dt.timedelta(days=420)
    bars = historical_bars(symbols, start.isoformat(), end.isoformat(), feed="iex")
    for symbol, rows in bars.items():
        local[symbol] = merge_rows(local.get(symbol, []), alpaca_to_rows(symbol, rows))
    features = feature_rows(local)
    benchmark = {r["date"]: r for r in local[BENCHMARK]}
    dates = sorted(set(benchmark) & set().union(*(set(v) for v in features.values())))
    asof = args.asof or dates[-1]
    if asof not in dates:
        raise SystemExit(f"No complete session for {asof}")
    # News is requested only for the frozen model picks, not every symbol.
    picks = []
    current_rows = [features[s][asof] for s in sorted(features) if asof in features[s]]
    for spec in specs:
        scored = score_candidates(current_rows, spec)
        if scored:
            pick = scored[0]
            picks.append({"model": spec.id, "name": spec.name, "symbol": pick["symbol"], "score": pick["score"], "top_candidates": [{"symbol": x["symbol"], "score": x["score"]} for x in scored[:5]]})
    news = {}
    news_start = (dt.date.fromisoformat(asof) - dt.timedelta(days=1)).isoformat() + "T00:00:00Z"
    news_end = (dt.date.fromisoformat(asof) + dt.timedelta(days=1)).isoformat() + "T00:00:00Z"
    for symbol in sorted({x["symbol"] for x in picks}):
        articles = historical_news([symbol], news_start, news_end, include_content=False)
        news[symbol] = classify_news(articles)
    for pick in picks:
        pick["news"] = news.get(pick["symbol"], {"news_clear": False, "article_count": 0, "severe_negative": [], "review": []})
        pick["paper_action"] = "review" if not pick["news"]["news_clear"] else "eligible_for_paper_review"
    allocation_per_model = min(args.max_model_allocation, args.total_allocation / len(specs)) if specs else 0.0
    for pick in picks:
        pick["allocation_total"] = args.total_allocation
        pick["allocation_per_model"] = allocation_per_model
    signal = {"created_utc": dt.datetime.now(UTC).isoformat(), "asof": asof, "selection": args.selection, "selection_meta": selection_meta, "allocation_total": args.total_allocation, "allocation_per_model": allocation_per_model, "data_source": "Alpaca IEX daily bars", "news_source": "Alpaca v1beta1/news (Benzinga-provided archive)", "models": picks, "orders_submitted": [], "no_live_orders": True, "signal_frozen_during_phase2": True, "paper_safety": {"max_total_allocation": args.total_allocation, "max_model_allocation": args.max_model_allocation, "max_quote_age_ms": args.max_quote_age_ms, "max_spread_bps": args.max_spread_bps, "leverage": 1.0, "severe_news_action": "block"}}
    if args.record_paper:
        if args.selection != "top30" or not selection_meta.get("selection_frozen"):
            raise SystemExit("Phase 2 paper measurement requires the frozen --selection top30 set; no runtime rank selection is allowed.")
        prereg = _paper_preregistration([spec.id for spec in specs])
        quotes = fetch_quotes(sorted({x["symbol"] for x in picks}), fixture=args.quote_fixture)
        current_rows_by_symbol = {symbol: row for symbol, row in ((s, features[s][asof]) for s in features if asof in features[s])}
        if args.paper_leg == "entry":
            paper_result = _record_entry(
                picks=picks,
                current_rows=current_rows_by_symbol,
                quotes=quotes,
                equity=args.paper_equity,
                asof=asof,
                ledger=args.paper_ledger,
                pending_path=args.paper_pending,
                max_total_allocation=args.total_allocation,
                max_model_allocation=args.max_model_allocation,
                max_quote_age_ms=args.max_quote_age_ms,
                max_spread_bps=args.max_spread_bps,
            )
        else:
            # Exit news is fetched for the pending symbols as well as today's
            # picks so a stale pending position cannot bypass the news gate.
            pending_symbols = []
            if args.paper_pending.exists():
                pending_symbols = [x["symbol"] for x in json.loads(args.paper_pending.read_text(encoding="utf-8"))]
            for symbol in sorted(set(pending_symbols) - set(news)):
                articles = historical_news([symbol], news_start, news_end, include_content=False)
                news[symbol] = classify_news(articles)
            paper_result = _record_exit(
                pending_path=args.paper_pending,
                current_rows=current_rows_by_symbol,
                quotes=quotes if not pending_symbols else fetch_quotes(sorted(set(pending_symbols)), fixture=args.quote_fixture),
                news=news,
                asof=asof,
                ledger=args.paper_ledger,
                max_quote_age_ms=args.max_quote_age_ms,
                max_spread_bps=args.max_spread_bps,
            )
        summary = measurement_summary(args.paper_ledger)
        write_measurement_state(PAPER_STATE, preregistration=prereg, summary=summary, status="paper_measurement_halted" if paper_result.get("halted") else "paper_measurement_active")
        signal["paper_recording"] = {"leg": args.paper_leg, "result": paper_result, "measurement": summary, "ledger": str(args.paper_ledger), "no_live_orders": True}
    if args.submit_paper:
        if not args.allow_order_submit:
            raise SystemExit("Legacy order submission is disabled by default. Add --allow-order-submit only for an explicitly reviewed Alpaca paper order.")
        if args.submit_paper not in {x["symbol"] for x in picks}:
            raise SystemExit("--submit-paper must be one of the current model picks")
        chosen = next(x for x in picks if x["symbol"] == args.submit_paper)
        if not chosen["news"]["news_clear"]:
            raise SystemExit("paper order blocked by severe news flag")
        if args.notional > args.paper_equity * min(args.max_model_allocation, args.total_allocation):
            raise SystemExit("legacy order exceeds the per-model paper safety cap")
        response = submit_paper_order(args.submit_paper, args.notional, dry_run=False)
        signal["orders_submitted"].append(response)
        signal["no_live_orders"] = True
    output_dir = FOCUSED_OUT if args.selection != "all" else OUT
    output_dir.mkdir(parents=True, exist_ok=True)
    output_name = "paper_signal_top30.json" if args.selection == "top30" else ("paper_signal_top50.json" if args.selection == "top50" else "paper_signal.json")
    (output_dir / output_name).write_text(json.dumps(signal, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(signal, indent=2))


if __name__ == "__main__":
    main()
