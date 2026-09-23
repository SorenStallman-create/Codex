"""Validate and record paper-only research-agent decisions.

This is the frozen decision interface and local integrity-evident event log.
It does not call a language model, change a strategy, touch the paper fill
ledger, or submit orders. Model/provider integration is a separate phase.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
import re
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DEFAULT_LEDGER = ROOT / "state" / "agent_shadow" / "events.jsonl"
SCHEMA_VERSION = 1
GENESIS_HASH = "0" * 64
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.]{0,9}$")

DECISION_FIELDS = {
    "decision_id",
    "run_id",
    "session_date",
    "decision_at_utc",
    "data_asof_utc",
    "input_snapshot_sha256",
    "data_sources",
    "agent",
    "action",
    "symbol",
    "thesis",
    "evidence",
    "confidence",
    "risk_flags",
    "invalidation_conditions",
    "no_trade_reason",
}
AGENT_FIELDS = {"model_id", "model_version", "prompt_sha256", "config_sha256"}
SOURCE_FIELDS = {"name", "observed_at_utc", "sha256"}
EVIDENCE_FIELDS = {"claim", "source_name"}
EVENT_FIELDS = {
    "schema_version",
    "event_type",
    "sequence",
    "previous_hash",
    "recorded_at_utc",
    "decision",
    "event_hash",
}


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha256(value: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _text(value: Any, field: str, *, max_length: int = 2_000, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    value = value.strip()
    if (not value and not allow_empty) or len(value) > max_length:
        raise ValueError(f"{field} must contain 1 to {max_length} characters")
    return value


def _timestamp(value: Any, field: str) -> tuple[dt.datetime, str]:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO 8601 timestamp with a timezone")
    text = value.strip()
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO 8601 timestamp with a timezone") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    utc = parsed.astimezone(dt.timezone.utc)
    return utc, utc.isoformat().replace("+00:00", "Z")


def _hash(value: Any, field: str) -> str:
    if not isinstance(value, str) or not HASH_RE.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase SHA-256 hex digest")
    return value


def _string_list(value: Any, field: str, *, max_items: int = 50) -> list[str]:
    if not isinstance(value, list) or len(value) > max_items:
        raise ValueError(f"{field} must be a list with at most {max_items} items")
    return [_text(item, f"{field} item", max_length=500) for item in value]


def validate_decision(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate an agent response and return its canonical, allowlisted shape."""
    if not isinstance(payload, dict):
        raise ValueError("decision payload must be a JSON object")
    unknown = set(payload) - DECISION_FIELDS
    if unknown:
        raise ValueError("unknown decision fields refused: " + ", ".join(sorted(unknown)))

    run_id = _text(payload.get("run_id"), "run_id", max_length=100)
    if not RUN_ID_RE.fullmatch(run_id):
        raise ValueError("run_id may contain only letters, numbers, dot, underscore, and hyphen")

    decision_id = payload.get("decision_id") or str(uuid.uuid4())
    try:
        decision_id = str(uuid.UUID(_text(decision_id, "decision_id", max_length=36)))
    except (ValueError, AttributeError) as exc:
        raise ValueError("decision_id must be a UUID") from exc

    session_date_text = _text(payload.get("session_date"), "session_date", max_length=10)
    try:
        session_date = dt.date.fromisoformat(session_date_text)
    except ValueError as exc:
        raise ValueError("session_date must use YYYY-MM-DD") from exc
    if session_date.isoformat() != session_date_text:
        raise ValueError("session_date must use YYYY-MM-DD")

    decision_at, decision_at_text = _timestamp(payload.get("decision_at_utc"), "decision_at_utc")
    data_asof, data_asof_text = _timestamp(payload.get("data_asof_utc"), "data_asof_utc")
    if data_asof > decision_at:
        raise ValueError("data_asof_utc cannot be later than decision_at_utc")

    sources_in = payload.get("data_sources")
    if not isinstance(sources_in, list) or not sources_in or len(sources_in) > 100:
        raise ValueError("data_sources must contain between 1 and 100 provenance records")
    sources: list[dict[str, str]] = []
    source_names: set[str] = set()
    for index, source in enumerate(sources_in):
        if not isinstance(source, dict) or set(source) != SOURCE_FIELDS:
            raise ValueError(f"data_sources[{index}] must contain name, observed_at_utc, and sha256")
        name = _text(source["name"], f"data_sources[{index}].name", max_length=120)
        if name in source_names:
            raise ValueError("data source names must be unique within a decision")
        source_names.add(name)
        observed, observed_text = _timestamp(source["observed_at_utc"], f"data_sources[{index}].observed_at_utc")
        if observed > data_asof:
            raise ValueError(f"data source {name} is later than data_asof_utc")
        sources.append({"name": name, "observed_at_utc": observed_text, "sha256": _hash(source["sha256"], f"data_sources[{index}].sha256")})

    agent_in = payload.get("agent")
    if not isinstance(agent_in, dict) or set(agent_in) != AGENT_FIELDS:
        raise ValueError("agent must contain model_id, model_version, prompt_sha256, and config_sha256")
    agent = {
        "model_id": _text(agent_in["model_id"], "agent.model_id", max_length=160),
        "model_version": _text(agent_in["model_version"], "agent.model_version", max_length=120),
        "prompt_sha256": _hash(agent_in["prompt_sha256"], "agent.prompt_sha256"),
        "config_sha256": _hash(agent_in["config_sha256"], "agent.config_sha256"),
    }

    action = payload.get("action")
    if not isinstance(action, str) or action not in {"candidate", "no_trade"}:
        raise ValueError("action must be candidate or no_trade")
    symbol_in = payload.get("symbol", "")
    if action == "candidate":
        symbol = _text(symbol_in, "symbol", max_length=10).upper()
        if not SYMBOL_RE.fullmatch(symbol):
            raise ValueError("symbol must be a ticker-style identifier")
    else:
        if symbol_in not in (None, "", "CASH"):
            raise ValueError("no_trade decisions cannot name a symbol")
        symbol = ""

    evidence_in = payload.get("evidence")
    if not isinstance(evidence_in, list) or len(evidence_in) > 100:
        raise ValueError("evidence must be a list with at most 100 items")
    evidence: list[dict[str, str]] = []
    for index, item in enumerate(evidence_in):
        if not isinstance(item, dict) or set(item) != EVIDENCE_FIELDS:
            raise ValueError(f"evidence[{index}] must contain claim and source_name")
        source_name = _text(item["source_name"], f"evidence[{index}].source_name", max_length=120)
        if source_name not in source_names:
            raise ValueError(f"evidence[{index}] references an unknown data source")
        evidence.append({"claim": _text(item["claim"], f"evidence[{index}].claim", max_length=1_000), "source_name": source_name})
    if action == "candidate" and not evidence:
        raise ValueError("candidate decisions require at least one sourced evidence item")

    confidence = payload.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError("confidence must be a finite number between 0 and 1")

    thesis = _text(payload.get("thesis", ""), "thesis", max_length=2_000, allow_empty=action == "no_trade")
    no_trade_reason = _text(payload.get("no_trade_reason", ""), "no_trade_reason", max_length=1_000, allow_empty=action == "candidate")
    if action == "candidate" and no_trade_reason:
        raise ValueError("candidate decisions cannot include no_trade_reason")

    invalidation = _string_list(payload.get("invalidation_conditions"), "invalidation_conditions")
    if action == "candidate" and not invalidation:
        raise ValueError("candidate decisions require at least one invalidation condition")

    return {
        "decision_id": decision_id,
        "run_id": run_id,
        "session_date": session_date.isoformat(),
        "decision_at_utc": decision_at_text,
        "data_asof_utc": data_asof_text,
        "input_snapshot_sha256": _hash(payload.get("input_snapshot_sha256"), "input_snapshot_sha256"),
        "data_sources": sources,
        "agent": agent,
        "action": action,
        "symbol": symbol,
        "thesis": thesis,
        "evidence": evidence,
        "confidence": float(confidence),
        "risk_flags": _string_list(payload.get("risk_flags"), "risk_flags"),
        "invalidation_conditions": invalidation,
        "no_trade_reason": no_trade_reason,
    }


