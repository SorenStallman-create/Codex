# Why these five model families were tested

## Evidence

- [Lachance, *Night trading: Lower risk but higher returns?*](https://onlinelibrary.wiley.com/doi/abs/10.1002/rfe.1180)
  reports persistence and predictability in overnight returns and identifies
  past overnight return, volatility, momentum, and turnover as useful
  predictors. It also emphasizes that implementation costs and liquidity must
  be measured.
- [Berkman, Koch, Tuttle, and Zhang, *Paying Attention*](https://ideas.repec.org/a/cup/jfinqa/v47y2012i04p715-741_00.html)
  finds positive overnight returns followed by daytime reversals, especially
  in high-attention stocks. That motivates the close-strength and reversal
  variants, but it also warns that opening prices can be expensive for retail
  traders.
- [Lou, Polk, and Skouras, *The cross-section of intraday and overnight returns*](https://www.sciencedirect.com/science/article/pii/S0304405X21000854)
  documents that anomaly behavior differs between the daytime and overnight
  segments. The model therefore scores close-to-open outcomes directly rather
  than using close-to-close returns as a proxy.
- [Salotra and coauthors, overnight versus daytime ETF study](https://www.mdpi.com/2227-9091/14/4/84)
  is a recent robustness reference for splitting returns into overnight and
  daytime components. It is treated as a research lead, not proof of a live
  edge in individual stocks.

## Tested variants

1. **Overnight momentum** — rolling close-to-open strength, medium-term
   momentum, market-relative momentum, and volume, with an SPY regime filter.
2. **Intraday reversal into the close** — weak same-day return, positive
   overnight history, and volatility control, without forcing a bullish market
   regime.
3. **Close strength** — strong same-day return, close location near the high,
   unusual volume, and short-term momentum, with a regime filter.
4. **Market-relative strength** — residual 20-day strength versus SPY plus
   60-day momentum and overnight history, with a regime filter.
5. **Risk-adjusted ensemble** — a fixed blend of the above feature families
   with a volatility penalty.

All rules are frozen before the holdout. The validation champion is selected
only from 2020–2021. The 2022–2024 block is untouched by selection; 2025–2026
is a later descriptive check.

## What the news layer will do later

The price-only backtest intentionally does not pretend that today's news was
available historically. In paper-forward operation, fetch timestamped news
before the close, map articles to symbols, and use a conservative gate:

- reject a candidate with unresolved negative regulatory, fraud, bankruptcy,
  offering, or earnings-surprise news;
- require at least one recent company-specific or sector catalyst for a
  high-conviction pick;
- store the article IDs, created times, headlines, and model score in the
  decision ledger so the next-day result is auditable.

Alpaca's historical and real-time news endpoints are the first integration
choice because Alpaca documents historical news back to 2015 and says the
current feed is provided directly by Benzinga. A direct Benzinga subscription
is the upgrade path if fuller wire coverage or lower latency is needed.

## Main risks

- This is a long-only, one-name, full-notional experiment. A single overnight
  gap can dominate a day's result.
- The fixed universe is not survivorship-bias-free and excludes delisted names.
- A daily bar cannot model a real closing auction, opening spread, partial fill,
  halt, overnight news shock, borrow, or corporate-action timing perfectly.
- The attractive recent reversal result fails the doubled-cost stress. That is
  a direct warning against promoting it to live capital.
