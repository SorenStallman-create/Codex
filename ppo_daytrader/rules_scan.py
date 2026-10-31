"""The long-term bots' rules, and their short-term equivalents, played in the same 15-minute
day-trading environment at zero fees and at real fees. Test years 2021-2026 pooled; no
parameter is fitted. Writes artifacts/rules_scan.json."""
import json
from dataclasses import replace
import numpy as np
from data import OUT, FEATURES, STATE
from env import MARKETS, run_policy
from indicators import IND_FEATURES, load_sessions_ind
from evaluate import block_boot

NAMES = FEATURES + IND_FEATURES + STATE
F = {n: i for i, n in enumerate(NAMES)}


def state_rule(market, long_on=None, short_on=None, long_off=None, short_off=None):
    """Enter on a condition, hold until the exit condition (or the session close)."""
    pos_ix = {p: i for i, p in enumerate(market.positions)}
    def act(obs, j):
        v = lambda n: obs[:, F[n]]
        pos = v('position'); new = pos.copy()
        if long_on is not None:
            new = np.where((pos == 1) & long_off(v), 0, new)
            new = np.where((new == 0) & long_on(v), 1, new)
        if short_on is not None and -1 in market.positions:
            new = np.where((pos == -1) & short_off(v), 0, new)
            new = np.where((new == 0) & short_on(v), -1, new)
        return np.select([new == p for p in pos_ix], list(pos_ix.values()))
    return act


RSI = lambda r: (r - 50) / 25  # indicators store (rsi - 50) / 25
RULES = {
    # the long-term bots' own state, held intraday
    'trend_guard_4h': dict(long_on=lambda v: v('h4_trend_guard') > 0, long_off=lambda v: v('h4_trend_guard') == 0),
    'breakout_guard_4h': dict(long_on=lambda v: v('h4_breakout_guard') > 0, long_off=lambda v: v('h4_breakout_guard') == 0),
    'pullback_guard_4h': dict(long_on=lambda v: v('h4_pullback_guard') > 0, long_off=lambda v: v('h4_pullback_guard') == 0),
    # the same formulas on 15-minute bars
    'trend_guard_15m': dict(long_on=lambda v: v('m15_trend_guard') > 0, long_off=lambda v: v('m15_trend_guard') == 0),
    'breakout_guard_15m': dict(long_on=lambda v: v('m15_breakout_guard') > 0, long_off=lambda v: v('m15_breakout_guard') == 0),
    'pullback_guard_15m': dict(long_on=lambda v: v('m15_pullback_guard') > 0, long_off=lambda v: v('m15_pullback_guard') == 0),
    # what the IC scan says the short-term equivalent is: fade stretched moves
    'rsi_reversion_15m': dict(long_on=lambda v: v('m15_rsi14') < RSI(30), long_off=lambda v: v('m15_rsi14') > RSI(50),
                              short_on=lambda v: v('m15_rsi14') > RSI(70), short_off=lambda v: v('m15_rsi14') < RSI(50)),
    # long-term trend for direction, short-term stretch for timing
    'trend_4h_dip_15m': dict(long_on=lambda v: (v('h4_trend_guard') > 0) & (v('m15_rsi14') < RSI(30)),
                             long_off=lambda v: (v('m15_rsi14') > RSI(55)) | (v('h4_trend_guard') == 0),
                             short_on=lambda v: (v('h4_regime') == 0) & (v('h4_sma20_50') < 0) & (v('m15_rsi14') > RSI(70)),
                             short_off=lambda v: (v('m15_rsi14') < RSI(45)) | (v('h4_sma20_50') > 0)),
}


def main():
    s = load_sessions_ind(); d = s['days'].astype('datetime64[D]')
    test = np.where(d >= np.datetime64('2021-01-01'))[0]
    years = d[test].astype('datetime64[Y]').astype(int) + 1970
    out = {}
    for mk in ('spot', 'perp'):
        base = MARKETS[mk]
        for name, kw in [('intraday_buy_hold', None)] + list(RULES.items()):
            for mult in (0.0, 1.0):
                m = replace(base, cost=base.cost * mult, funding=base.funding * mult)
                act = (lambda obs, j: np.full(len(obs), m.positions.index(1))) if kw is None else state_rule(m, **kw)
                r = run_policy(m, s, test, act)
                dr = r['daily_ret']
                row = {'mean_day_pct': 100 * float(dr.mean()), 'ci95': [100 * x for x in block_boot(dr)],
                       'trades_per_day': float(r['turnover'].mean()), 'exposure_pct': 100 * float(np.abs(r['held']).mean()),
                       'gross_per_trade_pct': 100 * float(np.log1p(dr).sum() / max(r['turnover'].sum() / 2, 1)),
                       'by_year': {int(y): 100 * float(dr[years == y].mean()) for y in np.unique(years)},
                       'equity': np.cumprod(1 + dr)[::7].round(4).tolist()}
                out.setdefault(mk, {}).setdefault(name, {})['zero_fee' if mult == 0 else 'real_fee'] = row
                print(f"{mk:4s} {name:20s} {'0 fee ' if mult == 0 else 'real  '} {row['mean_day_pct']:+.3f}%/day "
                      f"[{row['ci95'][0]:+.3f},{row['ci95'][1]:+.3f}] trades/day {row['trades_per_day']:.2f} "
                      f"per round trip {row['gross_per_trade_pct']:+.3f}% yrs " + ' '.join(f"{v:+.2f}" for v in row['by_year'].values()), flush=True)
    (OUT / 'rules_scan.json').write_text(json.dumps(out))


if __name__ == '__main__':
    main()
