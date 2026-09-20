# Focused top-30% overnight replay

This folder contains the current focused experiment. It keeps only the two
models selected from the five-model overnight study:

1. **Overnight momentum**
2. **Intraday reversal into the close**

The cut is `ceil(5 × 30%) = 2`, ranked by validation quality. The selection
was frozen before the two replay windows were evaluated. This avoids selecting
models because they looked good in the replay itself.

## Open the visual

Open [`index.html`](index.html). It includes the equity curves, selected model
results, allocation and cost stress, latest research picks, and links to the
raw ledgers.

## What was run

- One long U.S. stock is chosen after the close and sold at the next regular-session open.
- Two adjacent windows of 90 sessions each were replayed: `prior_90` and `recent_90`.
- Each selected model was evaluated at 100% notional for diagnostics.
- The focused portfolio used 50% total notional: 25% per model and 50% in cash.
- Entry and exit each use 5 bps adverse slippage; commission is zero.
- The doubled-cost scenario uses 10 bps per side.
- The data are split/dividend-adjusted daily Yahoo research bars.

## Results at a glance

The recent 90-session focused portfolio finished slightly positive at the
50%-total allocation. The prior 90-session window was stronger. The doubled
cost recent scenario was negative, and every bootstrap lower bound remained
below zero. This is an interesting candidate for paper-forward testing, not a
capital-ready strategy.

Daily bars do not reconstruct the closing auction, overnight news timing,
opening spread, or partial fills. The historical replay does not use Alpaca
news. The quote-aware, news-gated paper step is documented in
[`FOCUSED_PAPER_FORWARD.md`](FOCUSED_PAPER_FORWARD.md).

## Reproduce

From the Trading Lab directory, the archived top-N replay now requires an
explicit legacy flag because Phase 1 disables rank selection:

```sh
python3 focused_overnight.py --fraction 0.30 --sessions 90 --total-allocation 0.50 --allow-legacy-rank-selection
python3 build_focused_report.py
```

The planned Phase 2 signal command for these two models is:

```sh
python3 overnight_daily.py --selection top30 --allow-legacy-rank-selection
```

It is intentionally inactive during Phase 1; once enabled, it writes a signal
and submits no order by default.
