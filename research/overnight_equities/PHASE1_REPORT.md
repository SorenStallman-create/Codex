# Phase 1 report

Phase 1 is complete for the overnight-equity line. The runner now requires a
matching preregistration, records scored periods in `holdout_registry.json`,
reports no-turnover and volatility-matched SPY benchmarks, and attaches power
statistics to every strategy summary row.

## The number that controls the next phase

The latest broad recent block contains **428 observations**. Its minimum
detectable Sharpe at 95% confidence and 80% power is **2.15**. The best recent
strategy row, intraday reversal, estimates Sharpe **0.84** with a 95% interval
of **[-0.67, 2.34]**. That row is therefore **INCONCLUSIVE**, and it is only
about 1.09 percentage points above volatility-matched SPY in that block.

The untouched 2022–2024 holdout contains **752 observations**, with a minimum
detectable Sharpe of **1.62**. All five strategy rows have confidence intervals
that include zero, and all five trail volatility-matched SPY on that holdout.

Phase 2 fill-cost measurement has not started. No paper order or live order was
submitted.

See [`SCOREBOARD.md`](SCOREBOARD.md) for every period and model, including
Sharpe error bars, matched-SPY difference, modeled slippage, and verdict.
