"""Walk-forward protocol: expanding train window, 183-day validation for checkpoint
selection, then one untouched calendar-year test. Hyperparameters are frozen in
PROTOCOL below before any test year is evaluated.

    python3 walkforward.py train      # trains every (market, fold, seed) in parallel
    python3 walkforward.py evaluate   # scores ensembles and baselines on the test years
"""
import argparse, json, os, sys, time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from pathlib import Path
import numpy as np

from data import OUT, SWING_CHUNK_DAYS, load_sessions, load_sessions_swing, load_sessions_v2

PROTOCOL = {
    'frozen_utc': '2026-10-30',
    'markets': ['spot', 'perp'],
    'test_years': [2021, 2022, 2023, 2024, 2025, 2026],
    'train_start': '2018-01-01',
    'val_days': 183,
    'seeds': [11, 22, 33],
    'decision': 'greedy action of the seed-averaged policy probabilities',
    'checkpoint': 'highest validation mean daily log return, evaluated every 10 updates',
    'primary_metric': 'mean daily net return vs always-flat and vs intraday buy-and-hold, block-bootstrap 95% CI',
}
MODELS = OUT / 'models'
# The deep arm was added after the compact arm's first results, at Soren's request, as a
# capacity test. Its settings are fixed here before any of its test years are scored.
VARIANTS = {
    'mlp': {'models': OUT / 'models', 'loader': load_sessions, 'arch': 'mlp', 'seeds': [11, 22, 33], 'updates': 400},
    'deep': {'models': OUT / 'models_deep', 'loader': load_sessions_v2, 'arch': 'deep', 'seeds': [11, 22], 'updates': 150, 'epochs': 3},
    # Round 2 (2026-10-30, after the compact results): attack the edge-vs-cost gap.
    # Hourly decisions and/or resting limit orders. The winner is chosen by validation score
    # only (choose_arm.py) and frozen in artifacts/selection.json before its test years are scored.
    'h1': {'models': OUT / 'models_h1', 'loader': load_sessions, 'arch': 'mlp', 'seeds': [11, 22, 33], 'updates': 400,
           'cadence': 4, 'markets': ['spot', 'perp', 'spot_maker', 'perp_maker']},
    'm15_maker': {'models': OUT / 'models_m15_maker', 'loader': load_sessions, 'arch': 'mlp', 'seeds': [11, 22, 33],
                  'updates': 400, 'markets': ['spot_maker', 'perp_maker']},
    # Round 3 (2026-10-30, Soren chose longer holds): 4-hour decisions inside 30-day chunks, so a
    # position can be held for weeks and each round trip is spread over a bigger expected move.
    # Same PPO settings; folds are purged so no train/validation chunk overlaps what follows it.
    'swing': {'models': OUT / 'models_swing', 'loader': load_sessions_swing, 'arch': 'mlp', 'seeds': [11, 22, 33],
              'updates': 400, 'markets': ['spot_swing', 'perp_swing'], 'period_days': SWING_CHUNK_DAYS},
    # Round 4 (2026-10-30, Soren: retry without fees, port the long-term indicators): the
    # Trend/Breakout/Pullback Guard inputs at 15-minute and 4-hour speed, trained at real
    # fees and at zero fees. Settings frozen here before any of its test years are scored.
    'ind': {'models': OUT / 'models_ind', 'loader': lambda: __import__('indicators').load_sessions_ind(),
            'arch': 'mlp', 'seeds': [11, 22, 33], 'updates': 400,
            'markets': ['spot', 'perp', 'spot_nofee', 'perp_nofee']},
    # Round 5 (2026-10-30, Soren: use all Mosaic data plus online data): adds funding, basis,
    # open interest, perp volume, liquidations, taker ratio, Coinbase premium, exchange flows,
    # Fear & Greed and the Andean/Laguerre/McGinley indicators (ext_data.py). Sessions
    # 2020-01-08 to 2026-03-05 (the span of the hourly derivatives mirror). Frozen before scoring.
    'ext': {'models': OUT / 'models_ext', 'loader': lambda: __import__('ext_data').load_sessions_ext(),
            'arch': 'mlp', 'seeds': [11, 22, 33], 'updates': 400,
            'markets': ['spot', 'perp', 'spot_nofee', 'perp_nofee']},
}


def sessions_for(variant):
    from env import resample
    V = VARIANTS[variant]
    return resample(V['loader'](), V.get('cadence', 1))


