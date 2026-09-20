# Codex handoff: Trading Lab

Date: 2026-09-19
Project: paper-only strategy research and Alpaca paper-forward measurement

## Original goal

Build a safer, more useful trading research workflow from the supplied strategy
videos and the written trading-research plan. Preserve useful performance while
reducing overfitting and execution risk. The current continuation point is the
overnight-equities Phase 2 measurement: freeze the signal, record realistic
quote-aware paper fills for 60 sessions, and use the pre-registered slippage
rules to decide whether the line continues to 250 sessions or is archived.

No live-money trading is enabled by this project.

## Repository and Git state

The Trading Lab directory was not previously a Git repository, and the nearby
`Documents/GitHub/Claude-Code` checkout is unrelated and has no configured
remote. This handoff therefore bootstrapped a local repository in this directory,
created the requested dated handoff branch, and pushed it to the configured
`origin` remote. The branch contains the reviewed project files listed below.

## Completed work

### Research controls (Phase 1)

- Added `benchmark.py` with SPY buy-and-hold and volatility-matched benchmarks.
- Added `power.py` with Sharpe standard error, confidence intervals, and minimum
  detectable Sharpe calculations.
- Added `preregister.py` and preregistration files for the overnight work.
- Added `holdout_registry.py` and wired holdout reuse warnings into the runners.
- Added `scoreboard.py` and updated the overnight report to show error bars,
  sample size, power, and matched benchmark differences.
- Removed runtime rank-based selection from the focused overnight path; the
  current top-30 cohort is frozen before forward measurement.

### Paper execution controls (Phase 2)

- Added `paper_execution.py` with quote-age, spread, news, allocation, duplicate,
  partial-fill, reconciliation, and kill-switch checks.
- Added `alpaca_overnight.py` for historical bars/news and paper quote/order
  access using standard-library HTTP.
- Added `alpaca_setup.py` and the Alpaca integration guide. Environment templates
  remain outside Git.
- Added the paper ledger contract and dashboard under
  `research/overnight_equities/paper/`.
- Added the frozen Phase 2 preregistration at
  `research/overnight_equities/prereg/phase2_paper_20260919.json`.
- Kept the execution path paper-only. No live order endpoint or live credential
  is stored in this project.

### Evidence already available

- Broad recent overnight screen: 428 observations; the best broad model's
  confidence interval included zero, so it was inconclusive.
- 2022–2024 holdout: 752 observations; the strategy confidence intervals did
  not clear the matched SPY benchmark.
- Focused recent 90-session screen: the frozen top-30 portfolio returned about
  +1.05% at the modeled cost and 50% allocation; doubled modeled cost returned
  about −3.40%. These are historical research results, not live evidence.
- The Phase 2 paper ledger is currently header-only: 0 of 60 sessions complete.

## Files included in this handoff

- Root Python modules: the research engines, report builders, data clients,
  Alpaca adapter/setup, safety controls, and dashboard server.
- `tests/`: the complete deterministic test suite, including Phase 1 and paper
  execution tests.
- `web/`: the local dashboard assets.
- Root project docs: `README.md`, `ALPACA_INTEGRATION.md`, the BTC and overnight
  start notes. Environment templates stay outside Git.
- `research/overnight_equities/`: Phase 1/2 documentation, preregistration and
  holdout metadata, compact summaries, current signals, and the focused top-30
  and paper dashboards.

Large re-downloadable candle files, generated raw decision/trade/equity exports,
virtual environments, Python caches, runtime state, archives, and credential
files are intentionally excluded by `.gitignore`.

## Tests run

From the project root:

```text
python3 -m unittest discover -s tests -q
Ran 84 tests in 2.927s
OK

python3 -m compileall -q .
PASS
```

`python3 alpaca_setup.py check` reached the network-check path but could not
resolve the Alpaca host in the restricted environment. It did not write a
credential into the repository. Run it again on a machine with network access
after configuring a paper key pair.

## Remaining work

1. Configure the user's regenerated Alpaca **paper** key pair locally. The
   repository must never receive the values.
2. Start the frozen nightly paper loop and record one entry and one exit per
   completed session in `research/overnight_equities/paper/fills.csv`.
3. Do not tune the signal, add candidates, or replay the measured window while
   the first 60 sessions are being collected.
4. After 60 completed sessions, apply the fixed rule in `paper/README.md`:
   median round-trip slippage under 8 bps continues to 250 sessions; 8–12 bps
   is marginal and requires a measured gross edge above measured cost; over 12
   bps archives the overnight line.
5. Before any real capital is considered, require all gates in the plan:
   250+ quote-aware paper sessions, a positive bootstrap lower bound versus
   matched SPY, measured slippage within 2 bps of the model assumption, the
   pre-registered drawdown limit, survival at 2x modeled cost, and a written
   stop condition.

## Exact next steps

1. On the new machine, clone the handoff branch and enter the project directory.
2. Use Python 3.10 or newer; no third-party package is required for the core
   runner.
3. Configure the private paper credentials with the commands below.
4. Run the tests and inspect `research/overnight_equities/paper/index.html`.
5. At the configured session cutoff, run the entry command. At the next regular
   session open, run the exit command. Review the ledger after each leg.
6. Reconcile the Alpaca paper account and local ledger before proceeding after
   any data, quote, news, or order error.

## Setup and environment notes

```bash
cd /path/to/trading-lab
python3 --version
python3 -m unittest discover -s tests -q
python3 alpaca_setup.py init
python3 alpaca_setup.py set
python3 alpaca_setup.py check
```

`alpaca_setup.py set` stores the paper credentials only at
`~/.config/trading_lab/alpaca.env` with mode `0600`. The file is outside this
repository and must remain private. The paper endpoint is
`https://paper-api.alpaca.markets`; the adapter uses the IEX quote feed.

The frozen paper commands are:

```bash
python3 overnight_daily.py --selection top30 --record-paper --paper-leg entry
python3 overnight_daily.py --selection top30 --record-paper --paper-leg exit
python3 build_paper_dashboard.py
```

The runner defaults to simulated fills and does not submit orders. Any future
paper order submission must remain explicitly enabled, use the paper endpoint,
and pass the existing allocation, quote, news, and reconciliation gates.
