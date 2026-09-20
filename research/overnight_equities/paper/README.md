# Phase 2 quote-aware paper measurement

This folder is the execution scoreboard for the frozen overnight signal. The
signal and its two-model selection are locked for the first 60 completed
calendar sessions. The purpose of this phase is to measure the fill cost that
the historical replay could not observe.

## Safety controls

- 50% maximum total notional and 25% maximum per-model notional;
- no leverage;
- buy fills use the quoted ask and sell fills use the quoted bid;
- quotes older than 5 seconds or wider than 12 bps are blocked;
- severe or missing news blocks a leg;
- duplicate legs and reconciliation errors halt the loop;
- the runner writes a local ledger and sends no order by default.

The current `fills.csv` is intentionally header-only until a quote-aware paper
session is run with Alpaca credentials or a clearly labelled offline fixture.

## Fixed decision rule

After 60 completed sessions, the ledger computes median and 75th-percentile
round-trip slippage. Median below 8 bps continues to 250 sessions, 8–12 bps is
marginal and requires a measured gross edge with a confidence interval, and
above 12 bps archives the overnight line. The rule is preregistered in
`../prereg/phase2_paper_20260919.json`.

## Commands

Create a signal only:

```sh
python3 overnight_daily.py --selection top30
```

Record a quote-aware entry or exit (simulated at the adverse quote side):

```sh
python3 overnight_daily.py --selection top30 --record-paper --paper-leg entry
python3 overnight_daily.py --selection top30 --record-paper --paper-leg exit
```

Use `--quote-fixture path.json` only for deterministic testing. It must contain
`{"AAPL": {"bp": ..., "ap": ..., "t": ...}}`-style quote data. The default
path uses Alpaca's latest quote endpoint and requires credentials in the
environment; credentials are never written to this folder.
