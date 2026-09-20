# Paper-forward plan for the focused top-30% models

Phase 2 is active as a quote-cost measurement. The signal and two-model set
are frozen while the first 60 completed calendar sessions are recorded.

## Frozen strategy set

The broad study's five model families were ranked on the validation block only.
The top 30% retains two models: overnight momentum and intraday reversal into
the close. No future replay window was used to choose them.

## Evening workflow

1. Before the regular-session close, update Alpaca IEX daily bars and collect
   Alpaca/Benzinga-backed news for the current candidates.
2. Run `python3 overnight_daily.py --selection top30` for the frozen selection.
   Add `--record-paper --paper-leg entry` after the close and
   `--record-paper --paper-leg exit` at the next open to write the quote-aware
   ledger. The runner uses the Alpaca latest-quote endpoint by default and can
   accept a labelled fixture for offline tests.
3. Review the two symbols, scores, news flags, and the intended 25% per-model
   allocation. If the two models select the same symbol, keep total exposure
   capped at 50%.
4. Keep the default no-order mode until the account has enough observations.
5. Record the next regular-session open fill and realized return in the paper
   ledger.

## Promotion rule

Collect at least 90 completed paper sessions with quote-aware fills. Review
gross and net return, maximum drawdown, profit factor, average spread/slippage,
news blocks, missing data, and execution failures. The recent historical
replay is not a promotion gate: its bootstrap lower bound is still negative
and the doubled-cost scenario loses money.

## Safety limits for a later paper run

- maximum total notional: 50% of paper equity;
- maximum per-model notional: 25%;
- no leverage;
- block severe negative-news flags;
- do not record or place a paper order when the quote is stale or the spread
  exceeds the configured limit;
- stop the loop after a data, order, or reconciliation error;
- buy at the ask and sell at the bid when measuring simulated paper fills;
- preserve the full model set in any diagnostic run; the Phase 2 recorder uses
  only the frozen top-30% file and does not rank models at runtime;
- do not enable real-money endpoints from this experiment.

No credentials, paper order, or real order was used to create the historical
focused report.

## Fixed 60-session slippage decision

Once 60 completed sessions have both an entry and an exit fill, compute the
median and 75th percentile of the summed entry-plus-exit
`slippage_vs_official_bps` values:

- median round trip **below 8 bps**: the edge may survive; continue to 250
  completed sessions before judging returns;
- median round trip **8–12 bps**: marginal; continue only if measured gross
  overnight edge exceeds measured cost with its confidence interval reported;
- median round trip **above 12 bps**: stop and archive the overnight line.

During those 60 sessions the signal, parameters, universe, allocation limits,
and news rules are frozen. No new model search, rank selection, or historical
replay is allowed to change the measurement window. The live measurement
dashboard is [`../paper/index.html`](../paper/index.html); the ledger is
[`../paper/fills.csv`](../paper/fills.csv).
