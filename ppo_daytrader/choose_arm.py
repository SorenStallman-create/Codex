"""Round-2 model selection on validation windows only.

For every (variant, market) arm, the seed ensemble of each fold plays that fold's 183-day
validation window. The arm with the highest mean validation return across the six folds is
frozen in artifacts/selection.json. Test years are not touched here.
"""
import json
import numpy as np

from data import OUT
from env import MARKETS, run_policy
from ppo import policy_fn
from walkforward import PROTOCOL, VARIANTS, folds, sessions_for
import evaluate

ARMS = [('mlp', 'spot'), ('mlp', 'perp'), ('h1', 'spot'), ('h1', 'perp'), ('h1', 'spot_maker'), ('h1', 'perp_maker'),
        ('m15_maker', 'spot_maker'), ('m15_maker', 'perp_maker')]
# Follow-up of the perp linear-forecast lead: the ridge forecast trades only when its forecast
# clears the fee it would pay. Fitted on each fold's training window, scored on validation.
LINEAR_ARMS = [(f'linear_{"h1" if k == 4 else "m15"}', k, mk) for k in (1, 4) for mk in ('spot', 'perp', 'spot_maker', 'perp_maker')]


def fit_window(sess, days, market):
    r = np.log(sess['close'][days] / sess['open'][days])
    c = np.concatenate([np.zeros((len(days), 1)), np.cumsum(r, 1)], 1)
    best = (0.0, None)
    for a in range(c.shape[1] - 1):
        for b in range(a + 1, c.shape[1]):
            w = (c[:, b] - c[:, a]).mean()
            for d in ((1, -1) if -1 in market.positions else (1,)):
                if d * w - 2 * market.cost > best[0]:
                    best = (d * w - 2 * market.cost, (a, b, d))
    return best


def window_rule(market, win):
    def act(obs, j):
        if win is None:
            return np.full(len(obs), market.flat_action)
        a, b, d = win
        return np.full(len(obs), market.positions.index(d if a <= j < b else 0))
    return act


def main():
    rows = []
    for variant, mk in ARMS:
        evaluate.set_variant(variant)
        sess = sessions_for(variant)
        per_fold = []
        for f in folds(sess['days']):
            models = evaluate.load_models(mk, f['year'])
            if not models:
                break
            r = run_policy(MARKETS[mk], sess, f['val'], policy_fn(models))
            per_fold.append({'year': f['year'], 'val_day_pct': 100 * float(r['daily_ret'].mean()),
                             'trades_per_day': float(r['turnover'].mean())})
        if len(per_fold) < len(PROTOCOL['test_years']):
            print('skip (incomplete)', variant, mk)
            continue
        score = float(np.mean([p['val_day_pct'] for p in per_fold]))
        rows.append({'variant': variant, 'market': mk, 'val_score_day_pct': score, 'folds': per_fold})
        print(f'{variant:10s} {mk:11s} val {score:+.4f}%/day', [round(p['val_day_pct'], 3) for p in per_fold], flush=True)
    from env import resample
    from data import load_sessions
    base = load_sessions()
    for name, k, mk in LINEAR_ARMS:
        sess = resample(base, k); per_fold = []
        evaluate.set_variant('mlp')
        for f in folds(sess['days']):
            pred = evaluate.fit_linear(sess, f['train'])
            r = run_policy(MARKETS[mk], sess, f['val'], evaluate.linear_rule(MARKETS[mk], pred))
            per_fold.append({'year': f['year'], 'val_day_pct': 100 * float(r['daily_ret'].mean()),
                             'trades_per_day': float(r['turnover'].mean())})
        score = float(np.mean([p['val_day_pct'] for p in per_fold]))
        rows.append({'variant': name, 'market': mk, 'val_score_day_pct': score, 'folds': per_fold})
        print(f'{name:10s} {mk:11s} val {score:+.4f}%/day', [round(p['val_day_pct'], 3) for p in per_fold],
              'trades/day', round(float(np.mean([p['trades_per_day'] for p in per_fold])), 2), flush=True)
    # Intraday seasonality: one contiguous UTC-hour window (and side) chosen on the training
    # window by mean return minus a taker round trip; flat if nothing clears it.
    sess = resample(base, 4)
    for mk in ('spot', 'perp'):
        m = MARKETS[mk]; per_fold = []
        for f in folds(sess['days']):
            sc, win = fit_window(sess, f['train'], m)
            r = run_policy(m, sess, f['val'], window_rule(m, win))
            per_fold.append({'year': f['year'], 'val_day_pct': 100 * float(r['daily_ret'].mean()),
                             'trades_per_day': float(r['turnover'].mean()), 'window': win})
        score = float(np.mean([p['val_day_pct'] for p in per_fold]))
        rows.append({'variant': 'season_h1', 'market': mk, 'val_score_day_pct': score, 'folds': per_fold})
        print(f'season_h1  {mk:11s} val {score:+.4f}%/day', [p['window'] for p in per_fold], flush=True)
    rows.sort(key=lambda r: -r['val_score_day_pct'])
    if len(rows) < len(ARMS) + len(LINEAR_ARMS) + 2:
        print('not all arms trained; selection not frozen')
        return
    out = {'rule': 'highest mean validation return of the seed ensemble across the six folds', 'ranking': rows,
           'selected': {'variant': rows[0]['variant'], 'market': rows[0]['market']}}
    (OUT / 'selection.json').write_text(json.dumps(out, indent=2))
    print('selected', out['selected'])


if __name__ == '__main__':
    main()