def folds(days, period_days=1):
    """With multi-day chunks (period_days > 1) every train and validation chunk must end before the
    next window starts, and the test uses back-to-back, non-overlapping chunks."""
    d = np.array(days, dtype='datetime64[D]')
    if period_days > 1:
        return swing_folds(d, np.timedelta64(period_days, 'D'))
    out = []
    for y in PROTOCOL['test_years']:
        t0 = np.datetime64(f'{y}-01-01'); t1 = np.datetime64(f'{y + 1}-01-01')
        v0 = t0 - np.timedelta64(PROTOCOL['val_days'], 'D')
        out.append({'year': y,
                    'train': np.where((d >= np.datetime64(PROTOCOL['train_start'])) & (d < v0))[0],
                    'val': np.where((d >= v0) & (d < t0))[0],
                    'test': np.where((d >= t0) & (d < t1))[0]})
    return out


def swing_folds(d, L):
    out = []
    for y in PROTOCOL['test_years']:
        t0 = np.datetime64(f'{y}-01-01'); t1 = np.datetime64(f'{y + 1}-01-01')
        v0 = t0 - np.timedelta64(PROTOCOL['val_days'], 'D')
        test, k = [], np.searchsorted(d, t0)
        while k < len(d) and d[k] + L <= t1:  # inside the year, so pooled test years never overlap
            test.append(k)
            k = np.searchsorted(d, d[k] + L)
        out.append({'year': y,
                    'train': np.where((d >= np.datetime64(PROTOCOL['train_start'])) & (d + L <= v0))[0],
                    'val': np.where((d >= v0) & (d + L <= t0))[0],
                    'test': np.array(test)})
    return out


def fold_list(variant, days):
    return folds(days, VARIANTS[variant].get('period_days', 1))


def job(args):
    market, year, seed, variant = args
    import torch
    from ppo import train, summary, PPOConfig
    V = VARIANTS[variant]; MODELS = V['models']
    sess = sessions_for(variant); f = {x['year']: x for x in fold_list(variant, sess['days'])}[year]
    path = MODELS / f'{market}_{year}_s{seed}.pt'
    logf = open(MODELS / f'{market}_{year}_s{seed}.log', 'w')
    log = lambda s: (logf.write(s + '\n'), logf.flush())
    t0 = time.time()
    model, hist, meta = train(market, sess, f['train'], f['val'], seed, PPOConfig(arch=V['arch'], updates=V['updates'], epochs=V.get('epochs', 4)), log=log)
    meta.update({'market': market, 'year': year, 'seed': seed, 'seconds': time.time() - t0,
                 'train_days': len(f['train']), 'val_days': len(f['val']),
                 'train_range': [str(sess['days'][f['train'][0]]), str(sess['days'][f['train'][-1]])],
                 'val_range': [str(sess['days'][f['val'][0]]), str(sess['days'][f['val'][-1]])],
                 'summary': summary(model)})
    torch.save(model.state_dict(), path)
    (MODELS / f'{market}_{year}_s{seed}.json').write_text(json.dumps({'meta': meta, 'history': hist}))
    return f'{market} {year} s{seed}: best upd {meta["best_update"]} val {meta["best_val_day_log_pct"]:+.3f}%/day ({meta["seconds"]:.0f}s)'


def train_all(workers, only=None, variant='mlp'):
    V = VARIANTS[variant]; MODELS = V['models']
    MODELS.mkdir(parents=True, exist_ok=True)
    (OUT / 'protocol.json').write_text(json.dumps({**PROTOCOL, 'variants': {k: {kk: str(vv) for kk, vv in v.items() if kk != 'loader'} for k, v in VARIANTS.items()}}, indent=2))
    jobs = [(m, y, s, variant) for m in V.get('markets', PROTOCOL['markets']) for y in PROTOCOL['test_years'] for s in V['seeds']
            if not (MODELS / f'{m}_{y}_s{s}.pt').exists() and (only is None or m == only)]
    print(f'{len(jobs)} jobs', flush=True)
    with ProcessPoolExecutor(workers) as ex:
        for r in ex.map(job, jobs):
            print(r, flush=True)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['train', 'evaluate'])
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--market')
    ap.add_argument('--variant', default='mlp', choices=list(VARIANTS))
    a = ap.parse_args()
    if a.cmd == 'train':
        train_all(a.workers, a.market, a.variant)
    else:
        from evaluate import main
        main(a.variant)
