"""Independent deterministic checker for the RohOnChain-inspired study.

The checker is deliberately separate from the strategy runner.  It does not
generate trades, call a language model, connect Kimi, or place orders.  It
turns predeclared evidence gates into an auditable verdict.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
V2 = ROOT / "research" / "intraday_v2"
PAIR = ROOT / "research" / "roh_guide_v1"


def check_pair():
    report = json.loads((PAIR / "results.json").read_text())
    lead = report["summary"][report["freeze"]["lead"]]["evaluation"]
    return {
        "study": "Roh guide statistical arbitrage / SPY-QQQ",
        "lead": report["freeze"]["lead"],
        "mean_pnl_per_evaluation_day": lead["mean_pnl"],
        "total_pnl": lead["total_pnl"],
        "trades": lead["trades"],
        "max_drawdown_pct": lead["max_drawdown_pct"],
        "qualified": False,
        "reasons": [
            "Only 11 evaluation sessions are available.",
            "The source dates were already visible in the earlier lab.",
            "Short borrow, borrow fees, margin, quotes and intrabar fills are not modeled.",
            "The lead loses money after the declared fee and slippage allowance.",
        ],
    }


def check_v2():
    report = json.loads((V2 / "results.json").read_text())
    out = []
    for market, models in report["markets"].items():
        for model, result in models.items():
            out.append({"market": market, "model": model,
                        "total_pnl": result["total_pnl"],
                        "trades": result["trades"],
                        "qualified": bool(result["qualified"]),
                        "gates": result["gates"]})
    return out


def main():
    verdict = {
        "created_utc": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "llm_or_kimi_used": False,
        "real_orders": False,
        "pair": check_pair(),
        "earlier_intraday": check_v2(),
        "deployment_verdict": "RESEARCH_ONLY_KEEP_CASH",
        "next_gate": "Acquire at least 120 genuinely reserved sessions, then run at least 60 new paper sessions and 100 fills with quote-aware reconciliation.",
    }
    (PAIR / "checker_verdict.json").write_text(json.dumps(verdict, indent=2))
    print(json.dumps(verdict, indent=2))


if __name__ == "__main__":
    main()
