# Trading Lab scoreboard

Generated from the latest preregistered overnight-equity run. Sharpe is annualized from daily excess returns over 4% cash; confidence intervals use the Lo-style standard error. `INCONCLUSIVE` means the 95% Sharpe interval includes zero.

- Return target: 0.5% per week compounded = 29.61% CAGR. The scoreboard does not treat weekly hit rate as a success metric.
- Preregistration: `phase1_overnight_20260919`
- Holdout registry: `/Users/sorenstallman/Documents/Codex/2026-09-16/https-www-youtube-com-watch-v-4/outputs/trading-lab/research/overnight_equities/holdout_registry.json`
- Selection policy: No champion is selected by validation rank. All candidates and the matched benchmarks are reported.
- Modeled round-trip slippage: 10.00 bps (5 bps per side); realized slippage is measured in the Phase 2 quote-aware paper ledger.

| Period | Line | n_obs | Sharpe | 95% CI | Min detectable Sharpe | Return | Matched SPY diff | Status |
|---|---|---:|---:|---:|---:|---:|---:|---|
| development | Close strength | 1257 | -0.33 | [-1.20, 0.55] | 1.25 | -12.86% | -96.53% | **INCONCLUSIVE** |
| development | Risk-adjusted ensemble | 1257 | -1.06 | [-1.94, -0.18] | 1.25 | -49.09% | -129.03% | **INCONCLUSIVE** |
| development | Overnight momentum | 1257 | -0.25 | [-1.12, 0.63] | 1.25 | -6.86% | -90.29% | **INCONCLUSIVE** |
| development | Intraday reversal into the close | 1257 | -0.45 | [-1.33, 0.42] | 1.25 | -25.03% | -114.06% | **INCONCLUSIVE** |
| development | Market-relative strength | 1257 | 0.09 | [-0.79, 0.96] | 1.25 | 23.24% | -48.12% | **INCONCLUSIVE** |
| holdout | Close strength | 752 | -0.25 | [-1.39, 0.88] | 1.62 | -10.23% | -40.07% | **INCONCLUSIVE** |
| holdout | Risk-adjusted ensemble | 752 | 0.02 | [-1.11, 1.16] | 1.62 | 8.19% | -20.47% | **INCONCLUSIVE** |
| holdout | Overnight momentum | 752 | -0.60 | [-1.74, 0.53] | 1.62 | -31.71% | -62.27% | **INCONCLUSIVE** |
| holdout | Intraday reversal into the close | 752 | 0.20 | [-0.93, 1.34] | 1.62 | 19.58% | -10.83% | **INCONCLUSIVE** |
| holdout | Market-relative strength | 752 | -0.19 | [-1.33, 0.94] | 1.62 | -5.44% | -34.78% | **INCONCLUSIVE** |
| recent | Close strength | 428 | -0.28 | [-1.78, 1.23] | 2.15 | -12.05% | -59.66% | **INCONCLUSIVE** |
| recent | Risk-adjusted ensemble | 428 | -1.51 | [-3.01, 0.00] | 2.15 | -44.95% | -87.45% | **INCONCLUSIVE** |
| recent | Overnight momentum | 428 | 0.44 | [-1.06, 1.95] | 2.15 | 25.86% | -31.74% | **INCONCLUSIVE** |
| recent | Intraday reversal into the close | 428 | 0.84 | [-0.67, 2.34] | 2.15 | 48.49% | 1.09% | **INCONCLUSIVE** |
| recent | Market-relative strength | 428 | -0.43 | [-1.94, 1.07] | 2.15 | -21.68% | -73.60% | **INCONCLUSIVE** |
| validation | Close strength | 504 | -0.53 | [-1.91, 0.86] | 1.98 | -23.13% | -75.08% | **INCONCLUSIVE** |
| validation | Risk-adjusted ensemble | 504 | 0.07 | [-1.32, 1.46] | 1.98 | 3.85% | -51.39% | **INCONCLUSIVE** |
| validation | Overnight momentum | 504 | 0.64 | [-0.74, 2.03] | 1.98 | 50.78% | -15.32% | **INCONCLUSIVE** |
| validation | Intraday reversal into the close | 504 | 0.66 | [-0.73, 2.05] | 1.98 | 47.86% | -11.56% | **INCONCLUSIVE** |
| validation | Market-relative strength | 504 | 0.58 | [-0.81, 1.97] | 1.98 | 42.47% | -20.45% | **INCONCLUSIVE** |

## Pre-registered prediction vs realized Sharpe

| Period | Line | Predicted range | Realized Sharpe | Inside range |
|---|---|---:|---:|---|
| development | Overnight momentum | [0.05, 0.30] | -0.25 | no |
| development | Intraday reversal into the close | [0.05, 0.30] | -0.45 | no |
| development | Close strength | [0.05, 0.30] | -0.33 | no |
| development | Market-relative strength | [0.05, 0.30] | 0.09 | yes |
| development | Risk-adjusted ensemble | [0.05, 0.30] | -1.06 | no |
| validation | Overnight momentum | [0.05, 0.30] | 0.64 | no |
| validation | Intraday reversal into the close | [0.05, 0.30] | 0.66 | no |
| validation | Close strength | [0.05, 0.30] | -0.53 | no |
| validation | Market-relative strength | [0.05, 0.30] | 0.58 | no |
| validation | Risk-adjusted ensemble | [0.05, 0.30] | 0.07 | yes |
| holdout | Overnight momentum | [0.05, 0.30] | -0.60 | no |
| holdout | Intraday reversal into the close | [0.05, 0.30] | 0.20 | yes |
| holdout | Close strength | [0.05, 0.30] | -0.25 | no |
| holdout | Market-relative strength | [0.05, 0.30] | -0.19 | no |
| holdout | Risk-adjusted ensemble | [0.05, 0.30] | 0.02 | no |
| recent | Overnight momentum | [0.05, 0.30] | 0.44 | no |
| recent | Intraday reversal into the close | [0.05, 0.30] | 0.84 | no |
| recent | Close strength | [0.05, 0.30] | -0.28 | no |
| recent | Market-relative strength | [0.05, 0.30] | -0.43 | no |
| recent | Risk-adjusted ensemble | [0.05, 0.30] | -1.51 | no |

## Interpretation

A positive return is not a pass by itself. The matched-SPY difference, Sharpe confidence interval, minimum detectable Sharpe, and drawdown/cost gates must be read together. The current historical rows are measurement evidence, not a live-capital authorization.

The Phase 2 quote-aware recorder is active with a frozen signal. Its fixed decision rule requires 60 completed calendar sessions before any cost conclusion; open `paper/index.html` for the current count.
