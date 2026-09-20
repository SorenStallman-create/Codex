# Trading Lab

A local, paper-only strategy research app based on the workflow demonstrated in the two reference videos.

It reproduces the useful parts of that workflow:

- Pull public Binance candlesticks (no API key required)
- Run 67 variants across six strategy families
- Include the video's featured **Andean Oscillator + Laguerre baseline** idea
- Use a 70/30 in-sample / out-of-sample split
- Model 0.15% costs on every position change
- Compare every result with buy-and-hold
- Rank for return, drawdown, profit factor, and sample size—not raw return alone
- Paper-forward-test one selected strategy
- Keep an append-only trade ledger and a separate learnings log

## Start it

Requires Python 3.10 or newer. It has no third-party Python dependencies.

```bash
python3 app.py
```

Then open [http://127.0.0.1:8765](http://127.0.0.1:8765).

Run tests with:

```bash
python3 -m unittest discover -s tests -v
```

## Safety boundary

This build cannot place a real order. It does not accept exchange credentials and has no withdrawal or order endpoint. `POST /api/paper/step` only writes simulated entries/exits to `state/ledger.jsonl` and observations to `state/learnings.md`.

That boundary is intentional. A backtest is a filter, not evidence that a strategy will remain profitable. Keep a strategy in paper mode through a meaningful number of independent trades before even considering a separately reviewed live adapter.

## Strategy families

1. **Andean + Laguerre** — Andean bull/bear crossover gated by a Laguerre price baseline.
2. **SMA crossover** — fast/slow simple moving-average trend following.
3. **EMA crossover** — fast/slow exponential moving-average trend following.
4. **Donchian breakout** — previous-channel breakout with a trend exit.
5. **RSI mean reversion** — oversold/overbought entries with neutral exits.
6. **McGinley Dynamic** — price-above/below baseline states, included as a standard-formula proxy because the reference video's exact settings are not fully stated.

Signals are evaluated on a completed candle and applied at the next candle's open. That one-bar delay prevents accidental look-ahead.

## Data

The app requests public klines from Binance's market-data-only endpoint. If the endpoint is temporarily unavailable, the UI reports the error instead of silently inventing a result.

## Reproducible weekly experiment

Run `python3 research_weekly.py` to create a timestamped report under `research/`. It downloads and validates the full hourly BTCUSDT spot history needed for a four-fold, 180-day walk-forward model screen, freezes the top 25 of 67 candidates before either holdout, and compares them over the latest seven complete UTC days and a seed-selected week roughly three months earlier.

The simulation uses a $10,000 paper account, 10% unleveraged spot allocation, next-open fills after completed-hour signals, 0.15% fee and 0.05% slippage per side, and treats short signals as cash. Separate CSVs vary trading costs and allocation while holding other assumptions fixed. It does not model the video's 10× leverage, borrow/funding, stops, or intrabar fills.

The video-specific McGinley rule is included only as a standard-formula proxy; its exact timeframe/settings are not fully stated and it did not make the frozen top 25 in this run. The other private/configured video strategies are not claimed as replicated because full input settings and complete entry/exit rules are unavailable. The HTML report links to every candle, model rank, fold score, decision, trade, and equity value, with a manifest containing hashes and data-quality checks.

Run tests with `python3 -m unittest discover -s tests -v`.

## Files

- `app.py` — data client, indicators, backtester, batch runner, paper ledger, and local server
- `web/index.html` — interactive dashboard
- `tests/test_backtest.py` — deterministic tests for signal timing, batch size, and metrics
- `state/` — created on first paper trade; remains local

## Repaired weekly replay

The default homepage now serves `research/repaired_replay/index.html`. The old
exploratory screener is at `/lab`; evidence downloads are served under `/research/`.
Run `python3 repair_replay.py` to rebuild the replay from the original saved candles
and rankings. Both inputs are checked against the original SHA-256 manifest.
This preserves the original September 17, 2026 11:00 UTC cutoff.

The replay has the original 25-model cohort and a second 25-model comparison with
7 Andean, 3 McGinley, 4 SMA, 4 EMA, 4 Donchian, and 3 RSI variants. Family quotas
were chosen after inspecting the weeks, so the second cohort is exploratory.
Models within each family use the original pre-period ranking. The source videos'
complete configured scripts are unavailable; these remain local approximations.
The original procedure is four fixed scoring blocks, not rolling walk-forward
refitting. Original files are preserved, including their historical wording.

The UI includes equity and price charts, candle replay, indicator values, complete
trade tables, cost/allocation comparisons, and downloadable evidence. The account
starts in cash each week and liquidates at the end; forced exits are labeled.
Tests cover signal causality, accounting reconciliation, allocation family counts,
and corrected entry-slippage attribution. Profit factor is undefined when no losses
exist, rather than a fabricated value of 99.

## Focused three-bot experiment (September 17/18, 2026)

The homepage now opens the focused report in `research/focused_bot/index.html`.
The protocol, findings, frozen selection and complete evidence are in that folder.
The earlier replay remains at `/research/repaired_replay/index.html`.

`focused_engine.py` shares causal strategy/risk logic between historical tests and
`focused_paper.py`. `focused_research.py develop` was run once to freeze settings
using 2021–2023 and choose the champion using 2024. `focused_research.py evaluate`
reproduces the reserved 2025 test, later regression, controls and sensitivity tests
without changing that selection. `build_focused_report.py` renders the results.
`focused_data.py` caches and audits public BTC, ETH and SOL hourly history.

Trend Guard 20/50 is the frozen paper candidate. None of the three bots passed all
predeclared gates. See `research/focused_bot/FINDINGS.md` for results and limits.
The manual forward runner has been initialized at a fresh UTC-hour boundary;
run `python3 focused_paper.py step` after new hourly candles close. It does not
run continuously, place real orders, or count historical warmup as forward profit.

## Intraday rebuild

The homepage now serves `research/intraday_v1/index.html`: individual-day SPY and
crypto tests, minute replay and a separate live-paper monitor. Each historical day
starts with a normalized $10,000 account and ends flat. Full losses remain in the
daily summary; additive P&L is not presented as compounding.

`intraday_engine.py` implements 5-minute indicators and 1-minute simulated fills.
`intraday_research.py` freezes parameters and evaluates daily accounts.
`intraday_data.py` and `intraday_spy_data.py` preserve and validate minute sources.
`intraday_live.py --watch` runs a finite-session, public-data paper monitor while
this computer is awake. The dashboard has a stop control. No exchange orders or
account credentials are used. See `research/intraday_v1/FINDINGS.md` for failures,
small-sample limits, costs and reproduction commands.

## RohOnChain guide study (September 19, 2026)

The guide-specific visual report is `research/roh_guide_v1/index.html`. It applies
the useful architecture ideas—maker/checker separation, evidence gates, and
research categories—without integrating Kimi or any language-model service.
`roh_guide_research.py` tests a causal Gatev-style SPY/QQQ residual pair rule
with 1.5/2/2.5 standard-deviation entries. The canonical 2σ rule lost $63.86
across 11 exploratory evaluation sessions after modeled costs. It is not a
capital-ready strategy. `roh_guide_checker.py` is a separate deterministic
promotion checker; it cannot place orders. Volatility-surface, factor and
insider-cluster candidates are documented but require options, point-in-time
cross-sectional, or filing-arrival data that the current candle set does not
contain. See `research/roh_guide_v1/ROH_GUIDE_IMPLEMENTATION.md`.

## BTC five-bot reset (September 18, 2026)

The new BTC-only visual comparison is `research/btc_live_five/index.html`.
It tests five fixed live-style candidates on audited BTCUSDT hourly spot data:
Trend Guard 20, Breakout Guard 20, Pullback Guard 35, Andean + Laguerre 35,
and McGinley 20. Bitcoin is treated as a 24/7 market, so positions remain open
until their explicit exit, stop, or drawdown guard. The 2024 holdout leaders
were Breakout Guard (+4.99%) and Trend Guard (+4.54%) after the lab's modeled
costs. Both have negative bootstrap lower bounds, so neither is capital-ready.

`research/btc_five_bots` is the separate 15-minute UTC-day screen. It tests
Volume Impulse, Lag-1 Reversal, Daily VWAP Reversion, New York Open Breakout,
and EMA/ATR Trend. Every candidate lost in the 2024 holdout after costs;
`research/btc_five_bots_first_pass` preserves the original higher-turnover run.
The study uses public Binance futures archives for the 15-minute screen and
public Binance spot history for the live-style companion. Neither path has a
broker order endpoint, credentials, Kimi, or leverage.
