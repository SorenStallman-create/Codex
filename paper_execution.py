"""Quote-aware, paper-only execution controls for the overnight experiment.

The historical signal is intentionally kept outside this module.  This file
implements the execution layer that can be measured prospectively: quote
quality gates, conservative bid/ask fills, exposure limits, an append-only
fill ledger, reconciliation, and the fixed 60-session slippage decision rule.

No function in this module sends an order to a broker.
"""
from __future__ import annotations

import csv
import datetime as dt
import fcntl
import json
import math
import os
import statistics
from pathlib import Path
from typing import Iterable


REQUIRED_FIELDS = (
    "date",
    "symbol",
    "model",
    "side",
    "intended_price",
    "official_close",
    "official_open",
    "quoted_bid",
    "quoted_ask",
    "spread_bps",
    "fill_price",
    "slippage_vs_official_bps",
    "notional",
    "news_flag",
    "quote_age_ms",
    "partial_fill",
    "error",
)
EXTRA_FIELDS = (
    "trade_id",
    "session_date",
    "leg",
    "timestamp_utc",
    "gate_reason",
    "order_id",
    "fill_source",
)
LEDGER_FIELDS = REQUIRED_FIELDS + EXTRA_FIELDS

DEFAULT_MAX_TOTAL_ALLOCATION = 0.50
DEFAULT_MAX_MODEL_ALLOCATION = 0.25
DEFAULT_MAX_QUOTE_AGE_MS = 5_000
DEFAULT_MAX_SPREAD_BPS = 12.0
MEASUREMENT_SESSIONS = 60
CONTINUE_SESSIONS = 250


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def _as_float(value: object, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _quote_timestamp(value: object) -> dt.datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    if isinstance(value, (int, float)):
        # Alpaca timestamps are normally RFC3339 strings.  Accept seconds,
        # milliseconds, microseconds, and nanoseconds for fixture portability.
        numeric = float(value)
        while numeric > 10_000_000_000:
            numeric /= 1_000.0
        return dt.datetime.fromtimestamp(numeric, dt.timezone.utc)
    text = str(value).replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def normalise_quote(symbol: str, raw: dict) -> dict:
    """Return a small stable quote shape from an Alpaca/fixture quote."""
    bid = _as_float(raw.get("bid") if "bid" in raw else raw.get("bp"))
    ask = _as_float(raw.get("ask") if "ask" in raw else raw.get("ap"))
    stamp = raw.get("timestamp", raw.get("t"))
    return {
        "symbol": symbol,
        "bid": bid,
        "ask": ask,
        "bid_size": _as_float(raw.get("bid_size", raw.get("bs")), 0.0),
        "ask_size": _as_float(raw.get("ask_size", raw.get("as")), 0.0),
        "timestamp": stamp,
    }


def quote_gate(
    quote: dict,
    *,
    now: dt.datetime | None = None,
    max_age_ms: int = DEFAULT_MAX_QUOTE_AGE_MS,
    max_spread_bps: float = DEFAULT_MAX_SPREAD_BPS,
) -> dict:
    """Check a quote before an order is even considered.

    A missing quote, crossed/invalid market, stale timestamp, or wide spread
    blocks the leg.  A fixture can supply ``quote_age_ms`` directly when a
    timestamp is not available; real Alpaca quotes must include a timestamp.
    """
    now = now or utc_now()
    bid = _as_float(quote.get("bid", quote.get("bp")))
    ask = _as_float(quote.get("ask", quote.get("ap")))
    reasons: list[str] = []
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        reasons.append("missing_or_nonpositive_quote")
    elif ask < bid:
        reasons.append("crossed_quote")
    mid = ((bid + ask) / 2.0) if bid is not None and ask is not None and bid > 0 and ask > 0 else None
    spread_bps = ((ask - bid) / mid * 10_000.0) if mid and ask is not None and bid is not None else None
    if spread_bps is not None and spread_bps > max_spread_bps:
        reasons.append("spread_above_limit")

    if quote.get("quote_age_ms") not in (None, ""):
        age = _as_float(quote.get("quote_age_ms"))
    else:
        stamp = _quote_timestamp(quote.get("timestamp", quote.get("t")))
        if stamp is None:
            age = None
        else:
            age = (now - stamp).total_seconds() * 1_000.0
    if age is None:
        reasons.append("missing_quote_timestamp")
    elif age < -1_000:
        reasons.append("quote_timestamp_in_future")
    elif age > max_age_ms:
        reasons.append("quote_stale")

    return {
        "eligible": not reasons,
        "reasons": reasons,
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "spread_bps": spread_bps,
        "quote_age_ms": age,
    }


def conservative_fill_price(side: str, gate: dict) -> float | None:
    """Use the adverse side of the quote for a paper fill."""
    if side == "buy":
        return _as_float(gate.get("ask"))
    if side == "sell":
        return _as_float(gate.get("bid"))
    raise ValueError("side must be buy or sell")


def slippage_vs_official_bps(side: str, fill_price: float | None, official_price: float | None) -> float | None:
    """Return adverse slippage as a positive number of basis points."""
    fill = _as_float(fill_price)
    official = _as_float(official_price)
    if fill is None or official is None or fill <= 0 or official <= 0:
        return None
    if side == "buy":
        return (fill / official - 1.0) * 10_000.0
    if side == "sell":
        return (official / fill - 1.0) * 10_000.0
    raise ValueError("side must be buy or sell")


def allocation_plan(
    picks: Iterable[dict],
    equity: float,
    *,
    max_total_allocation: float = DEFAULT_MAX_TOTAL_ALLOCATION,
    max_model_allocation: float = DEFAULT_MAX_MODEL_ALLOCATION,
) -> list[dict]:
    """Allocate equally across the frozen model lines under hard caps.

    Equal allocation means an ``all`` run cannot silently discard models to
    fit the total cap.  The focused two-model set therefore receives 25% per
    model and 50% total, while a wider unselected diagnostic set receives a
    smaller equal sleeve.
    """
    if equity <= 0 or not math.isfinite(equity):
        raise ValueError("equity must be positive and finite")
    if not 0 < max_total_allocation <= 1 or not 0 < max_model_allocation <= 1:
        raise ValueError("allocation limits must be in (0, 1]")
    unique: list[dict] = []
    seen: set[str] = set()
    for pick in picks:
        model = str(pick.get("model") or "")
        if not model or model in seen:
            raise ValueError("each allocation pick needs a unique model")
        seen.add(model)
        unique.append(pick)
    if not unique:
        return []
    sleeve_fraction = min(max_model_allocation, max_total_allocation / len(unique))
    return [
        {
            **pick,
            "allocation_fraction": sleeve_fraction,
            "notional": round(equity * sleeve_fraction, 2),
        }
        for pick in unique
    ]


def validate_allocation(rows: Iterable[dict], equity: float, *, max_total_allocation: float = DEFAULT_MAX_TOTAL_ALLOCATION, max_model_allocation: float = DEFAULT_MAX_MODEL_ALLOCATION) -> dict:
    """Reconcile planned notional against both exposure caps."""
    rows = list(rows)
    by_model: dict[str, float] = {}
    total = 0.0
    for row in rows:
        notional = _as_float(row.get("notional"), 0.0) or 0.0
        model = str(row.get("model") or "")
        total += notional
        by_model[model] = by_model.get(model, 0.0) + notional
    total_fraction = total / equity if equity else math.inf
    model_fractions = {model: amount / equity for model, amount in by_model.items()} if equity else {model: math.inf for model in by_model}
    errors = []
    if total_fraction > max_total_allocation + 1e-9:
        errors.append("total_notional_above_limit")
    errors.extend(f"model_notional_above_limit:{model}" for model, fraction in model_fractions.items() if fraction > max_model_allocation + 1e-9)
    return {
        "ok": not errors,
        "errors": errors,
        "total_notional": total,
        "total_fraction": total_fraction,
        "model_fractions": model_fractions,
    }


def _normalise_record(record: dict) -> dict:
    row = {field: record.get(field, "") for field in LEDGER_FIELDS}
    # Keep CSV values deterministic and readable.
    for field in ("intended_price", "official_close", "official_open", "quoted_bid", "quoted_ask", "spread_bps", "fill_price", "slippage_vs_official_bps", "notional", "quote_age_ms"):
        value = row[field]
        if value not in (None, ""):
            number = _as_float(value)
            row[field] = "" if number is None else f"{number:.8f}" if field in {"intended_price", "official_close", "official_open", "quoted_bid", "quoted_ask", "fill_price"} else f"{number:.4f}"
    row["date"] = str(row["date"])
    row["symbol"] = str(row["symbol"]).upper()
    row["model"] = str(row["model"])
    row["side"] = str(row["side"]).lower()
    row["partial_fill"] = str(row["partial_fill"]).lower()
    row["news_flag"] = str(row["news_flag"])
    row["error"] = str(row["error"])
    return row


def validate_record(record: dict) -> None:
    row = _normalise_record(record)
    if not row["date"] or not row["symbol"] or not row["model"]:
        raise ValueError("fill record requires date, symbol, and model")
    if row["side"] not in {"buy", "sell"}:
        raise ValueError("fill record side must be buy or sell")
    notional = _as_float(row["notional"])
    if notional is None or notional < 0:
        raise ValueError("fill record notional must be nonnegative")
    fill = _as_float(row["fill_price"])
    if not row["error"] and (fill is None or fill <= 0):
        raise ValueError("a completed fill needs a positive fill_price or an error")
    if row["partial_fill"] not in {"true", "false", "", "0", "1"}:
        raise ValueError("partial_fill must be a boolean-like value")


def append_fill(path: Path, record: dict) -> dict:
    """Append one ledger row with a process lock and immutable header."""
    row = _normalise_record(record)
    validate_record(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        exists = path.exists() and path.stat().st_size > 0
        if exists:
            with path.open("r", newline="", encoding="utf-8") as existing:
                header = next(csv.reader(existing), [])
            if header != list(LEDGER_FIELDS):
                raise RuntimeError("fill ledger header changed; refusing to append")
            if row.get("trade_id") and row.get("leg"):
                existing_rows = read_fills(path)
                if any(item.get("trade_id") == row["trade_id"] and item.get("leg") == row["leg"] for item in existing_rows):
                    raise RuntimeError(f"duplicate fill leg refused: {row['trade_id']}::{row['leg']}")
        with path.open("a", newline="", encoding="utf-8") as out:
            writer = csv.DictWriter(out, fieldnames=LEDGER_FIELDS, extrasaction="ignore")
            if not exists:
                writer.writeheader()
            writer.writerow(row)
            out.flush()
            os.fsync(out.fileno())
        fcntl.flock(lock, fcntl.LOCK_UN)
    return row


def read_fills(path: Path) -> list[dict]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    return rows


def reconcile_fills(rows: Iterable[dict]) -> dict:
    """Find duplicate legs, incomplete pairs, and ledger-level errors."""
    rows = list(rows)
    errors: list[str] = []
    groups: dict[str, list[dict]] = {}
    for row in rows:
        trade_id = str(row.get("trade_id") or f"{row.get('session_date') or row.get('date')}::{row.get('model')}::{row.get('symbol')}")
        groups.setdefault(trade_id, []).append(row)
    completed = []
    for trade_id, legs in groups.items():
        sides = [str(row.get("side", "")).lower() for row in legs]
        if len(legs) > 2 or sides.count("buy") > 1 or sides.count("sell") > 1:
            errors.append(f"duplicate_legs:{trade_id}")
        if any(str(row.get("partial_fill", "")).lower() in {"true", "1", "yes"} for row in legs):
            errors.append(f"partial_fill_requires_reconciliation:{trade_id}")
        if len(legs) == 2 and set(sides) == {"buy", "sell"} and not any(row.get("error") for row in legs):
            values = [_as_float(row.get("slippage_vs_official_bps")) for row in legs]
            if all(value is not None for value in values):
                completed.append({"trade_id": trade_id, "session_date": legs[0].get("session_date") or legs[0].get("date"), "model": legs[0].get("model"), "symbol": legs[0].get("symbol"), "round_trip_bps": sum(values)})
        elif len(legs) == 2 and set(sides) == {"buy", "sell"}:
            errors.append(f"errored_pair:{trade_id}")
    return {"ok": not errors, "errors": errors, "rows": len(rows), "groups": len(groups), "completed_trades": completed}


def measurement_summary(path: Path, *, minimum_sessions: int = MEASUREMENT_SESSIONS) -> dict:
    """Apply the fixed Phase 2 rule to the quote-aware ledger."""
    rows = read_fills(path)
    reconciliation = reconcile_fills(rows)
    completed = reconciliation["completed_trades"]
    sessions = {str(item.get("session_date")) for item in completed if item.get("session_date")}
    values = [float(item["round_trip_bps"]) for item in completed]
    median = statistics.median(values) if values else None
    p75 = statistics.quantiles(values, n=4, method="inclusive")[2] if len(values) >= 2 else (values[0] if values else None)
    if len(sessions) < minimum_sessions:
        decision = "WAITING_FOR_60_COMPLETED_SESSIONS"
    elif median is None:
        decision = "STOP_DATA_ERROR_NO_MEASURABLE_SLIPPAGE"
    elif median < 8.0:
        decision = "CONTINUE_TO_250_SESSIONS"
    elif median <= 12.0:
        decision = "MARGINAL_REQUIRE_GROSS_EDGE_AND_CONFIDENCE_INTERVAL"
    else:
        decision = "STOP_ARCHIVE_OVER_NIGHT_LINE"
    return {
        "ledger": str(path),
        "rows": len(rows),
        "completed_trades": len(completed),
        "completed_sessions": len(sessions),
        "minimum_sessions": minimum_sessions,
        "round_trip_slippage_bps": values,
        "median_round_trip_bps": median,
        "p75_round_trip_bps": p75,
        "decision": decision,
        "reconciliation": reconciliation,
    }


def paper_forward_spec(model_ids: Iterable[str]) -> dict:
    """The immutable Phase 2 measurement contract used by the runner."""
    return {
        "strategy": "overnight_equities_paper_forward",
        "hypothesis": "The frozen close-to-next-open signal may retain a measurable gross edge after quote-aware paper fills and execution gates.",
        "source": "Trading Lab Phase 2 standing brief; execution measurement rather than a new signal hypothesis.",
        "predicted_effect": {"metric": "round_trip_slippage_bps", "value": 10.0, "range": [4.0, 12.0]},
        "universe": ["SPY", "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "AVGO", "TSLA", "JPM", "WMT", "LLY", "V", "ORCL", "MA", "XOM", "COST", "NFLX", "JNJ", "HD", "PG", "BAC", "ABBV", "KO", "CRM", "CVX", "MRK", "AMD", "PEP", "TMO", "MCD", "LIN", "DIS", "CSCO", "IBM", "GE", "CAT", "AMAT", "QCOM", "TXN", "INTU", "NOW", "UBER", "PLTR", "GS", "ISRG", "BKNG", "IWM"],
        "periods": {"paper_measurement": {"sessions": MEASUREMENT_SESSIONS, "follow_on_sessions": CONTINUE_SESSIONS}},
        "parameters": {
            "models": sorted(set(model_ids)),
            "entry": "buy at quoted ask after the regular-session close decision",
            "exit": "sell at quoted bid at the next regular-session open",
            "feed": "Alpaca IEX",
            "max_total_allocation": DEFAULT_MAX_TOTAL_ALLOCATION,
            "max_model_allocation": DEFAULT_MAX_MODEL_ALLOCATION,
            "max_quote_age_ms": DEFAULT_MAX_QUOTE_AGE_MS,
            "max_spread_bps": DEFAULT_MAX_SPREAD_BPS,
            "leverage": 1.0,
            "severe_news_action": "block",
            "fill_cost_definition": "adverse-side quote fill versus official close/open",
        },
        "gates": [
            "median round-trip slippage below 8 bps continues to 250 sessions",
            "median round-trip slippage from 8 to 12 bps requires gross edge above measured cost with confidence interval",
            "median round-trip slippage above 12 bps archives the line",
            "halt on data, quote, order, or reconciliation error",
        ],
        "kill_condition": "Archive the overnight line if measured median round-trip slippage exceeds 12 bps, or if the frozen signal cannot be reconciled without manual intervention.",
    }


def write_measurement_state(path: Path, *, preregistration: dict, summary: dict, status: str = "paper_measurement_active") -> None:
    payload = {
        "updated_utc": iso_now(),
        "status": status,
        "no_live_orders": True,
        "signal_frozen": True,
        "preregistration": {"run_id": preregistration.get("run_id"), "spec_hash": preregistration.get("spec_hash")},
        "summary": summary,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)
