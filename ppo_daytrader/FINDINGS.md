# Findings: PPO day trader (2026-10-30)

All numbers are pooled across the untouched test years 2021 to 2026-10-28 (2,263 sessions),
as mean daily net return with a 7-day block-bootstrap 95% interval. Full tables, per-year
results and the network views are in `artifacts/report.html`.

## Compact network (2×64 MLP, 3 seeds per fold)

| Market | Strategy | %/day | 95% CI | vs intraday B&H | Trades/day |
|---|---|---|---|---|---|
| Spot, 0.20%/side | PPO ensemble | −0.149 | −0.235 … −0.067 | +0.163 (+0.067 … +0.252) | 0.98 |
| | Intraday buy & hold | −0.312 | −0.433 … −0.186 | — | 2.00 |
| | Random, matched turnover | −0.140 | −0.206 … −0.073 | +0.172 | 0.78 |
| | Momentum 1h / reversion 1h | −4.66 / −4.67 | | | 24 |
| | Linear forecast | 0.000 (never trades) | | | 0 |
| Perp, 0.06%/side | PPO ensemble | −0.185 | −0.305 … −0.065 | −0.133 (−0.287 … +0.026) | 2.99 |
| | Intraday buy & hold | −0.052 | −0.180 … +0.077 | — | 2.00 |
| | Linear forecast | +0.031 | +0.001 … +0.064 | +0.083 | 0.17 |

1. **No PPO configuration made money after costs.** Spot lost 0.15% a day with an interval
   below zero. It beat intraday buy and hold only by being in the market 29% of the time
   instead of 100%. A random policy with the same turnover did as well.
2. **The edge exists but is smaller than the cost of trading it.** A ridge forecast of the
   next 15-minute return has an out-of-sample rank IC of 0.013 to 0.048 by year. That edge is
   about 0.01% per trade against a 0.12% (perp) to 0.40% (spot) round trip. With zero costs,
   the frozen spot ensemble is positive in 5 of 6 test years.
3. **What the network learned.** Integrated gradients show the spot policy's long-vs-flat
   decision mostly follows the previous day's return, time of day, time left in the session,
   and day of week. Shuffling any single market input during live play changes the result by
   less than 0.02% a day. Shuffling its own position input is catastrophic. The policy is
   mostly a holding schedule, not a price reader. The PPO's own long preference has a rank IC
   near zero against the next bar.
4. **Training dynamics.** Entropy collapses within about 30 updates as the agent learns to
   stop paying for random trades. The validation-picked checkpoint is often early. Seeds
   disagree noticeably (for example, perp 2021 seed 22 made +0.46% a day while the ensemble
   lost 0.13%), so single-seed results are not trustworthy.
5. **The one lead.** The perp linear forecast, trading only when its forecast clears the
   one-way cost, is the only strategy with an interval above zero. It was one of seven
   baselines and the interval barely clears zero. It should be pre-registered and paper
   tested, not promoted.

## Deep network (capacity test)

Causal dilated conv over the last 64 raw 15-minute bars (return, wicks, gap, volume, range),
fused with the 17 features, Equibles VIX context and agent state. About 40k parameters, 2 seeds.

| Market | PPO ensemble %/day | 95% CI | vs intraday B&H | Trades/day | In market |
|---|---|---|---|---|---|
| Spot | −0.221 | −0.336 … −0.103 | +0.091 | 1.31 | 65% |
| Perp | 0.000 | 0.000 … 0.000 | +0.052 | 0.00 | 0% |

More capacity and raw inputs did not help. On spot it lost more than the compact network.
One seed collapsed to holding all day (identical to intraday buy and hold). On perp, both seeds
learned that the best available action is to never trade. Attributions spread across time of
day, raw volume and the raw return channel, but none of it survives costs out of sample.

## Round 2: attacking the cost gap (validation only)

Eighteen arms were scored on the six 183-day validation windows before any test year was
looked at (`artifacts/selection.json`):

- Hourly PPO decisions: −0.13 to −0.19% a day, worse than 15-minute decisions.
- Resting limit orders (fill only on a 5 bp trade-through, maker fees): −0.08 to −0.16% a day
  for PPO, and −0.38 to −0.71% a day for the linear rule. Limit orders fill mainly when price
  moves against them, and the more a strategy trades, the more of those fills it collects.
- The perp linear-forecast lead fell to −0.018% a day on validation.
- A fitted hour-of-day window flips side between folds and loses 0.076% a day on perp.
  On spot, no window clears the round trip.
- The frozen rule selects the arm with the highest validation mean. That is the 15-minute
  spot linear rule, which never finds a forecast above the fee. It never trades, so its test
  result is exactly zero. No arm that actually trades has a positive validation average.

Leverage was not applied. It multiplies fees and drawdowns as much as returns, and there is
no positive edge to size.

## What would change the verdict

- Lower real costs: maker rebates or VIP fee tiers, which only matter with fill-rate data.
- A longer holding horizon, so a small per-bar edge compounds before paying costs.
- Information that isn't in OHLCV: order book imbalance, funding and basis, liquidations.
