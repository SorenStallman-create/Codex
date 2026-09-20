"""Write the compact, error-bar-first research scoreboard."""
from __future__ import annotations

import math
from pathlib import Path


def _number(value, digits: int = 2) -> str:
    if value is None:
        return "—"
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(value):
        return "inf" if value > 0 else "—"
    return f"{value:.{digits}f}"


def write_scoreboard(report: dict, path: Path) -> None:
    models = {model["id"]: model["name"] for model in report.get("models", [])}
    rows = [row for row in report.get("summary", []) if row.get("model") in models]
    rows.sort(key=lambda row: (row.get("period", ""), row.get("model", "")))
    lines = [
        "# Trading Lab scoreboard",
        "",
        "Generated from the latest preregistered overnight-equity run. Sharpe is annualized from daily excess returns over 4% cash; confidence intervals use the Lo-style standard error. `INCONCLUSIVE` means the 95% Sharpe interval includes zero.",
        "",
        "- Return target: 0.5% per week compounded = 29.61% CAGR. The scoreboard does not treat weekly hit rate as a success metric.",
        f"- Preregistration: `{(report.get('preregistration') or {}).get('run_id', 'missing')}`",
        f"- Holdout registry: `{(report.get('holdout_registry') or {}).get('path', 'missing')}`",
        f"- Selection policy: {report.get('selection_policy', '—')}",
        "- Modeled round-trip slippage: 10.00 bps (5 bps per side); realized slippage is measured in the Phase 2 quote-aware paper ledger.",
        "",
        "| Period | Line | n_obs | Sharpe | 95% CI | Min detectable Sharpe | Return | Matched SPY diff | Status |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        ci = f"[{_number(row.get('sharpe_ci_low'))}, {_number(row.get('sharpe_ci_high'))}]"
        status = row.get("power_verdict") or ("INCONCLUSIVE" if float(row.get("sharpe_ci_low", 0)) <= 0 else "CI_ABOVE_ZERO")
        matched = row.get("excess_vs_spy_buy_hold_matched_pct")
        matched_text = _number(matched) + "%" if matched is not None else "—"
        lines.append(
            f"| {row.get('period', '—')} | {models.get(row.get('model'), row.get('model'))} | {row.get('n_obs', '—')} | {_number(row.get('sharpe'))} | {ci} | {_number(row.get('min_detectable_sharpe'))} | {_number(row.get('return_pct'))}% | {matched_text} | **{status}** |"
        )
    predictions = report.get("prediction_comparison", [])
    lines.extend([
        "",
        "## Pre-registered prediction vs realized Sharpe",
        "",
        "| Period | Line | Predicted range | Realized Sharpe | Inside range |",
        "|---|---|---:|---:|---|",
    ])
    for row in predictions:
        bounds = row.get("predicted_range") or []
        range_text = f"[{_number(bounds[0])}, {_number(bounds[1])}]" if len(bounds) == 2 else "—"
        lines.append(f"| {row.get('period', '—')} | {models.get(row.get('model'), row.get('model'))} | {range_text} | {_number(row.get('realized_value'))} | {'yes' if row.get('inside_predicted_range') else 'no'} |")
    lines.extend([
        "",
        "## Interpretation",
        "",
        "A positive return is not a pass by itself. The matched-SPY difference, Sharpe confidence interval, minimum detectable Sharpe, and drawdown/cost gates must be read together. The current historical rows are measurement evidence, not a live-capital authorization.",
        "",
        "The Phase 2 quote-aware recorder is active with a frozen signal. Its fixed decision rule requires 60 completed calendar sessions before any cost conclusion; open `paper/index.html` for the current count.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
