"""Create and validate pre-registration files for Trading Lab backtests."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PREREG_DIR = ROOT / "research" / "overnight_equities" / "prereg"


def _canonical(spec: dict) -> bytes:
    body = {key: value for key, value in spec.items() if key not in {"run_id", "created_utc", "spec_hash"}}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def spec_hash(spec: dict) -> str:
    return hashlib.sha256(_canonical(spec)).hexdigest()


def overnight_spec() -> dict:
    # Import lazily so overnight_equities can import this module for validation.
    import overnight_equities as oe

    return {
        "strategy": "overnight_equities",
        "hypothesis": "A fixed liquid U.S. equity close-to-next-open ranking can produce a positive risk-adjusted return after modeled costs.",
        "source": "Overnight-return literature summarized in STRATEGY_RESEARCH.md plus an explicitly labeled research hypothesis.",
        "predicted_effect": {"metric": "sharpe", "value": 0.15, "range": [0.05, 0.30]},
        "universe": [oe.BENCHMARK, *oe.UNIVERSE],
        "periods": {name: {"start": bounds[0].isoformat(), "end_exclusive": bounds[1].isoformat()} for name, bounds in oe.PERIODS.items()},
        "parameters": {
            "fee_each_side": oe.FEE,
            "slippage_each_side": oe.SLIP,
            "allocation": oe.ALLOCATION,
            "cash_rate": oe.CASH_RATE,
            "borrow_spread": oe.BORROW_SPREAD,
            "min_price": oe.MIN_PRICE,
            "min_dollar_volume": oe.MIN_DOLLAR_VOLUME,
            "models": [model.id for model in oe.MODELS],
            "entry": "regular-session close",
            "exit": "next regular-session open",
        },
        "gates": [
            "positive holdout return",
            "maximum holdout drawdown at most 15 percent",
            "positive 2x cost holdout return",
            "bootstrap lower return bound above zero",
            "Sharpe confidence interval lower bound above zero",
        ],
        "kill_condition": "Abandon the line if the holdout fails the preregistered gates or if the paper slippage rule kills the measured edge.",
    }


def create(run_id: str | None = None, directory: Path = PREREG_DIR, overrides: dict | None = None) -> dict:
    spec = overnight_spec()
    if overrides:
        spec.update(overrides)
    run_id = run_id or dt.datetime.now(dt.timezone.utc).strftime("overnight_%Y%m%dT%H%M%SZ")
    record = {"run_id": run_id, "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(), **spec}
    record["spec_hash"] = spec_hash(record)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{run_id}.json"
    if path.exists():
        raise FileExistsError(path)
    path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    return record


def create_from_spec(spec: dict, run_id: str, directory: Path = PREREG_DIR) -> dict:
    """Write a preregistration for a non-historical measurement phase."""
    record = {"run_id": run_id, "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(), **spec}
    record["spec_hash"] = spec_hash(record)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{run_id}.json"
    if path.exists():
        raise FileExistsError(path)
    path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    return record


def create_paper(run_id: str | None = None, directory: Path = PREREG_DIR) -> dict:
    """Create the fixed Phase 2 quote-cost measurement contract."""
    from paper_execution import paper_forward_spec

    run_id = run_id or dt.datetime.now(dt.timezone.utc).strftime("paper_forward_%Y%m%dT%H%M%SZ")
    return create_from_spec(paper_forward_spec(["overnight_momentum", "overnight_reversal"]), run_id, directory)


def require_matching(expected: dict, directory: Path = PREREG_DIR) -> dict:
    expected_hash = spec_hash(expected)
    if not directory.exists():
        raise RuntimeError(f"No preregistration directory exists at {directory}. Run preregister.py create first.")
    matches: list[dict] = []
    for path in sorted(directory.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if record.get("spec_hash") == expected_hash and spec_hash(record) == expected_hash:
            matches.append(record)
    if not matches:
        raise RuntimeError("No matching preregistration file. Backtest refused; create one with preregister.py create before running.")
    return matches[-1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or inspect an overnight backtest preregistration")
    sub = parser.add_subparsers(dest="command", required=True)
    create_parser = sub.add_parser("create")
    create_parser.add_argument("--run-id")
    paper_parser = sub.add_parser("create-paper", help="create the fixed Phase 2 quote-cost preregistration")
    paper_parser.add_argument("--run-id")
    list_parser = sub.add_parser("list")
    list_parser.add_argument("--directory", type=Path, default=PREREG_DIR)
    args = parser.parse_args()
    if args.command == "create":
        record = create(args.run_id)
        print(json.dumps({"run_id": record["run_id"], "spec_hash": record["spec_hash"]}, indent=2))
    elif args.command == "create-paper":
        record = create_paper(args.run_id)
        print(json.dumps({"run_id": record["run_id"], "spec_hash": record["spec_hash"]}, indent=2))
    else:
        print(json.dumps([json.loads(path.read_text(encoding="utf-8")) for path in sorted(args.directory.glob("*.json"))], indent=2))


if __name__ == "__main__":
    main()
