# Round 5 pre-registration: PPO with Mosaic indicators + derivatives/sentiment data

Written 2026-10-30 ~23:00 UTC, while the 72 training runs were in progress and **before any
test-year score for the `ext` variant was computed**. Follows the Mosaic research rules
(Mosaic context export, sections 5 and 8). The SHA-256 of this file is recorded in
`prereg/LOCKLOG.md` before `walkforward.py evaluate --variant ext` is run.

## Who pays us (the economic reason)
Short-horizon BTC moves overshoot when leveraged perp traders crowd one side: they pay
funding, push the perp premium over spot, and get liquidated in cascades. A trader who fades
crowding (high funding / premium / greed) or stretched 15-minute moves is paid by those
forced or impatient traders. Round 4 showed the fade exists before fees (~0.8 bp/trade).
Round 5 asks whether crowding data lets the network trade *fewer, larger* fades.

## Frozen setup
- Inputs: 17 base + 24 Guard indicators + 27 new (funding x2, funding z, perp premium and its
  24h change, open interest 1h/4h/24h/30d, perp volume x2, Fear & Greed x2, liquidations x3,
  taker ratio, Coinbase premium, exchange netflow, stablecoin ratio, availability flag,
  Andean 35 / Laguerre 0.5 / McGinley 20 at 15m and 4h) + 3 agent state = 71.
- Data: sessions 2020-01-08 to 2026-03-05 (span of the hourly derivatives mirror).
  Hourly rows used 1h after their stamp; daily values from the next session.
- PPO: identical to rounds 1 and 4 (2x64 tanh, 400 updates, 3 seeds, validation-picked
  checkpoint, greedy ensemble). Markets: spot (long/flat, 20 bp/side), perp (long/short,
  6 bp/side + funding), and zero-fee twins (diagnostic only, never promoted).
- Test: walk-forward years 2021, 2022, 2023, 2024, 2025, 2026 (Jan 1 to Mar 5), each scored
  once.

## Primary metric and pass bar (all must hold, real-fee markets only)
1. Pooled mean daily net return > 0 with 7-day block-bootstrap 95% lower bound > 0.
2. Beats intraday buy-and-hold on the paired daily difference (lower bound > 0).
3. Placebo rank: mean daily return above the 95th percentile of 200 random-timing policies
   with matched turnover on the same days.
4. Sub-periods: positive in at least 4 of the 6 test years.
5. Costs: mean daily return still > 0 at 10 bp and at 25 bp per side.
6. Reported before tax first, then after tax (Virginia short-term 29.75% on positive
   calendar-year gains; losses carried within the pooled test).

Mosaic rule note: Mosaic forbids shorting. The perp arm (which shorts) is research only; only
the spot arm could ever be promoted. Leverage is a sizing layer: considered only if spot passes
all six gates, sized by fractional Kelly with a 1.5x gross cap and 60% drawdown limit, and
compared with the unlevered model at equal average leverage.

## Trial count
This is one more trial in the ledger (`prereg/TRIALS.md`), which counts every scored
configuration in this project so far. A single pass among this many trials would be
treated as a lead for forward paper testing, not as proof.
