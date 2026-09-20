"""Registry that prevents silent re-use of scored holdout periods."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Iterable


TEST_PERIOD_NAMES = {"holdout", "recent"}


def universe_hash(universe: Iterable[str]) -> str:
    payload = json.dumps(sorted(set(universe)), separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()


def entry_key(universe: Iterable[str], period: str, start: str, end_exclusive: str) -> str:
    return f"{universe_hash(universe)}::{period}::{start}::{end_exclusive}"


def load(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "entries": [], "events": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, registry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, indent=2, sort_keys=True), encoding="utf-8")


def register(
    path: Path,
    *,
    universe: Iterable[str],
    periods: dict[str, dict],
    run_id: str,
    allow_reopen: bool = False,
    reason: str = "",
) -> dict:
    """Register all periods and refuse a repeated test period by default."""
    registry = load(path)
    registry.setdefault("version", 1)
    registry.setdefault("entries", [])
    registry.setdefault("events", [])
    existing = {entry.get("key"): entry for entry in registry["entries"]}
    messages: list[str] = []
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    uh = universe_hash(universe)
    for period, bounds in periods.items():
        start = bounds["start"] if isinstance(bounds, dict) else bounds[0]
        end = bounds["end_exclusive"] if isinstance(bounds, dict) else bounds[1]
        key = entry_key(universe, period, start, end)
        if key in existing and period in TEST_PERIOD_NAMES:
            message = f"HOLDOUT REGISTRY WARNING: {period} {start}..{end} has already been scored for this universe."
            messages.append(message)
            if not allow_reopen:
                raise RuntimeError(message + " Re-run is blocked; use --reopen-holdout only for an explicit audit migration.")
            registry["events"].append({"time_utc": now, "event": "reopened", "key": key, "run_id": run_id, "reason": reason or "explicit reopen"})
        if key not in existing:
            entry = {"key": key, "universe_hash": uh, "period": period, "start": start, "end_exclusive": end, "first_run_id": run_id, "first_scored_utc": now}
            registry["entries"].append(entry)
            existing[key] = entry
    registry["last_checked_utc"] = now
    registry["last_run_id"] = run_id
    save(path, registry)
    return {"warnings": messages, "registry": registry}


def bootstrap_from_results(path: Path, results_path: Path, universe: Iterable[str]) -> dict:
    """Backfill the registry once from the pre-Phase-1 report."""
    if path.exists():
        return load(path)
    report = json.loads(results_path.read_text(encoding="utf-8"))
    periods = report.get("periods", {})
    registry = {"version": 1, "entries": [], "events": [], "bootstrapped_from": str(results_path)}
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    uh = universe_hash(universe)
    for period, bounds in periods.items():
        start = bounds["start"]
        end = bounds["end_exclusive"]
        registry["entries"].append({"key": entry_key(universe, period, start, end), "universe_hash": uh, "period": period, "start": start, "end_exclusive": end, "first_run_id": "pre_phase1_existing_report", "first_scored_utc": now})
    save(path, registry)
    return registry
