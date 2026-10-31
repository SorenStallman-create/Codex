# Round 7 pre-registration: IBIT forward paper test ("Flash-IBIT-4h")

Written 2026-10-31 before any Round 7 code ran. Soren chose this on 2026-10-31 ("Yes, IBIT paper log").
Paper only. No order path. No leverage. Long or cash only (Mosaic: no shorting).

## Why this rule
Round 6 found the best prediction points at 4-24h horizons, top 5-10% confidence, high volatility.
The only live feed reachable is Equibles real-time quotes for IBIT (spot BTC ETF), US weekdays only,
with no intraday history and no funding or open interest. So the rule may use only what an hourly
IBIT log can compute.

**Who pays us (hypothesis, not evidence):** after sharp hourly BTC moves during US hours, ETF and
retail flow overshoots and market makers lean against it; the 4h drift after confident signals is
the payment. If this is wrong, the forward log will show no edge.

## Rule
- Snapshot P(t): the last trade at or before each whole hour t.
- Inputs at hour t: log returns r1, r2, r3, r4 (P(t) vs P(t-1h) ... P(t-4h)); vol4 = std of the four
  1h returns; pos4 = where P(t) sits between the min and max of P(t-4h..t); each return also divided by vol4.
  Every lookback snapshot must exist (within 20 minutes of its hour) or there is no decision.
- Target: 4h forward log return divided by vol4.
- Model: LightGBM regression, the Round 6 settings (lr 0.03, 31 leaves, min 500 rows per leaf,
  0.7 feature and bagging fraction, 300 trees, seed 7).
- Signal: go long if the prediction is above the 90th percentile of predictions over the last 365
  days of the training data (top 10%). Long only.
- Decision hours: 08:00 to 16:00 New York time, Monday to Friday. Hold exactly 4h; exit at the first
  quote at or after entry + 4h. One position at a time. Skip a decision if the position is open.

## Stage 1 (historical check, BTC Bitstamp, read once)
- Train on all hours 2018-01-01 to 2024-12-31. Score 2025-01-01 to 2026-10-25 on decision hours only.
- Report gross bp per trade, hit rate, trades, net at 10 and 25 bp per side, and the percentile
  against 200 random-entry placebos with the same trade count in the same decision hours.
- Pass bar to start the forward log: gross edge per trade at least 10 bp AND placebo rank at least 90%.
- If stage 1 fails, the log does not start; I report and ask Soren.

## Forward log (stage 2)
- Frozen model: retrained once on all hours 2018-01-01 to 2026-10-25, threshold from its last 365 days.
  The model file and its SHA-256 are committed before the first live decision. No retraining.
- Hourly routine reads IBIT from Equibles, appends the quote, decides, and logs to the repo.
- Fills: buy at the ask, sell at the bid (real spread). Also report at 10 and 25 bp per side.
- $10,000 paper notional, 1x.
- Review once at 100 closed trades or on 2027-04-30, whichever comes first.
  Pass: mean net per trade above 0 with a 90% bootstrap interval above 0, at the real spread,
  and ahead of random entries at the 90th percentile. Before tax first, then after tax at 29.75%.
- Counts as trial rows in prereg/TRIALS.md.
