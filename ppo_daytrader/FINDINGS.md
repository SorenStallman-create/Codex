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

## Round 3: longer holds (swing, 4-hour bars, up to 30 days)

The same compact PPO is trained on continuous 4-hour bars cut into 30-day sessions. It can hold
a position from 4 hours to 30 days and is forced flat at the end of each chunk. Train and
validation chunks end before the next window starts, so they never overlap it, and each test year
is played as 12 back-to-back chunks (9 in 2026). That is 2,070 test days in total. Costs are
unchanged. Figures are per-day rates, with a 2-chunk block bootstrap.

| Market | Strategy | %/day | 95% CI | vs buy & hold | Trades / 30 days | In market |
|---|---|---|---|---|---|---|
| Spot, 0.20%/side | PPO ensemble | −0.001 | −0.124 … +0.125 | −0.030 (−0.069 … +0.010) | 6.9 | 78% |
| | Buy & hold (per chunk) | +0.028 | −0.100 … +0.155 | — | 2.0 | 100% |
| | Linear forecast | −0.000 | −0.128 … +0.116 | −0.028 | 4.4 | 62% |
| | Random, matched turnover | +0.002 | −0.067 … +0.069 | −0.027 | 3.7 | 32% |
| | Trend: 3-day / 11-day / price vs 16-day mean | −0.092 / −0.066 / −0.015 | | all below B&H | 9–22 | ~52% |
| Perp, 0.06%/side | PPO ensemble | −0.087 | −0.211 … +0.045 | −0.095 (−0.202 … +0.013) | 45 | 90% |
| | Buy & hold (per chunk) | +0.008 | −0.129 … +0.137 | — | 2.0 | 100% |
| | Linear forecast | −0.033 | −0.174 … +0.097 | −0.041 | 54 | 99% |

1. **Longer holds fixed the cost problem.** The spot swing ensemble's result moves only about
   0.03% a day between zero cost and base cost. In the day trader, that gap was the whole story.
2. **Fixing costs exposed the next problem: no directional skill.** Spot swing PPO roughly
   matched BTC by being long 78% of the time. It trailed buy and hold, and its long preference has
   a rank IC between −0.03 and +0.02 against the next 4-hour bar. The perp version traded 45 times
   per 30 days, both long and short, and lost 0.087% a day.
3. **Simple trend following does not work here either.** Every trend and reversion rule trailed
   buy and hold on both markets. The linear forecast of the next 4-hour bar has an out-of-sample
   rank IC of −0.03 to +0.05, and its sign changes from year to year.
4. Results by year track BTC's direction: the strategy gains in up years (2023, 2024) and loses
   in down years (2022, 2025). That pattern is market exposure, not edge.

No swing configuration is a candidate for leverage. Leverage would only magnify a 76–87%
drawdown driven by market exposure.

## Round 4: zero fees, and the long-term indicators at short-term speed

Soren asked for a run without fees, and for the Trading Lab's long-term indicators (Trend
Guard, Breakout Guard, Pullback Guard: SMA 20/50, EMA200 regime, Donchian 20/10, RSI 14, ATR 14)
to be turned into short-term inputs. `indicators.py` computes each one twice: on 15-minute bars,
and as the original 4-hour version taken from the last completed 4-hour bar. Together with session
VWAP and on-balance volume, that adds 24 inputs, for 44 in total. The PPO was retrained at real
fees and at zero fees (no fee, slippage or funding). The zero-fee version is a diagnostic, not a
tradable setup.

**Why early 2021 looked good and then faded.** From Jan 1 to Feb 20, 2021, the first compact spot
ensemble made +1.09% a day. Over the same weeks BTC rose 1.43% a day intraday. The ensemble was
long half the time, and its daily P&L tracked BTC with a correlation of 0.78. That was the bull
market, not skill. From Feb 21 to May 1 it lost 0.47% a day.

**At short horizons the long-term indicators flip sign** (`ic_scan.py`, `artifacts/ic_scan.json`).
Against the next 15 minutes to 4 hours, 15-minute RSI, distance above the 20-bar high, price vs
its 4-hour mean and session VWAP all have a *negative* rank IC of −0.03 to −0.10. The sign is the
same in every period from 2018 to 2026. Strength over the last few hours is followed by slight
pullbacks, so the short-term equivalent of a trend signal is a fade. The 4-hour Guard
indicators carry almost no information about the next few hours.

