# News context snapshot — 2026-09-19

This is a weekend research snapshot for the last complete U.S. session,
Friday 2026-09-18. It is context for the paper workflow, not a trade order.
The historical backtest did **not** use these articles.

## Market context

- [Associated Press: How major U.S. stock indexes fared Friday](https://apnews.com/article/da0dbe004b6f83c36e7d1626a9741a92)
  reports a mixed close, a 0.2% S&P 500 gain, a 0.4% Nasdaq gain, and the
  10-year Treasury yield reaching 5% during the week.

## Model candidates and news checks

- **META** was the validation champion's latest price/volume pick. [AP reports
  Mark Zuckerberg distancing Meta from coordinated calls for an AI
  slowdown](https://apnews.com/article/2f4eab05b1e931456d00ebc2fe93c989), while
  [Meta's newsroom lists new product and AI announcements](https://about.fb.com/news/).
  A real news gate should classify the event and verify its timestamp before
  permitting an overnight position.
- **QCOM** was the reversal model's latest pick. A secondary report describes
  a roughly 6% decline on 2026-09-18 after profit-taking following a strong
  month-long rally: [24/7 Wall St. report](https://247wallst.com/investing/2026/09/18/qualcomm-drops-6-as-past-months-rally-unwinds-skyworks-and-qorvo-slip/).
  That is exactly the type of headline a paper news gate should flag rather
  than blindly buy.
- **ISRG** was the close-strength model's latest pick. The quote page records a
  2.55% 2026-09-18 gain and unusually high volume: [Raymond James/Barchart
  quote page](https://raymondjames.websol.barchart.com/?0=news&1_selected=stockQuote&module=stockDetail&region=US&symbol=ISRG).
  A price-only strength signal is insufficient without checking catalysts,
  earnings, and corporate actions.
- **JPM** was the ensemble pick. The [Benzinga JPM news page](https://www.benzinga.com/quote/jpm/news)
  shows the company-specific headline stream that the Alpaca/Benzinga adapter
  should capture before a future paper decision.

## Operational conclusion

The current model output is a candidate list. It does not produce a live
recommendation for Monday because the next-session fill does not exist yet and
the historical run has no timestamped news layer. The paper workflow should
reject a candidate when the news feed shows a severe unresolved negative event,
when the stock has an earnings/corporate-action conflict, or when the current
quote spread is too wide.
