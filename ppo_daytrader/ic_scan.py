"""Which inputs rank short-term BTC moves? Spearman IC of every input against the forward
return from the decision's fill (bar j open) over 15 minutes, 1 hour, 4 hours and to the
session close, by period. Writes artifacts/ic_scan.json."""
import json
import numpy as np
from data import OUT, FEATURES
from indicators import IND_FEATURES, load_sessions_ind

HORIZONS = {'15m': 1, '1h': 4, '4h': 16, 'to_close': None}
PERIODS = [('2018-2020', '2018-01-01', '2021-01-01')] + [(str(y), f'{y}-01-01', f'{y + 1}-01-01') for y in range(2021, 2027)]


def rank(v):
    return np.argsort(np.argsort(v)).astype(np.float64)


def main():
    s = load_sessions_ind(); names = FEATURES + IND_FEATURES
    o, c = s['open'], s['close']; days = s['days'].astype('datetime64[D]')
    X = np.asarray(s['obs'])
    out = {'names': names, 'horizons': list(HORIZONS), 'periods': [p[0] for p in PERIODS], 'ic': {}}
    for hk, h in HORIZONS.items():
        T = o.shape[1]
        js = np.arange(T) if h is None else np.arange(T - h + 1)  # forward window must stay in the session
        end = np.full(len(js), T - 1) if h is None else js + h - 1
        fwd = np.log(c[:, end] / o[:, js])
        for pname, a, b in PERIODS:
            m = (days >= np.datetime64(a)) & (days < np.datetime64(b))
            # subsample decisions so overlapping horizons do not inflate the sample
            step = 1 if h in (1, None) else h
            sel = js[::step] if h is not None else js[::8]
            y = fwd[m][:, ::step] if h is not None else fwd[m][:, ::8]
            ry = rank(y.reshape(-1))
            for k, n in enumerate(names):
                x = X[m][:, sel, k].reshape(-1)
                ic = float(np.corrcoef(rank(x), ry)[0, 1]) if x.std() > 0 else 0.0
                out['ic'].setdefault(n, {}).setdefault(hk, {})[pname] = round(ic, 4)
    (OUT / 'ic_scan.json').write_text(json.dumps(out))
    for hk in HORIZONS:
        print('\n==', hk, ' '.join(f'{p[0]:>9}' for p in PERIODS), ' sign-consistent')
        rows = []
        for n in names:
            v = [out['ic'][n][hk][p[0]] for p in PERIODS]
            cons = sum(np.sign(x) == np.sign(v[0]) for x in v[1:])
            rows.append((abs(np.mean(v[1:])), n, v, cons))
        for _, n, v, cons in sorted(rows, reverse=True)[:14]:
            print(f'{n:22s}', ' '.join(f'{x:+9.3f}' for x in v), f'   {cons}/6')


if __name__ == '__main__':
    main()