def _read_verified(path: Path) -> tuple[int, str, set[tuple[str, str]], set[str]]:
    if not path.exists() or path.stat().st_size == 0:
        return 0, GENESIS_HASH, set(), set()
    previous_hash = GENESIS_HASH
    decisions: set[tuple[str, str]] = set()
    decision_ids: set[str] = set()
    run_agents: dict[str, dict[str, str]] = {}
    run_dates: dict[str, str] = {}
    count = 0
    with path.open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"invalid JSON at event line {line_number}") from exc
            if not isinstance(event, dict) or set(event) != EVENT_FIELDS:
                raise RuntimeError(f"invalid event shape at line {line_number}")
            if event.get("schema_version") != SCHEMA_VERSION or event.get("event_type") != "decision":
                raise RuntimeError(f"unsupported event at line {line_number}")
            if event.get("sequence") != count + 1 or event.get("previous_hash") != previous_hash:
                raise RuntimeError(f"broken event sequence or hash link at line {line_number}")
            _, recorded_at = _timestamp(event.get("recorded_at_utc"), f"line {line_number}.recorded_at_utc")
            if recorded_at != event["recorded_at_utc"]:
                raise RuntimeError(f"non-canonical recorded_at_utc at line {line_number}")
            try:
                decision = validate_decision(event.get("decision"))
            except (ValueError, TypeError) as exc:
                raise RuntimeError(f"invalid decision at line {line_number}: {exc}") from exc
            if decision != event["decision"]:
                raise RuntimeError(f"non-canonical decision at line {line_number}")
            recorded_dt, _ = _timestamp(recorded_at, "recorded_at_utc")
            decision_dt, _ = _timestamp(decision["decision_at_utc"], "decision_at_utc")
            if decision_dt > recorded_dt + dt.timedelta(minutes=5):
                raise RuntimeError(f"decision timestamp is after record time at line {line_number}")
            key = (decision["run_id"], decision["session_date"])
            if key in decisions or decision["decision_id"] in decision_ids:
                raise RuntimeError(f"duplicate decision at event line {line_number}")
            run_id = decision["run_id"]
            if run_id in run_agents and run_agents[run_id] != decision["agent"]:
                raise RuntimeError(f"agent model, prompt, or config changed within run {run_id}")
            if run_id in run_dates and decision["session_date"] <= run_dates[run_id]:
                raise RuntimeError(f"sessions are not strictly increasing within run {run_id}")
            run_agents[run_id] = decision["agent"]
            run_dates[run_id] = decision["session_date"]
            decisions.add(key)
            decision_ids.add(decision["decision_id"])
            unhashed = {key: value for key, value in event.items() if key != "event_hash"}
            expected_hash = _sha256(unhashed)
            if event.get("event_hash") != expected_hash:
                raise RuntimeError(f"event hash mismatch at line {line_number}")
            previous_hash = expected_hash
            count += 1
    return count, previous_hash, decisions, decision_ids


