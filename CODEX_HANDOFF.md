# Codex handoff: Trading Lab

Date: 2026-09-20
Project: paper-only strategy research and Alpaca paper-forward measurement

## Original goal

Build a safer, more useful trading research workflow from the supplied strategy
videos and the written trading-research plan. Preserve useful performance while
reducing overfitting and execution risk. The current continuation point is the
overnight-equities Phase 2 measurement: freeze the signal, record realistic
quote-aware paper fills for 60 sessions, and use the pre-registered slippage
rules to decide whether the line continues to 250 sessions or is archived.

No live-money trading is enabled by this project.

## HIGH PRIORITY: research-driven self-improving agent

The user wants the missing research-driven model implemented after the frozen
deterministic paper measurement is underway. This is the "Luna"-style model
described in the earlier brief: each session it gathers point-in-time market,
news, catalyst, and risk data; chooses a liquid stock or chooses no trade; and
records its thesis, evidence, confidence, invalidation conditions, and result.
It must feed the same quote-aware paper execution gates before any order path is
considered.

The supplied inspiration is the video [*how to literally print money while you
sleep with AI dropshipping (just copy me)*](https://www.youtube.com/watch?v=ScF0pi6FSLQ).
The transferable idea is the operating loop, not the video's income claim:

- **Autonomous testing loop:** generate candidate strategies or variants, test
  them, measure the result, review the evidence, and queue the next experiment.
- **Data-driven iteration:** preserve the inputs, assumptions, baseline,
  hypothesis, and outcome; change one material variable at a time and compare
  against an untouched validation window.
- **Product/strategy testing analogy:** treat product variants and market
  strategies as testable candidates with explicit metrics, while recognizing
  that market data are noisy and do not guarantee repeatable demand or returns.
- **Scale only proven winners:** promote a strategy only after it clears
  pre-registered out-of-sample and quote-aware paper gates; increasing capital is
  never the default response to a short winning streak.
- **Kill losers quickly:** archive candidates that fail their declared tests or
  risk limits, record why they failed, and do not resurrect them through
  untracked parameter changes.
- **Automated research and review:** automate data collection, timestamped
  research, trade/thesis journaling, result attribution, report generation, and
  a human-readable review queue; keep the decision and evidence log immutable.
- **Strict risk gates before real-money execution:** maintain paper-only and
  shadow modes first, enforce position/exposure limits, quote-age and spread
  checks, cost stress, drawdown limits, reconciliation, and a kill switch, and
  require independent review before any live order path is even considered.

This source is a workflow inspiration, not independent evidence of trading
profitability or financial advice. Its claims must not be copied into
production without measurement.

Implementation priority, in order:

1. Add a frozen research-agent interface and event log (model/version, prompt
   hash, data timestamps, sources, decision, confidence, risks, and no-trade
   reason).
2. Run it in shadow mode beside the deterministic overnight cohort; no orders,
   no self-editing, and no prompt/model changes during each pre-registered
   60-session window.
3. Allow only offline, one-variable proposals into a review queue. A proposal
   must pass tests and an untouched validation window before it can become a
   new frozen version.
4. Compare the agent with the deterministic cohort and
   `spy_buy_hold_matched`, using the same fill ledger, confidence intervals,
   power line, cost stress, and 250-session gate.

This is intentionally a later priority. The current repository does not yet
contain a Luna/Hermes implementation, and no Luna sessions, trades, or results
exist. The supplied video is a design reference, not proof of profitability.

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
resolve the Alpaca host in the restricted environment. A later network-enabled
check returned HTTP 401 because the local private file currently has a malformed
key ID and an invalid secret field. No credential value was written to this
repository. Regenerate a paper-only key pair in the Alpaca dashboard, store it
only in `~/.config/trading_lab/alpaca.env` with mode `0600`, and rerun the check.

## Remaining work

1. Configure the user's regenerated Alpaca **paper** key pair locally. The
   repository must never receive the values.
2. On the next regular market session, start the frozen nightly paper loop and record one entry and one exit per
   completed session in `research/overnight_equities/paper/fills.csv`.
3. Do not tune the signal, add candidates, or replay the measured window while
   the first 60 sessions are being collected.
4. After 60 completed sessions, apply the fixed rule in `paper/README.md`:
   median round-trip slippage under 8 bps continues to 250 sessions; 8–12 bps
   is marginal and requires a measured gross edge above measured cost; over 12
   bps archives the overnight line.
5. After the deterministic measurement is stable, implement the research-driven
   agent under the priority above and preregister its shadow-mode window.
6. Before any real capital is considered, require all gates in the plan:
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
