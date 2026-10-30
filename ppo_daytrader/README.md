# PPO day trader (BTC/USD, paper research only)

A reinforcement-learning day trader for BTC/USD, built on the Trading Lab's rules:
paper only, causal signals, next-bar fills, modeled costs on every trade, frozen settings
before any holdout is scored, and comparison against simple baselines. Nothing here can
place an order.

## The question

Can a PPO-trained network, seeing only completed 15-minute bars, make money trading
BTC within the UTC day after realistic costs? And if it can't, is the limit the network or
the information available to it?

## Setup

| Piece | Choice and reason |
|---|---|
| Data | Bitstamp BTC/USD 1-minute candles, 2018-01-01 to 2026-10-29, from the public Git mirror `ff137/bitstamp-btcusd-minute-data`. Exchange APIs are blocked from the research container. Days with gaps, zero-volume runs of 60+ minutes, or flagged outages are excluded (`artifacts/data_audit.json`). |
| Session | One UTC day of 96 fifteen-minute bars. Start flat; forced flat at the last close. |
| Timing | The decision for bar *j* sees features computed at the close of bar *j−1* and fills at bar *j*'s open. Tests check that removing future bars leaves past features unchanged. |
| Spot market | Long or flat. 0.15% fee + 0.05% slippage per side, matching the Trading Lab cost model. |
| Perp proxy | Short, flat or long. 0.05% taker + 0.01% slippage per side, plus 0.01% funding at each 08:00 and 16:00 UTC stamp held. Spot prices are used, so basis is ignored. |
| Reward | Log growth of equity after costs, in percent. |
| Compact network | Separate actor and critic MLPs, 20 → 64 → 64 → actions (tanh). Inputs: 17 named, volatility-scaled features (multi-horizon returns, distance from averages, volatility regime, bar range, close location, volume z-score, calendar) plus position, time left and open P&L. |
| Deep network | Capacity test. A causal dilated 1-D conv reads the last 64 raw bars (return, upper wick, lower wick, gap, volume, range), fused with the 17 features, VIX context from the Equibles connector, and agent state. Shared 128-wide trunk. |
| PPO | Clipped surrogate (0.2), GAE (γ 0.99, λ 0.95), entropy 0.01, Adam 3e-4 annealed, 128 sessions per rollout. |
| Walk-forward | Test years 2021–2026. Each fold trains on everything from 2018 up to 183 days before the test year. Those 183 days pick the checkpoint. The test year is scored once. |
| Ensemble | The seed models' action probabilities are averaged, then the greedy action is taken. |
| Baselines | Always flat, intraday buy and hold, 1h and 4h momentum, 1h reversion, a ridge forecast that trades only when the forecast clears the cost, and a random policy with matched turnover. |
| Statistics | 7-day block bootstrap 95% intervals on mean daily return and on the paired difference to intraday buy and hold. |

## Reproduce

```bash
git clone --depth 1 https://github.com/ff137/bitstamp-btcusd-minute-data ../data_src/btcmin
pip install torch numpy pandas
python3 ppo_daytrader/data.py --src ../data_src/btcmin --v2 --swing
cd ppo_daytrader
python3 walkforward.py train --variant mlp     # 36 runs, ~15 min on 4 cores
python3 walkforward.py train --variant deep    # 24 runs, ~2 h on 4 cores
python3 walkforward.py evaluate --variant mlp
python3 walkforward.py evaluate --variant deep
python3 walkforward.py train --variant swing   # 4h bars, 30-day holds; 36 runs, ~40 min on 4 cores
python3 walkforward.py evaluate --variant swing
python3 indicators.py --src ../../data_src/btcmin  # Guard indicators at 15m and 4h
python3 ic_scan.py && python3 rules_scan.py
python3 walkforward.py train --variant ind     # 72 runs (real and zero fees), ~40 min
python3 walkforward.py evaluate --variant ind
python3 export_viewer.py --variant ind --market perp_nofee --year 2026 && python3 build_viewer.py
git clone --depth 1 https://github.com/PietroC21/Crypto-PerpetualFutures ../../data_src/ext/Crypto-PerpetualFutures
git clone --depth 1 https://github.com/ErcinDedeoglu/crypto-market-data ../../data_src/ext/crypto-market-data
git clone --depth 1 https://github.com/akpasz/btc-data ../../data_src/ext/btc-data
pip install pyarrow
python3 ext_data.py                            # round 5 inputs
python3 walkforward.py train --variant ext && python3 walkforward.py evaluate --variant ext
python3 prereg_check.py --variant ext          # pre-registered gates (prereg/)
python3 build_report.py                        # artifacts/report.html
python3 -m unittest tests.test_ppo_daytrader   # from the repo root
```

`context/vix_daily_equibles.csv` is the VIX daily history pulled through the Equibles
connector on 2026-10-30. Model weights, training logs, results JSON and the HTML report
are written under `artifacts/`. Raw inputs are re-downloadable and stay out of Git.

## Findings

See `FINDINGS.md`.
