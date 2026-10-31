"""Round 7 (prereg/ROUND7_PREREG.md): a price-only 4h rule that an hourly IBIT log can run.

    python3 flash_ibit.py stage1 --src ../../data_src/btcmin    # historical check on BTC, read once
    python3 flash_ibit.py freeze --src ../../data_src/btcmin    # train the frozen model (only if stage 1 passes)
    python3 flash_ibit.py step --quote '{"price":43.58,"bid":43.55,"ask":43.58,"asof":"2026-10-30T23:26:34Z"}'
    python3 flash_ibit.py status

`step` is what the hourly routine calls with one Equibles IBIT quote. It appends the quote, closes a
position whose 4 hours are up (sell at the bid), and opens one if the frozen model's signal is in its
top 10% during New York 08:00-16:00 on a weekday (buy at the ask). Paper only.
"""
import argparse, hashlib, json
from pathlib import Path
import numpy as np
import pandas as pd
import lightgbm as lgb

from data import OUT, load_minutes

HERE = Path(__file__).parent
PAPER = HERE / 'paper'
MODEL = PAPER / 'flash_ibit_model.txt'
META = PAPER / 'flash_ibit_meta.json'
QUOTES = PAPER / 'ibit_quotes.csv'
LEDGER = PAPER / 'ibit_ledger.csv'
NY = 'America/New_York'
HOLD = 4
TOL = pd.Timedelta(minutes=20)
COVER = 0.10
NOTIONAL = 10_000.0
LGB = dict(objective='regression', learning_rate=0.03, num_leaves=31, min_data_in_leaf=500, feature_fraction=0.7,
           bagging_fraction=0.7, bagging_freq=1, seed=7, verbose=-1, num_threads=4)
N_TREES = 300
NAMES = ['r1', 'r2', 'r3', 'r4', 'vol4', 'pos4', 'z1', 'z2', 'z3', 'z4']


def btc_hourly(src):
    """Last BTC trade at or before each whole hour (Bitstamp minutes; NaN where the minute is missing)."""
    df, _, _ = load_minutes(src)
    ts = pd.to_datetime(df.timestamp, unit='s', utc=True) + pd.Timedelta(minutes=1)  # minute bar closes 1 min later
    s = pd.Series(df.close.values, index=ts)
    s = s[s.index.minute == 0]
    return s.reindex(pd.date_range(s.index[0], s.index[-1], freq='h'))


def inputs(P):
    """P: price snapshots on a regular hourly index, NaN where missing. Same code live and in the backtest."""
    lp = np.log(P)
    f = pd.DataFrame(index=P.index)
    for k in range(1, HOLD + 1):
        f[f'r{k}'] = lp - lp.shift(k)
    one = lp.diff()
    f['vol4'] = pd.concat([one.shift(i) for i in range(4)], axis=1).std(axis=1, skipna=False, ddof=0)
    win = pd.concat([P.shift(i) for i in range(HOLD + 1)], axis=1)
    lo, hi = win.min(axis=1, skipna=False), win.max(axis=1, skipna=False)
    f['pos4'] = ((P - lo) / (hi - lo)).where(hi > lo, 0.5)
    v = f.vol4.clip(lower=1e-4)
    for k in range(1, HOLD + 1):
        f[f'z{k}'] = f[f'r{k}'] / v
    return f[NAMES]


def decision_hours(idx):
    t = idx.tz_convert(NY)
    return (t.weekday < 5) & (t.hour >= 8) & (t.hour <= 16) & (t.minute == 0)


def fit(X, y):
    ok = X.notna().all(axis=1) & y.notna()
    m = lgb.train(LGB, lgb.Dataset(X[ok].values, y[ok].values), N_TREES)
    last = X[ok].index[-1] - pd.Timedelta(days=365)
    tail = X[ok & (X.index > last)]
    return m, float(np.quantile(m.predict(tail.values), 1 - COVER))


def target(P, X):
    return (np.log(P).shift(-HOLD) - np.log(P)) / X.vol4.clip(lower=1e-4)


def trade(P, X, pred, thr, mask):
    """One position at a time, long only, hold exactly 4h. Returns entry times and gross log returns."""
    fwd = (np.log(P).shift(-HOLD) - np.log(P)).values
    sig = mask & (pred > thr) & ~np.isnan(fwd)
    out, busy = [], -1
    for i in np.where(sig)[0]:
        if i >= busy:
            out.append((X.index[i], fwd[i])); busy = i + HOLD
    return out


def stage1(src):
    P = btc_hourly(src)
    X = inputs(P)
    y = target(P, X)
    tr = X.index < pd.Timestamp('2025-01-01', tz='UTC')
    te = (X.index >= pd.Timestamp('2025-01-01', tz='UTC')) & (X.index < pd.Timestamp('2026-10-25', tz='UTC'))
    m, thr = fit(X[tr], y[tr])
    ok = X.notna().all(axis=1).values & te & decision_hours(X.index)
    pred = np.full(len(X), -np.inf)
    pred[ok] = m.predict(X[ok].values)
    trades = trade(P, X, pred, thr, ok)
    g = np.array([r for _, r in trades])
    fwd = (np.log(P).shift(-HOLD) - np.log(P)).values
    pool = np.where(ok & ~np.isnan(fwd))[0]
    rng = np.random.default_rng(7)
    plac = np.array([fwd[rng.choice(pool, len(g), replace=False)].mean() for _ in range(200)])
    res = {
        'trades': int(len(g)), 'gross_bp': 1e4 * float(g.mean()), 'hit_pct': 100 * float((g > 0).mean()),
        'net10_bp': 1e4 * float(g.mean()) - 20, 'net25_bp': 1e4 * float(g.mean()) - 50,
        'placebo_pct': 100 * float((plac < g.mean()).mean()), 'drift_bp': 1e4 * float(np.nanmean(fwd[pool])),
        'threshold': thr, 'by_year': {str(yr): {'trades': int(len(v)), 'gross_bp': 1e4 * float(np.mean(v))}
                                      for yr, v in pd.Series(g, index=[t.year for t, _ in trades]).groupby(level=0)},
        'decision_rows': int(ok.sum()),
    }
    res['passes'] = res['gross_bp'] >= 10 and res['placebo_pct'] >= 90
    (OUT / 'flash_ibit_stage1.json').write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


