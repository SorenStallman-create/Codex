"""Record one trained network playing whole sessions, bar by bar, for the network viewer:
every input, every hidden unit, the action probabilities, the critic's value, what it did,
and why (gradient x input on each action's logit margin over flat).

    python3 export_viewer.py --variant ind --market perp --year 2026
"""
import argparse, json
import numpy as np
import torch

from data import OUT
from env import MARKETS, run_policy
from ppo import policy_fn
from walkforward import VARIANTS, fold_list, sessions_for
import evaluate as ev

LABELS = {
    'r1': 'Last 15m return', 'r4': 'Last 1h return', 'r16': 'Last 4h return', 'r64': 'Last 16h return',
    'r_day': 'Return since midnight', 'r_prev_day': "Yesterday's return", 'dev16': 'Price vs 4h average',
    'dev96': 'Price vs 24h average', 'vol16_rel': 'Recent volatility', 'vol96_rel': 'Daily volatility regime',
    'range_rel': 'Bar range', 'volu_z': 'Volume surprise', 'clv': 'Close within bar range',
    'tod_sin': 'Time of day (sin)', 'tod_cos': 'Time of day (cos)', 'dow_sin': 'Day of week (sin)', 'dow_cos': 'Day of week (cos)',
    'position': 'Current position', 'time_left': 'Time left in day', 'unreal': 'Open P&L',
    'vwap_dev': 'Price vs session VWAP', 'obv_slope': 'On-balance volume trend',
}
for spd, nm in (('m15', '15m'), ('h4', '4h')):
    LABELS.update({f'{spd}_sma20_50': f'{nm} SMA20 vs SMA50', f'{spd}_close_ema200': f'{nm} price vs EMA200',
                   f'{spd}_ema200_slope': f'{nm} EMA200 slope', f'{spd}_regime': f'{nm} uptrend regime',
                   f'{spd}_breakout20': f'{nm} vs 20-bar high', f'{spd}_breakdown10': f'{nm} vs 10-bar low',
                   f'{spd}_rsi14': f'{nm} RSI 14', f'{spd}_atr_rel': f'{nm} ATR vs normal',
                   f'{spd}_trend_guard': f'{nm} Trend Guard on', f'{spd}_breakout_guard': f'{nm} Breakout Guard on',
                   f'{spd}_pullback_guard': f'{nm} Pullback Guard on'})


def r3(a):
    return np.round(np.asarray(a, np.float64), 3).tolist()


def main(variant, market_name, year, n_sessions=8):
    ev.set_variant(variant)
    market = MARKETS[market_name]
    sess = sessions_for(variant)
    f = {x['year']: x for x in fold_list(variant, sess['days'])}[year]
    models = ev.load_models(market_name, year)
    metas = [json.loads((ev.MODELS / f'{market_name}_{year}_s{s}.json').read_text())['meta'] for s in ev.SEEDS]
    k = int(np.argmax([m['best_val_day_log_pct'] for m in metas]))
    net, seed = models[k], ev.SEEDS[k]
    days = f['test']
    played = run_policy(market, sess, days, policy_fn([net]), keep_obs=True)
    order = np.argsort(played['daily_log'])
    trades = played['turnover']
    picks = {'Best day': order[-1], 'Worst day': order[0], 'Median day': order[len(order) // 2],
             'Busiest day': int(np.argmax(trades)), 'Most recent day': len(days) - 1}
    rng = np.random.default_rng(year)
    active = np.where(trades > 0)[0]  # a cautious model rarely trades: sample the days where it did
    pool = active if len(active) >= n_sessions else np.arange(len(days))
    for i in rng.choice(pool, min(len(pool), n_sessions), replace=False):
        if len(picks) >= n_sessions:
            break
        if int(i) not in [int(v) for v in picks.values()]:
            picks[f'Sample day {len(picks) - 4}'] = int(i)
    X_all = played['obs'].reshape(-1, len(ev.INPUTS))
    base = torch.as_tensor(X_all.mean(0))
    A = net.actor
    fl = market.flat_action
    sessions = []
    for tag, i in picks.items():
        x = torch.as_tensor(played['obs'][i]).requires_grad_(True)
        h1 = A[1](A[0](x)); h2 = A[3](A[2](h1)); logits = A[4](h2)
        probs = torch.softmax(logits, -1)
        why = {}
        for a, p in enumerate(market.positions):
            if a == fl:
                continue
            g, = torch.autograd.grad((logits[:, a] - logits[:, fl]).sum(), x, retain_graph=True)
            why['long' if p > 0 else 'short'] = r3((g * (x - base)).detach().numpy())
        with torch.no_grad():
            v = net.critic(x).squeeze(-1)
        d = int(days[i])
        sessions.append({
            'tag': tag, 'day': str(sess['days'][d]),
            'open': np.round(sess['open'][d], 2).tolist(), 'high': np.round(sess['high'][d], 2).tolist(),
            'low': np.round(sess['low'][d], 2).tolist(), 'close': np.round(sess['close'][d], 2).tolist(),
            'x': r3(x.detach().numpy()), 'h1': r3(h1.detach().numpy()), 'h2': r3(h2.detach().numpy()),
            'probs': r3(probs.detach().numpy()), 'value': r3(v.numpy()), 'why': why,
            'held': played['held'][i].astype(int).tolist(), 'step_log_pct': r3(100 * played['step_log'][i]),
            'day_ret_pct': round(100 * float(played['daily_ret'][i]), 3), 'trades': int(trades[i]),
        })
    W = lambda l: r3(l.weight.detach().numpy())
    B = lambda l: r3(l.bias.detach().numpy())
    out = {
        'variant': variant, 'market': market_name, 'cost_per_side': market.cost, 'year': year, 'seed': seed,
        'positions': list(market.positions), 'inputs': list(ev.INPUTS),
        'labels': [LABELS.get(n, n) for n in ev.INPUTS],
        'train_range': metas[k]['train_range'], 'val_range': metas[k]['val_range'],
        'weights': {'W1': W(A[0]), 'b1': B(A[0]), 'W2': W(A[2]), 'b2': B(A[2]), 'W3': W(A[4]), 'b3': B(A[4])},
        'year_stats': {'days': int(len(days)), 'mean_day_pct': 100 * float(played['daily_ret'].mean()),
                       'trades_per_day': float(trades.mean())},
        'sessions': sessions,
    }
    path = OUT / f'viewer_{variant}_{market_name}_{year}.json'
    path.write_text(json.dumps(out, separators=(',', ':')))
    print('wrote', path, f'{path.stat().st_size / 1e6:.2f} MB', 'seed', seed, list(picks))


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--variant', default='ind')
    ap.add_argument('--market', default='perp')
    ap.add_argument('--year', type=int, default=2026)
    a = ap.parse_args()
    main(a.variant, a.market, a.year)
