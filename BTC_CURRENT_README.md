# Current BTC experiment handoff

This package is the current testing phase as of 2026-09-18. It is deliberately
BTC-only and contains two screens using fixed rules and frozen parameters:

1. **24/7 live-style screen** (`research/btc_live_five`): complete 4-hour
   signals, hourly next-bar paper fills, long/cash exposure, risk sizing,
   stops, and drawdown guards.
2. **15-minute day-trade screen** (`research/btc_five_bots`): one 1x position
   per UTC day, next-bar fills, forced daily flattening, fees, slippage, ATR
   stops, and funding stress.

## Open the visual

Open [research/btc_live_five/index.html](research/btc_live_five/index.html).
It contains the interactive comparison, holdout equity curve, bot catalog,
chronological results, execution stress table, and links to the local memo and
15-minute screen.

The static chart is
[research/btc_live_five/btc_five_comparison.png](research/btc_live_five/btc_five_comparison.png).

## What is included

- Current scripts: `btc_five_data.py`, `btc_five_research.py`,
  `btc_live_five.py`, `build_btc_five_report.py`, and `plot_btc_five.py`.
- These scripts are the reference code for the current outputs. The HTML
  report is self-contained; the live-style runner also depends on the lab's
  internal engine modules, which are intentionally not bundled into this
  current-data handoff.
- Current raw 15-minute BTC USD-M futures candles:
  `research/btc_five_bots/data/BTCUSDT_15m.jsonl`.
- Current raw 1-hour BTC spot candles used by the live-style screen:
  `research/btc_live_five/data/BTCUSDT_1h.jsonl`.
- Current decisions, fills, daily results, equity curves, stress runs,
  frozen configurations, model catalogs, and result manifests for both screens.
- `tests/test_btc_five.py`, the focused tests for the current BTC work.

## Current evidence

The 2024 holdout leaders in the 24/7 screen are Breakout Guard 20 (+4.99%)
and Trend Guard 20 (+4.54%), after the modeled baseline costs. Their bootstrap
lower bounds remain negative, so they are paper candidates only. All five
strict 15-minute day-trading candidates fail the promotion gates after modeled
costs. No model is promoted to live trading.

The folder is historical research. It has no exchange credentials, no live
order endpoint, and no Kimi integration.