def freeze(src):
    s1 = json.loads((OUT / 'flash_ibit_stage1.json').read_text())
    if not s1['passes']:
        raise SystemExit('stage 1 failed: the forward log does not start (prereg/ROUND7_PREREG.md)')
    P = btc_hourly(src)
    P = P[P.index < pd.Timestamp('2026-10-25', tz='UTC')]
    X = inputs(P)
    m, thr = fit(X, target(P, X))
    PAPER.mkdir(exist_ok=True)
    m.save_model(str(MODEL))
    sha = hashlib.sha256(MODEL.read_bytes()).hexdigest()
    META.write_text(json.dumps({'threshold': thr, 'model_sha256': sha, 'trained_through': '2026-10-24',
                                'inputs': NAMES, 'hold_hours': HOLD, 'notional_usd': NOTIONAL}, indent=1))
    print('froze', MODEL, sha, 'threshold', thr)


def read(path, cols):
    return pd.read_csv(path) if path.exists() else pd.DataFrame(columns=cols)


def step(quote):
    meta = json.loads(META.read_text())
    if hashlib.sha256(MODEL.read_bytes()).hexdigest() != meta['model_sha256']:
        raise SystemExit('model file changed since it was frozen')
    asof = pd.Timestamp(quote['asof']).tz_convert('UTC')
    hour = asof.round('h')
    q = read(QUOTES, ['hour', 'asof', 'price', 'bid', 'ask'])
    if quote.get('stale') or abs(asof - hour) > TOL:
        print('quote not usable for an hourly snapshot:', asof); return
    if (q.hour == str(hour)).any():
        print('hour already logged:', hour); return
    q.loc[len(q)] = [str(hour), str(asof), quote['price'], quote['bid'], quote['ask']]
    q.to_csv(QUOTES, index=False)

    led = read(LEDGER, ['entry_hour', 'entry_ask', 'pred', 'exit_hour', 'exit_bid', 'net_bp', 'gross_mid_bp'])
    qi = q.assign(hour=pd.to_datetime(q.hour, utc=True)).set_index('hour')
    is_open = len(led) and pd.isna(led.exit_hour.iloc[-1])
    if is_open and hour >= pd.Timestamp(led.entry_hour.iloc[-1]) + pd.Timedelta(hours=HOLD):
        e = led.index[-1]
        led.loc[e, ['exit_hour', 'exit_bid']] = [str(hour), quote['bid']]
        led.loc[e, 'net_bp'] = 1e4 * np.log(quote['bid'] / led.loc[e, 'entry_ask'])
        ent = qi.loc[pd.Timestamp(led.loc[e, 'entry_hour'])]
        led.loc[e, 'gross_mid_bp'] = 1e4 * np.log((quote['bid'] + quote['ask']) / (ent.bid + ent.ask))
        is_open = False
        print('closed', led.loc[e].to_dict())
    if not is_open and decision_hours(pd.DatetimeIndex([hour]))[0]:
        P = qi.price.astype(float).reindex(pd.date_range(hour - pd.Timedelta(hours=HOLD), hour, freq='h'))
        x = inputs(P).iloc[[-1]]
        if x.notna().all(axis=1).iloc[0]:
            m = lgb.Booster(model_file=str(MODEL))
            p = float(m.predict(x.values)[0])
            print('decision', hour, 'pred', round(p, 4), 'threshold', round(meta['threshold'], 4))
            if p > meta['threshold']:
                led.loc[len(led)] = [str(hour), quote['ask'], p, None, None, None, None]
                print('opened long at the ask', quote['ask'])
        else:
            print('missing lookback snapshots: no decision at', hour)
    led.to_csv(LEDGER, index=False)


def status():
    led = read(LEDGER, ['net_bp'])
    done = led.dropna(subset=['net_bp']) if len(led) else led
    q = read(QUOTES, ['hour'])
    print(f'{len(q)} hourly quotes logged; {len(done)} closed trades; open: {bool(len(led) and pd.isna(led.exit_hour.iloc[-1]))}')
    if len(done):
        n = done.net_bp.astype(float)
        print(f'net per trade {n.mean():+.1f} bp at the real spread; hit {100 * (n > 0).mean():.0f}%; '
              f'P&L ${NOTIONAL * (np.exp(n / 1e4) - 1).sum():+.2f} on ${NOTIONAL:,.0f} paper')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['stage1', 'freeze', 'step', 'status'])
    ap.add_argument('--src', default='../../data_src/btcmin')
    ap.add_argument('--quote')
    a = ap.parse_args()
    {'stage1': lambda: stage1(a.src), 'freeze': lambda: freeze(a.src),
     'step': lambda: step(json.loads(a.quote)), 'status': status}[a.cmd]()
