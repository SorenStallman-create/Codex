# Alpaca paper-forward plan

## Data and news

The official Alpaca historical API uses `https://data.alpaca.markets/{version}`
for stocks and news. The Python SDK is `alpaca-py`, but the included adapter
uses standard-library HTTP so the handoff can be inspected without installing
the SDK. Set either:

```sh
export APCA_API_KEY_ID='...'
export APCA_API_SECRET_KEY='...'
```

or the equivalent `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` variables. Never
put keys in the repository, Desktop folder, CSV files, or prompt text.

## Local setup

1. Create or log in to an Alpaca **Paper Trading** account and open its paper
   account dashboard.
2. Generate a paper API key pair. Paper keys are separate from live keys.
3. From this Trading Lab directory, run:

```sh
python3 alpaca_setup.py init
python3 alpaca_setup.py set
python3 alpaca_setup.py check
```

The `set` command hides both inputs and saves them only to
`~/.config/trading_lab/alpaca.env` with file mode `0600`. `check` verifies the
paper account endpoint and retrieves one IEX SPY quote. Keep the keys out of
chat and out of every research artifact.

`alpaca_overnight.py` provides:

- `historical_bars(...)` for adjusted daily bars;
- `historical_news(...)` for timestamped articles;
- `submit_paper_order(...)` for a DAY market order at the paper endpoint.

The news endpoint is Alpaca's `v1beta1/news`. Alpaca documents the historical
archive as dating to 2015 and the feed as Benzinga-provided. Use the direct
Benzinga API only if we need more coverage or latency than the Alpaca plan
provides.

## Daily paper loop

1. At 3:45–3:55 p.m. ET, request current bars, the last 24 hours of news, and
   the asset list from Alpaca.
2. Reject symbols that are halted, below the price/liquidity floor, have a
   scheduled earnings release or corporate action before the planned exit, or
   have unresolved severe negative news.
3. Run the frozen ranker and save the full candidate table plus article IDs.
4. Place one notional buy order through the paper endpoint with an idempotent
   `client_order_id`.
5. At the next regular-session open, submit the sell order and record the
   quote, fill, spread, and any delay.
6. Do not retry a partially filled order blindly. Reconcile the position and
   invoke a kill switch if the account has a stale order, a gap beyond the
   configured loss limit, or a data/news outage.

The current adapter defaults to a dry-run preview. Actual paper submission
requires passing `--execute-paper`; the research backtest never calls it.

## Phase 2 recorder

`overnight_daily.py --selection top30 --record-paper` now measures the frozen
signal through the quote-aware path. It reads Alpaca's latest quote endpoint,
uses the ask for a simulated buy and the bid for a simulated sell, and writes
`paper/fills.csv`. The runner refuses a stale quote (over 5 seconds), a spread
over 12 bps, severe or missing news, an over-cap allocation, a duplicate leg,
or an unreconciled partial fill. It sends no order in this mode. The first 60
completed calendar sessions are a measurement window; the decision thresholds
are recorded in `paper/README.md` and the Phase 2 preregistration.

## Required paper evidence before capital

- at least 90 prospective sessions;
- full quote-aware fills and timestamps;
- news IDs and timestamps stored before the decision cutoff;
- comparison against SPY close-to-open and a no-trade cash baseline;
- cost, delay, gap, and half-allocation stress;
- no promotion unless the lower block-bootstrap bound is positive and the
  result survives doubled costs.
