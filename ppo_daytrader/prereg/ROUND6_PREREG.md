# Round 6 pre-registration: edge map and selective prediction

Written 2026-10-30 ~23:25 UTC, before any code for this round produced a score.

## Question
Soren: "find an edge and figure out optimal points for prediction." Every earlier round predicted
at every bar and found the average edge is far below fees. This round asks a narrower question:
*are there specific moments (high-confidence predictions, at some holding horizon) where the
expected move is larger than the round-trip cost?* If so, trade only those moments.

## Who pays us
Same as round 5: crowded perp traders and impatient flow overshoot over hours. A selective
trader only acts when the overshoot is large enough to pay the fee.

## Frozen setup
- Data: continuous 15-minute Bitstamp bars, 2018-01 to 2026-10-29. Positions may be held across
  midnight (no forced daily flat).
- Inputs (68): the 17 base features, 24 Guard indicators, and the 27 round-5 inputs (funding,
  premium, open interest, perp volume, liquidations, Fear & Greed, on-chain, Andean, Laguerre,
  McGinley). Inputs are causal as in rounds 4 and 5. Round-5 inputs are left missing (NaN) outside
  their coverage, and LightGBM handles the gaps natively.
- Decision points: the close of every hourly-aligned 15-minute bar. Entry at the next bar's open.
- Horizons h: 1h, 4h, 12h, 24h, 72h. The label is the log return from the entry open to the close h later,
  divided by trailing volatility.
- Model: LightGBM regressor, one per horizon and fold. Settings are fixed: 300 trees, learning rate
  0.03, 31 leaves, min 500 rows per leaf, feature fraction 0.7, bagging 0.7, seed 7. No tuning.
- Walk-forward: test years 2021 to 2026 (2026 up to 2026-10-25). Train runs from 2018 to the
  validation start, minus a 3-day purge. Validation is the 183 days before the test year, with a
  3-day purge before the test year.
- Selection, on validation only:
  - The horizon h and the coverage level are chosen per fold and market.
  - Coverage means trading only the top x% of |prediction|, with x in {50, 20, 10, 5, 2, 1}.
  - The choice maximises validation net return per day.
  - "Trade nothing" is always allowed and wins if no choice is positive.
- Execution: one position at a time. Hold exactly h, then exit at the close.
  - Spot: long only, 20 bp per side.
  - Perp: long or short, 6 bp per side plus 1 bp per 8h held.

## Primary trials (2): spot and perp, with selected settings. Everything else is secondary.

## Pass bar (pooled test days 2021-01-01 to 2026-10-25, all must hold)
1. Mean daily net return > 0, with a 7-day block-bootstrap 95% lower bound > 0.
2. Above the 95th percentile of 200 placebos with random entry times, the same trade count,
   holding times and long/short mix.
3. Positive in at least 4 of the 6 test years.
4. Still positive at 10 bp and 25 bp per side.
5. Reported before tax, then after tax at 29.75%.

Spot is the only arm that could be promoted, because Mosaic forbids shorting. Leverage is not
considered unless spot passes.
