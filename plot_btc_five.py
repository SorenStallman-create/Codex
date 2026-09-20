"""Create the shareable BTC five-bot comparison figure."""
from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.dates as mdates


ROOT = Path(__file__).resolve().parent


def rows(path):
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main():
    live = rows(ROOT / "research/btc_live_five/daily_results.csv")
    day = rows(ROOT / "research/btc_five_bots/daily_results.csv")
    fig, axes = plt.subplots(2, 1, figsize=(13, 8), constrained_layout=True)
    for axis, source, title, label in ((axes[0], live, "BTC 24/7 live-style screen · 2024 holdout", "Live-style"), (axes[1], day, "BTC 15-minute UTC-day screen · 2024 holdout", "Day-trade")):
        source = [x for x in source if x["period"] == "holdout"]
        bots = sorted({x["bot"] for x in source})
        for bot in bots:
            xs = [x for x in source if x["bot"] == bot]
            key = "equity" if "equity" in xs[0] else "ending_equity"
            dates = [dt.datetime.fromisoformat(x["date"]).replace(tzinfo=dt.timezone.utc) for x in xs]
            axis.plot(dates, [float(x[key]) for x in xs], label=bot, linewidth=1.7)
        axis.axhline(10000, color="#8994a5", linewidth=0.8, linestyle="--")
        axis.set_title(title, loc="left", fontweight="bold")
        axis.set_ylabel("Equity ($)")
        axis.grid(alpha=0.18)
        axis.tick_params(axis="x", rotation=30, labelsize=8)
        axis.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
        axis.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        axis.legend(ncol=3, fontsize=8, frameon=False)
    fig.suptitle("BTC five-bot research — costs and uncertainty remain decisive", fontsize=16, fontweight="bold")
    fig.text(0.01, 0.01, "$10,000 paper account · public Binance data · no live orders · holdout curves after modeled costs", fontsize=9, color="#536273")
    out = ROOT / "research/btc_live_five/btc_five_comparison.png"
    svg = ROOT / "research/btc_live_five/btc_five_comparison.svg"
    fig.savefig(out, dpi=180)
    fig.savefig(svg)
    print(out)
    print(svg)


if __name__ == "__main__":
    main()