| Pooled 2021–2026, %/day | Zero fees | Real fees, perp | Real fees, spot |
|---|---|---|---|
| PPO with 44 inputs, perp | **+0.451** (+0.314 … +0.594), 57 trades/day | −0.003, ~0 trades | — |
| PPO with 44 inputs, spot | +0.232 (+0.135 … +0.333), 27 trades/day | — | −0.016, ~0 trades |
| First PPO (17 inputs) | | −0.185 | −0.149 |
| Breakout Guard 4h, held intraday | +0.080 (+0.010 … +0.155) | +0.038 (−0.033 … +0.109) | −0.043 |
| Trend Guard 4h, held intraday | +0.066 | +0.008 | −0.102 |
| 4h trend + 15m RSI dip | +0.046 (+0.011 … +0.082) | −0.022 | −0.048 |
| Intraday buy & hold | +0.088 | −0.052 | −0.312 |
| Linear forecast | +0.347 (perp) | +0.033 (−0.006 … +0.073) | 0 |

1. **Without fees, the network clearly works.** It finds the short-term fade with every input
   type: close within the bar, distance from the 20-bar high and 10-bar low, distance from the
   4-hour mean, and session VWAP. Shuffling any of these during play costs 0.09–0.16% a day.
   The zero-fee perp ensemble is positive in every test year.
2. **The edge per trade is far smaller than the fee.** +0.45% a day spread over 57 trades is about
   0.008% (0.8 bp) per trade. The perp fee is 0.06% per side. Trained with real fees, the same
   network learns that almost nothing clears the cost and stays flat. That is the correct answer,
   and much better than the first network's −0.19% a day.
3. **The edge is shrinking.** The zero-fee perp result by test year is +0.93, +0.47, +0.30, +0.55,
   +0.24 and +0.14% a day for 2021 to 2026, and the indicator ICs weaken in the same way. This is
   the "works in 2021, then craps out" pattern. The short-term inefficiency the network finds is
   being competed away.
4. **The long-term bots held intraday are the best real-fee rules,** but not significant. Breakout
   Guard 4h on perp makes +0.038% a day, with an interval that includes zero. These bots were
   designed to hold for days, and the forced flat at midnight works against them.

`network_viewer.html` (built by `export_viewer.py` and `build_viewer.py`) plays one network
through real 2026 test days bar by bar. It shows every input, both hidden layers, the strongest
weight × activation flows, the action probabilities, and the inputs behind each decision.

## Round 5: all Mosaic data plus derivatives, sentiment and on-chain data (pre-registered)

Exchange APIs are blocked from the research container, so the new data comes from public
GitHub mirrors (`ext_data.py` lists sources and licences):
- Hourly Binance perp and spot data, funding rates from 5 venues, and open interest (2020 to 2026-03-06).
- Daily liquidations, taker buy/sell ratio, Coinbase premium, exchange and stablecoin flows (from 2022-12).
- The daily Fear & Greed index (from 2018).

The remaining Mosaic indicators were added too: Andean 35, Laguerre 0.5 and McGinley 20 at 15
minutes and 4 hours. That makes 68 market inputs. No usable order-book history was found.

Following the Mosaic research rules, the pass bar was written and SHA-256 locked before any
test year was scored (`prereg/ROUND5_PREREG.md`, `prereg/LOCKLOG.md`). It counts as one more
trial in `prereg/TRIALS.md`, which held 160 trials before this round. Sessions run from
2020-01-08 to 2026-03-05, so training windows are shorter than in rounds 1–4.

**What the new data says on its own** (`artifacts/ic_scan_ext.json`):
- **Funding rate, perp premium and Fear & Greed lean contrarian** over the rest of the day: rank IC of about −0.02 to −0.04, with the same sign in 5–6 of 6 years. High funding and greed are followed by slightly weaker returns.
- **Open interest, liquidations and on-chain flows** show no consistent sign.
- **The Mosaic indicators at 15 minutes** point to fading the last few hours (IC −0.04 to −0.07), the same pattern as round 4.

**Pre-registered gates, real fees, pooled 2021-01-01 to 2026-03-05** (`artifacts/prereg_ext.json`):

| Gate | Spot (no shorting, Mosaic-compliant) | Perp (research only) |
|---|---|---|
| Mean %/day, 95% CI | −0.024 (−0.054 … +0.009) | −0.000 (−0.011 … +0.012) |
| 1. Lower bound > 0 | fail | fail |
| 2. Beats intraday buy & hold | pass (by staying mostly flat) | fail |
| 3. Above 95th percentile of 200 random-timing placebos | fail (89.5th) | fail (72.5th) |
| 4. Positive in 4 of 6 years | fail (2 of 6) | fail (2 of 6) |
| 5. Positive at 10 and 25 bp/side | fail (+0.003 / −0.037) | fail |
| **Passes** | **no** | **no** |

