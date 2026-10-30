# Trial ledger (every scored configuration in this project)

Counts each strategy × market × fee level that produced an out-of-sample or validation score.
Baselines count too, since a good-looking baseline would also have been reported.

| Round | What was scored | Trials |
|---|---|---|
| 1 | Compact PPO, spot + perp; 8 baselines each (flat, intraday B&H, momentum 1h/4h, reversion 1h, linear, random) | 18 |
| 1b | Deep PPO, spot + perp, with the same baselines | 18 |
| 2 | Validation-only arms: hourly PPO, limit-order PPO, linear 15m/1h, season window (spot/perp, market/limit) | 18 |
| 3 | Swing PPO spot + perp; 7 swing baselines each | 18 |
| 4 | Indicator PPO at real + zero fee (4 markets) with 11 baselines each | 48 |
| 4b | rules_scan: 8 rules × 2 markets × 2 fee levels | 32 |
| 4c | Ad-hoc crowding/basis fade rules on the new data (2 rules × 2 markets × 2 fees), run before this ledger existed | 8 |
| **Total before round 5** | | **160** |
| 5 | Ext PPO at real + zero fee (4 markets) with 11 baselines each (pre-registered) | 48 |

Of these, the trials that showed a 95% interval above zero at real fees:
the round-1 perp linear forecast (+0.031%/day, barely) and nothing else. With 160+ trials,
one such result is expected by chance.
