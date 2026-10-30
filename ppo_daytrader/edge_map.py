"""Round 6 (prereg/ROUND6_PREREG.md): where, if anywhere, does a prediction beat the fee?

Walk-forward LightGBM forecasts of the vol-scaled return over 1h/4h/12h/24h/72h from every
hourly decision point, then a validation-chosen horizon and coverage (trade only the most
confident x% of predictions). Positions may be held across midnight; one position at a time.

    python3 edge_map.py --src ../../data_src/btcmin --ext ../../data_src/ext
"""
import argparse, json
import numpy as np
import pandas as pd
import lightgbm as lgb

from data import OUT, FEATURES, build_bars, features, load_minutes
from indicators import IND_FEATURES, indicator_frame
from ext_data import EXT_FEATURES, HOURLY, DAILY, hourly_frame, daily_frame, mosaic_frame
from evaluate import block_boot
from prereg_check import after_tax

HORIZONS = {'1h': 4, '4h': 16, '12h': 48, '24h': 96, '72h': 288}
COVERAGE = [0.50, 0.20, 0.10, 0.05, 0.02, 0.01]
YEARS = [2021, 2022, 2023, 2024, 2025, 2026]
TEST_END = pd.Timestamp('2026-10-25', tz='UTC')
PURGE = pd.Timedelta(days=3)
MARKETS = {'spot': dict(cost=0.0020, sides=(1,), fund8h=0.0), 'perp': dict(cost=0.0006, sides=(1, -1), fund8h=0.0001)}
LGB = dict(objective='regression', learning_rate=0.03, num_leaves=31, min_data_in_leaf=500, feature_fraction=0.7,
           bagging_fraction=0.7, bagging_freq=1, seed=7, verbose=-1, num_threads=4)
N_TREES = 300
NAMES = FEATURES + IND_FEATURES + EXT_FEATURES


def frame(src, ext):
    df, outages, _ = load_minutes(src)
    bars, _ = build_bars(df, outages)
    h = hourly_frame(ext).reindex(bars.index, method='ffill', tolerance=pd.Timedelta(hours=12))
    d = daily_frame(ext).reindex(pd.DatetimeIndex(bars.day)); d.index = bars.index
    e = h.join(d).join(mosaic_frame(bars))[EXT_FEATURES].replace([np.inf, -np.inf], np.nan).clip(-6, 6)
    e.loc[e.index < pd.Timestamp('2022-12-04', tz='UTC'), [c for c in DAILY if c not in ('fear_greed', 'fear_greed_chg7')]] = np.nan
    X = features(bars).join(indicator_frame(bars)).join(e.astype(np.float32))
    return bars, X[NAMES]


def dataset(bars, X):
    """Decision rows at hourly closes. Entry = next bar's open; exit = close H bars after the decision bar."""
    t = bars.index; o = bars.open.values; c = bars.close.values
    lr = np.log(bars.close).diff()
    vol = lr.rolling(96 * 5, min_periods=96).std().values
    rows = np.where((t.minute == 45) & (np.arange(len(t)) > 96 * 7))[0]
    out = {'i': rows, 'time': t[rows], 'X': X.values[rows].astype(np.float32), 'vol': vol[rows]}
    for k, H in HORIZONS.items():
        j = rows + H
        ok = j < len(t)
        jj = np.where(ok, j, 0)
        contiguous = ok & (t[jj] - t[rows] == pd.Timedelta(minutes=15 * H)) & (rows + 1 < len(t))
        y = np.where(contiguous, np.log(c[jj] / o[np.minimum(rows + 1, len(t) - 1)]), np.nan)
        out[f'y_{k}'] = y
    return out


def simulate(D, pred, mask, h, thr, mk, cost=None, days=None):
    """One position at a time, hold exactly h. Returns (daily log returns over `days`, trades list)."""
    M = MARKETS[mk]; cost = M['cost'] if cost is None else cost; H = HORIZONS[h]
    idx = np.where(mask & np.isfinite(D[f'y_{h}']))[0]
    busy = pd.Timestamp.min.tz_localize('UTC'); trades = []
    for k in idx:
        p = pred[k]
        side = 1 if p > thr else (-1 if (p < -thr and -1 in M['sides']) else 0)
        if not side or D['time'][k] < busy:
            continue
        exit_t = D['time'][k] + pd.Timedelta(minutes=15 * H)
        r = side * np.expm1(D[f'y_{h}'][k])
        net = np.log1p(r) + 2 * np.log1p(-cost) - M['fund8h'] * H / 32
        trades.append((exit_t.floor('D'), side, net)); busy = exit_t
    s = pd.Series(0.0, index=days)
    for d, _, net in trades:
        if d in s.index:
            s[d] += net
    return s.values, trades


def calendar(a, b):
    return pd.date_range(a.floor('D'), b.floor('D') - pd.Timedelta(days=1), freq='D', tz='UTC')


