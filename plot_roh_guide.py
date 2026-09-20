"""Create a static figure for the guide-specific evaluation."""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "research" / "roh_guide_v1"


def main():
    report = json.loads((OUT / "results.json").read_text())
    models = sorted(report["summary"])
    days = report["freeze"]["splits"]["evaluation"]
    colors = ["#238f99", "#d08335", "#7566bc"]
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11,
                         "axes.spines.top": False, "axes.spines.right": False})
    fig, axes = plt.subplots(2, 1, figsize=(13, 8), layout="constrained")
    fig.suptitle("Guide study — market-neutral pairs did not qualify", fontsize=21, fontweight="bold")
    x = np.arange(len(days))
    width = 0.22
    for j, model in enumerate(models):
        rows = [r for r in report["daily"] if r["phase"] == "evaluation" and r["model"] == model]
        axes[0].bar(x + (j - 1) * width, [r["net_pnl"] for r in rows], width,
                    label=model, color=colors[j])
    axes[0].axhline(0, color="#68778a", linewidth=.8)
    axes[0].set_xticks(x, [d[5:] for d in days])
    axes[0].set_ylabel("Net P&L ($ / $10,000 day)")
    axes[0].set_title("Daily evaluation P&L", loc="left", fontweight="bold")
    axes[0].legend(ncol=3, fontsize=9)
    axes[0].grid(axis="y", alpha=.18)
    axes[0].set_axisbelow(True)
    means = [report["summary"][m]["evaluation"]["mean_pnl"] for m in models]
    totals = [report["summary"][m]["evaluation"]["total_pnl"] for m in models]
    axes[1].bar(np.arange(len(models)) - .17, means, .32, label="Mean/day", color="#344f70")
    axes[1].bar(np.arange(len(models)) + .17, totals, .32, label="Total", color="#9787d8")
    axes[1].axhline(0, color="#68778a", linewidth=.8)
    axes[1].set_xticks(np.arange(len(models)), models)
    axes[1].set_ylabel("Dollars")
    axes[1].set_title("Every tested threshold is negative on average or too sparse", loc="left", fontweight="bold")
    axes[1].legend(fontsize=9)
    axes[1].grid(axis="y", alpha=.18)
    axes[1].set_axisbelow(True)
    fig.supxlabel("Five-minute public data · 11 evaluation sessions · modeled fees and adverse slippage included · no live orders", fontsize=10)
    fig.savefig(OUT / "guide_comparison.png", dpi=170)
    fig.savefig(OUT / "guide_comparison.svg")
    print(OUT / "guide_comparison.png")


if __name__ == "__main__":
    main()