After tax (29.75% on positive years), spot made +7.3% in 2021 and +0.8% in 2022, then lost
17.6% in 2023, 31.4% in 2025 and 2.9% in early 2026. 2024 was flat.

With zero fees, the 68-input network still finds the short-term fade:
- Perp: +0.281%/day (+0.162 … +0.406), positive in every year.
- Spot: +0.210%/day.

Both are *below* round 4's 44-input zero-fee result (+0.451%/day on perp). The extra data did
not add usable signal at the 15-minute scale, and the shorter training window cost more than
the new inputs gave. The real-fee networks again learned to trade rarely; the spot one trades
about 0.27 times a day.

**Verdict:** nothing passes, so leverage stays off. The crowding inputs (funding, premium,
Fear & Greed) are the only new information with a consistent sign. Their effect plays out over
the rest of the day, not the next 15 minutes. The only way to test them honestly is with
fewer, longer holds and a forward paper log, because the hourly mirror ends in March 2026.

## Round 6: where is the edge, and at what horizon? (pre-registered)

Question from Soren: find an edge and the best points to predict. Protocol in `prereg/ROUND6_PREREG.md`,
hash-locked before any code ran. `edge_map.py` trains a LightGBM forecaster walk-forward on all 68 inputs
(hourly decisions, enter at the next open), for 1h, 4h, 12h, 24h and 72h horizons. Each year it picks the
horizon and confidence cut-off on validation data, then trades the test year once. Page: `artifacts/edge_map.html`.

**Pre-registered result: fail.**

| Market | Mean/day | 95% range | Trades | Placebo rank | Years positive |
|---|---|---|---|---|---|
| Spot (20 bp/side, long only) | −0.055% | −0.142 … +0.030 | 374 | 61.5% | 3 of 6 |
| Perp (6 bp/side + funding) | −0.020% | −0.112 … +0.073 | 1218 | 81% | 3 of 6 |

Spot before tax by year: 2021 +42%, 2022 −70%, 2023 +4%, 2024 +4%, 2025 −19%, 2026 −47%.
Validation kept choosing the 72h horizon with half of all signals, which is close to "be long while it rises".

**Edge map (pooled out-of-sample, descriptive):** gross bp per trade on the top x% most confident predictions.

| Horizon | All | Top 20% | Top 10% | Top 5% | Top 1% |
|---|---|---|---|---|---|
| 1h | 1.4 | 3.2 | 2.8 | 1.7 | 5.7 |
| 4h | 3.7 | 11.0 | 14.0 | 13.4 | 18.7 |
| 12h | 7.6 | 13.9 | 14.6 | 8.9 | 27.5 |
| 24h | 7.8 | 22.1 | 37.0 | 41.6 | 54.5 |
| 72h | 18.4 | 48.0 | 53.5 | 57.2 | 87.3 |

Round-trip costs: perp 12 bp, spot 40 bp. Long horizons include BTC's drift. The high-volatility third
roughly doubles the 24h edge (60 bp vs 24–26 bp). Hour of day is noise (13–63 bp across hours).

**Exploratory, looked at after seeing test data (a lead, not evidence):** per-year edge vs drift
(`artifacts/edge_by_year_exploratory.json`).
- 4h, top 10%: +21, +4, +21, +22, +14, −8 bp (2021–2026), ahead of drift in 5 of 6 years.
- 24h, top 5%: −33, +35, +93, +49, +76, +16 bp; about 90% longs and clustered on the same days.

**Verdict:** the best prediction points are 4–24 hour holds, the top 5–10% most confident signals, and
volatile markets. Nothing passes the locked test. The next honest step is to freeze one rule and log it
forward on paper; that needs a live BTC feed, which this environment's network policy blocks.

## What would change the verdict

- Lower real costs: maker rebates or VIP fee tiers, which only matter with fill-rate data.
- ~~A longer holding horizon~~: tested in Round 3. It removes the cost drag, but no directional edge appears.
- A short-term edge large enough to pay the fee. Round 4 found a real one, but at about 0.8 bp per trade it is roughly 15× too small for perp taker fees. A venue with maker rebates *and* fill data would be the only way to test it honestly.
- Information that isn't in OHLCV: order book imbalance, funding and basis, liquidations.