def record_decision(payload: dict[str, Any], ledger: Path = DEFAULT_LEDGER) -> dict[str, Any]:
    """Append one validated decision without rewriting prior events."""
    decision = validate_decision(payload)
    now = dt.datetime.now(dt.timezone.utc)
    decision_at, _ = _timestamp(decision["decision_at_utc"], "decision_at_utc")
    if decision_at > now + dt.timedelta(minutes=5):
        raise ValueError("decision_at_utc cannot be in the future")

    ledger.parent.mkdir(parents=True, exist_ok=True)
    lock_path = ledger.with_suffix(ledger.suffix + ".lock")
    with lock_path.open("a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        count, previous_hash, decisions, decision_ids = _read_verified(ledger)
        if (decision["run_id"], decision["session_date"]) in decisions:
            raise RuntimeError("a decision already exists for this run_id and session_date")
        if decision["decision_id"] in decision_ids:
            raise RuntimeError("decision_id already exists in the event log")
        event = {
            "schema_version": SCHEMA_VERSION,
            "event_type": "decision",
            "sequence": count + 1,
            "previous_hash": previous_hash,
            "recorded_at_utc": now.isoformat().replace("+00:00", "Z"),
            "decision": decision,
        }
        event["event_hash"] = _sha256(event)
        with ledger.open("a", encoding="utf-8") as output:
            output.write(json.dumps(event, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n")
            output.flush()
            os.fsync(output.fileno())
        fcntl.flock(lock, fcntl.LOCK_UN)
    return {"sequence": event["sequence"], "decision_id": decision["decision_id"], "event_hash": event["event_hash"], "ledger": str(ledger)}


def verify_ledger(ledger: Path = DEFAULT_LEDGER) -> dict[str, Any]:
    """Check sequence, duplicates, schema, and the complete event hash chain."""
    lock_path = ledger.with_suffix(ledger.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        count, last_hash, _, _ = _read_verified(ledger)
        fcntl.flock(lock, fcntl.LOCK_UN)
    return {"ok": True, "events": count, "last_event_hash": last_hash, "ledger": str(ledger)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate and integrity-check paper-only research-agent decision events")
    subparsers = parser.add_subparsers(dest="command", required=True)
    record_parser = subparsers.add_parser("record", help="validate and append one decision JSON object")
    record_parser.add_argument("--input", type=Path, required=True)
    record_parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    verify_parser = subparsers.add_parser("verify", help="verify the complete decision event log")
    verify_parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    args = parser.parse_args()

    if args.command == "record":
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        result = record_decision(payload, args.ledger)
    else:
        result = verify_ledger(args.ledger)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
