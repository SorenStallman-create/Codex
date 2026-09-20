# Overnight equity experiment

This is a separate stock experiment from the BTC work. It tests one long U.S.
equity position per session:

1. after the regular-session close, rank the fixed liquid universe;
2. buy one name near the close;
3. sell it at the next regular-session open;
4. repeat the ranking on the next evening.

That is an overnight trade, not a same-day-flat day trade. The historical
runner uses price and volume only, split/dividend-adjusted Yahoo daily bars,
one 100% notional position, and 5 bps adverse slippage on entry and exit.
Alpaca paper execution and its Benzinga-backed news feed are wired separately
in `alpaca_overnight.py` and are not called by the backtest.

## Open the visual

Open [index.html](index.html). It has the equity curve, model comparison,
latest evening ranking, cost stress, and links to the raw evidence.

## Phase 1 measurement status

The scoreboard now reports two SPY references for every strategy and period:

- `spy_buy_hold`: split/dividend-adjusted SPY total return with no turnover;
- `spy_buy_hold_matched`: SPY scaled to each strategy's realized volatility,
  with 4% cash and borrowing at cash plus 50 bps.

Every row also carries annualized Sharpe, a Lo-style standard error, a 95%
confidence interval, the minimum detectable Sharpe at 95%/80% power, and an
`INCONCLUSIVE` verdict whenever the confidence interval includes zero. The
current 2022–2024 holdout has 752 observations, so its minimum detectable
Sharpe is about 1.62. All five strategy rows are inconclusive and all are below
matched SPY on that holdout.

Open [`SCOREBOARD.md`](SCOREBOARD.md) for the compact report. The run was
pre-registered in `prereg/phase1_overnight_20260919.json`, and
`holdout_registry.json` records the periods that have been scored. A repeated
holdout is blocked unless an explicit `--reopen-holdout` audit flag is used.

## Phase 2 safety and execution measurement

The historical signal is now frozen for the first 60 quote-aware paper
sessions. The execution layer keeps total notional at or below 50%, keeps each
model at or below 25%, uses no leverage, buys at the quoted ask, sells at the
quoted bid, blocks quotes older than 5 seconds or wider than 12 bps, blocks
severe or missing news, and halts on reconciliation errors. It writes the
exact entry/exit evidence to [`paper/fills.csv`](paper/fills.csv). The fixed
slippage decision rule and current count are shown in the
[Phase 2 dashboard](paper/index.html).

This preserves the measured signal while testing the part daily bars cannot
observe: the actual cost of getting in and out. It does not claim that the
historical return will persist; the first decision is whether the edge survives
measured fills.

## Current result

No validation champion is selected. On the 2022–2024 holdout, overnight
momentum lost 31.71% after the modeled 5 bps/side slippage. Intraday reversal
returned +19.58%, but its maximum drawdown was 37.11%, its Sharpe confidence
interval included zero, and it remained below matched SPY. On the 2025–2026
recent block, reversal returned +48.49% with a 32.10% drawdown, but it fell to
−3.21% under doubled costs. No model passes a promotion gate.

These results are a research signal, not evidence that the strategy is ready
for capital. The fixed current universe has survivorship bias, daily bars do
not reconstruct the closing auction or opening quote, and the news gate is
disabled in the historical run.

## Files

- `data/*.jsonl`: raw daily bars for 48 liquid symbols, including `SPY`.
- `data_audit.json`: row counts, date coverage, failures, and SHA-256 hashes.
- `decision_log.csv`: every daily model pick, features, entry, exit, and result.
- `trade_log.csv`: closed simulated trades.
- `summary.csv`, `stress.csv`, `equity.csv`, `daily_results.csv`: metrics and
  replayable curves.
- `latest_signal.json`: the last complete session's model rankings. The latest
  session has no next-session exit yet, so it is not an order instruction.

Run from the Trading Lab directory:

```sh
python3 preregister.py create --run-id my_overnight_run
python3 overnight_equities.py --reopen-holdout  # only for an explicit audit migration
python3 build_overnight_report.py
# With Alpaca credentials, create a news-gated signal (no order by default)
python3 overnight_daily.py --selection top30
# Record the first quote-aware simulated paper entry/exit; no broker order is sent
python3 overnight_daily.py --selection top30 --record-paper --paper-leg entry
python3 overnight_daily.py --selection top30 --record-paper --paper-leg exit
```

Use `python3 overnight_equities.py --refresh` only when a new historical data
download is intended. No credentials are needed for the Yahoo research feed.