def main(src, ext):
    bars, X = frame(src, ext)
    D = dataset(bars, X)
    tm = D['time']; res = {'folds': {}, 'edge_map': {}, 'primary': {}}
    preds = {h: np.full(len(tm), np.nan) for h in HORIZONS}   # out-of-sample test predictions
    vpreds = {}
    sel = {mk: {} for mk in MARKETS}
    for y in YEARS:
        t0 = pd.Timestamp(f'{y}-01-01', tz='UTC'); t1 = min(pd.Timestamp(f'{y + 1}-01-01', tz='UTC'), TEST_END)
        v0 = t0 - pd.Timedelta(days=183)
        tr = tm < v0 - PURGE; va = (tm >= v0) & (tm < t0 - PURGE); te = (tm >= t0) & (tm < t1)
        vdays, tdays = calendar(v0, t0 - PURGE), calendar(t0, t1)
        fold = {}
        for h in HORIZONS:
            yy = D[f'y_{h}'] / (D['vol'] * np.sqrt(HORIZONS[h]))
            m = tr & np.isfinite(yy)
            model = lgb.train(LGB, lgb.Dataset(D['X'][m], np.clip(yy[m], -5, 5)), num_boost_round=N_TREES)
            pv = model.predict(D['X'][va]); pt = model.predict(D['X'][te])
            preds[h][te] = pt
            full_v = np.full(len(tm), np.nan); full_v[va] = pv; vpreds[(y, h)] = full_v
            fold[h] = {'val_ic': float(pd.Series(pv).corr(pd.Series(yy[va]), method='spearman')),
                       'test_ic': float(pd.Series(pt).corr(pd.Series(yy[te]), method='spearman'))}
        # validation-only choice of horizon and coverage, per market
        for mk in MARKETS:
            best = (0.0, None, None, None)
            grid = []
            for h in HORIZONS:
                pv = vpreds[(y, h)][va]
                for cov in COVERAGE:
                    thr = float(np.quantile(np.abs(pv), 1 - cov))
                    dlog, _ = simulate(D, vpreds[(y, h)], va, h, thr, mk, days=vdays)
                    score = float(np.mean(dlog))
                    grid.append({'h': h, 'coverage': cov, 'val_day_pct': 100 * score})
                    if score > best[0]:
                        best = (score, h, cov, thr)
            sel[mk][y] = {'h': best[1], 'coverage': best[2], 'thr': best[3], 'val_day_pct': 100 * best[0], 'grid': grid}
        res['folds'][y] = {'ic': fold, 'selection': {mk: {k: v for k, v in sel[mk][y].items() if k != 'grid'} for mk in MARKETS},
                           'grid': {mk: sel[mk][y]['grid'] for mk in MARKETS}}
        print(y, {h: (round(v['val_ic'], 3), round(v['test_ic'], 3)) for h, v in fold.items()},
              {mk: (sel[mk][y]['h'], sel[mk][y]['coverage'], round(sel[mk][y]['val_day_pct'], 3)) for mk in MARKETS}, flush=True)
    # ---- primary: the validation-selected strategy, scored once per test year ----
    rng = np.random.default_rng(7)
    for mk in MARKETS:
        daily, years, c10, c25, all_trades, plc_days = [], [], [], [], [], []
        for y in YEARS:
            t0 = pd.Timestamp(f'{y}-01-01', tz='UTC'); t1 = min(pd.Timestamp(f'{y + 1}-01-01', tz='UTC'), TEST_END)
            te = (tm >= t0) & (tm < t1); tdays = calendar(t0, t1); s = sel[mk][y]
            if s['h'] is None:
                z = np.zeros(len(tdays)); daily.append(z); c10.append(z); c25.append(z)
            else:
                d0, trades = simulate(D, preds[s['h']], te, s['h'], s['thr'], mk, days=tdays)
                daily.append(d0); all_trades += [(y, s['h']) + t[1:] for t in trades]
                c10.append(simulate(D, preds[s['h']], te, s['h'], s['thr'], mk, cost=0.0010, days=tdays)[0])
                c25.append(simulate(D, preds[s['h']], te, s['h'], s['thr'], mk, cost=0.0025, days=tdays)[0])
            years.append(np.full(len(tdays), y))
            plc_days.append((te, tdays, s, len([t for t in all_trades if t[0] == y])))
        d = np.expm1(np.concatenate(daily)); yrs = np.concatenate(years)
        # placebo: same count, horizon and long/short mix per year, random entry times
        sides = {y: [t[2] for t in all_trades if t[0] == y] for y in YEARS}
        plc = []
        for p in range(200):
            tot = []
            for (te, tdays, s, n), y in zip(plc_days, YEARS):
                z = pd.Series(0.0, index=tdays)
                if n and s['h']:
                    cand = np.where(te & np.isfinite(D[f"y_{s['h']}"]))[0]
                    for k, side in zip(rng.choice(cand, n, replace=False), sides[y]):
                        H = HORIZONS[s['h']]; r = side * np.expm1(D[f"y_{s['h']}"][k])
                        dd = (D['time'][k] + pd.Timedelta(minutes=15 * H)).floor('D')
                        if dd in z.index:
                            z[dd] += np.log1p(r) + 2 * np.log1p(-MARKETS[mk]['cost']) - MARKETS[mk]['fund8h'] * H / 32
                tot.append(z.values)
            plc.append(np.expm1(np.concatenate(tot)).mean())
        plc = np.array(plc)
        by_year = {int(y): 100 * float(d[yrs == y].mean()) for y in YEARS}
        ci = block_boot(d)
        gates = {'1_mean_ci_above_zero': ci[0] > 0, '2_placebo_rank_95': float((plc < d.mean()).mean()) >= 0.95,
                 '3_positive_years_4_of_6': sum(v > 0 for v in by_year.values()) >= 4,
                 '4_positive_at_10_and_25bp': bool(np.expm1(np.concatenate(c10)).mean() > 0 and np.expm1(np.concatenate(c25)).mean() > 0)}
        tr_net = np.array([t[3] for t in all_trades])
        res['primary'][mk] = {
            'mean_day_pct': 100 * float(d.mean()), 'ci95': [100 * x for x in ci], 'by_year_pct_day': by_year,
            'trades': len(all_trades), 'trades_per_day': len(all_trades) / len(d),
            'net_per_trade_pct': 100 * float(tr_net.mean()) if len(tr_net) else 0.0,
            'hit_rate_pct': 100 * float((tr_net > 0).mean()) if len(tr_net) else 0.0,
            'placebo_percentile': 100 * float((plc < d.mean()).mean()), 'placebo_p95_day_pct': 100 * float(np.percentile(plc, 95)),
            'cost10_day_pct': 100 * float(np.expm1(np.concatenate(c10)).mean()), 'cost25_day_pct': 100 * float(np.expm1(np.concatenate(c25)).mean()),
            'yearly_tax': after_tax(d, yrs), 'equity': np.cumprod(1 + d)[::7].round(4).tolist(),
            'selection': {y: {k: v for k, v in sel[mk][y].items() if k != 'grid'} for y in YEARS},
            'gates': {k: bool(v) for k, v in gates.items()}, 'passes_all': bool(all(gates.values())),
        }
        print(mk, json.dumps({k: v for k, v in res['primary'][mk].items() if k not in ('yearly_tax', 'equity', 'selection')}), flush=True)
    # ---- edge map (descriptive, pooled out-of-sample test predictions) ----
    hour = D['time'].hour.values; vol16 = D['X'][:, NAMES.index('vol16_rel')]
    for h in HORIZONS:
        p = preds[h]; yv = D[f'y_{h}']; m = np.isfinite(p) & np.isfinite(yv)
        p, yv, hr, vr = p[m], yv[m], hour[m], vol16[m]
        dec = np.minimum((pd.Series(p).rank(pct=True).values * 10).astype(int), 9)
        e = {'decile_mean_bp': [1e4 * float(yv[dec == k].mean()) for k in range(10)],
             'decile_hit_pct': [100 * float((yv[dec == k] > 0).mean()) for k in range(10)],
             'n': int(m.sum()), 'ic': float(pd.Series(p).corr(pd.Series(yv), method='spearman'))}
        a = np.abs(p); sgn = np.sign(p)
        cov_curve = []
        for cov in [1.0, 0.5, 0.2, 0.1, 0.05, 0.02, 0.01, 0.005]:
            sel_ = a >= np.quantile(a, 1 - cov)
            cov_curve.append({'coverage': cov, 'gross_bp': 1e4 * float((sgn * np.expm1(yv))[sel_].mean()),
                              'long_only_gross_bp': 1e4 * float(np.expm1(yv)[sel_ & (p > 0)].mean()) if (sel_ & (p > 0)).any() else None,
                              'hit_pct': 100 * float((sgn * yv > 0)[sel_].mean())})
        e['coverage_curve'] = cov_curve
        top = a >= np.quantile(a, 0.9)
        e['by_hour_top10_gross_bp'] = [1e4 * float((sgn * np.expm1(yv))[top & (hr == k)].mean()) if (top & (hr == k)).any() else None for k in range(24)]
        e['by_hour_abs_move_bp'] = [1e4 * float(np.abs(np.expm1(yv))[hr == k].mean()) for k in range(24)]
        terc = np.digitize(vr, np.quantile(vr, [1 / 3, 2 / 3]))
        e['by_vol_tercile_top10_gross_bp'] = [1e4 * float((sgn * np.expm1(yv))[top & (terc == k)].mean()) for k in range(3)]
        res['edge_map'][h] = e
        print('edge', h, 'ic', round(e['ic'], 4), 'cov', [(c['coverage'], round(c['gross_bp'], 1)) for c in cov_curve], flush=True)
    res['costs_round_trip_bp'] = {'spot': 40, 'perp': 12}
    (OUT / 'edge_map.json').write_text(json.dumps(res, default=str))


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default='../../data_src/btcmin'); ap.add_argument('--ext', default='../../data_src/ext')
    a = ap.parse_args(); main(a.src, a.ext)
