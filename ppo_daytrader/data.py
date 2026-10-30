"""Bitstamp BTC/USD minute candles -> audited 15-minute UTC-day sessions and causal features.

Source: github.com/ff137/bitstamp-btcusd-minute-data (rows through 2025-01-07 are
"Zielak (mczielinski), Bitcoin Historical Data, Kaggle", CC BY-SA 4.0).
Exchange APIs are not reachable from the research container, so the public Git
mirror is the audited input. Every feature at bar i uses only bars <= i.
"""
import argparse, hashlib, json, datetime as dt
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'artifacts'
BAR_MIN = 15
BARS_PER_DAY = 24 * 60 // BAR_MIN  # 96
START = '2018-01-01'
FEATURES = [
    # price action, scale-free (log returns divided by trailing volatility)
    'r1', 'r4', 'r16', 'r64', 'r_day', 'r_prev_day',
    'dev16', 'dev96',
    # volatility regime
    'vol16_rel', 'vol96_rel', 'range_rel',
    # participation
    'volu_z', 'clv',
    # calendar (UTC)
    'tod_sin', 'tod_cos', 'dow_sin', 'dow_cos',
]
STATE = ['position', 'time_left', 'unreal']  # appended by the environment


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def load_minutes(src):
    src = Path(src)
    files = [src / 'data/historical/btcusd_bitstamp_1min_2012-2025.csv.gz',
             src / 'data/updates/btcusd_bitstamp_1min_latest.csv']
    frames = [pd.read_csv(f) for f in files]
    df = pd.concat(frames).drop_duplicates('timestamp', keep='last').sort_values('timestamp')
    df = df[df.timestamp >= pd.Timestamp(START, tz='UTC').timestamp()]
    outages = pd.read_csv(src / 'data/provenance/btcusd_bitstamp_1min.csv')
    return df.reset_index(drop=True), outages, {f.name: sha256(f) for f in files}


def build_bars(df, outages):
    df = df.copy()
    df['ts'] = pd.to_datetime(df.timestamp, unit='s', utc=True)
    df['day'] = df.ts.dt.floor('D')
    # Quality: a day is usable only if all 1440 minutes exist, no zero-volume run >= 60 min,
    # no flagged outage overlaps it, and OHLC is internally consistent.
    zero = (df.volume <= 0).astype(int).values
    run = np.zeros(len(zero), int)
    for i in range(len(zero)):
        run[i] = run[i - 1] + 1 if zero[i] and i else zero[i]
    df['zero_run'] = run
    by_day = df.groupby('day')
    q = pd.DataFrame({
        'minutes': by_day.size(),
        'max_zero_run': by_day.zero_run.max(),
        'zero_frac': by_day.volume.apply(lambda v: float((v <= 0).mean())),
        'bad_ohlc': by_day.apply(lambda g: int(((g.low > g[['open', 'close']].min(axis=1)) |
                                                (g.high < g[['open', 'close']].max(axis=1)) |
                                                (g.low <= 0)).sum()), include_groups=False),
    })
    flagged = set()
    for _, o in outages.iterrows():
        d0 = pd.Timestamp(int(o.start_timestamp), unit='s', tz='UTC').floor('D')
        d1 = pd.Timestamp(int(o.end_timestamp), unit='s', tz='UTC').floor('D')
        flagged.update(pd.date_range(d0, d1, freq='D'))
    q['outage_flag'] = q.index.isin(flagged)
    q['usable'] = (q.minutes == 1440) & (q.max_zero_run < 60) & (q.bad_ohlc == 0) & ~q.outage_flag

    bars = df.set_index('ts').resample(f'{BAR_MIN}min', label='left', closed='left').agg(
        open=('open', 'first'), high=('high', 'max'), low=('low', 'min'),
        close=('close', 'last'), volume=('volume', 'sum')).dropna()
    bars['day'] = bars.index.floor('D')
    return bars, q


def features(bars):
    """All rolling windows end at the current bar's close: decision at open of bar i+1 sees row i."""
    c = bars.close
    lr = np.log(c).diff().fillna(0.0)
    vol96 = lr.rolling(96 * 5, min_periods=96).std().bfill()  # ~5-day trailing bar vol
    vol16 = lr.rolling(16, min_periods=4).std().bfill()
    z = lambda x: (x / vol96).clip(-8, 8)
    f = pd.DataFrame(index=bars.index)
    f['r1'] = z(lr)
    f['r4'] = z(lr.rolling(4).sum()) / 2
    f['r16'] = z(lr.rolling(16).sum()) / 4
    f['r64'] = z(lr.rolling(64).sum()) / 8
    day_open = bars.groupby('day').open.transform('first')
    f['r_day'] = z(np.log(c / day_open)) / 4
    daily_close = bars.groupby('day').close.last()
    daily_ret = np.log(daily_close).diff()
    f['r_prev_day'] = (bars.day.map(daily_ret.shift(1)).values / (vol96 * np.sqrt(96)))
    f['dev16'] = z(np.log(c / c.rolling(16).mean())) / 2
    f['dev96'] = z(np.log(c / c.rolling(96).mean())) / 4
    f['vol16_rel'] = np.log(vol16 / vol96)
    f['vol96_rel'] = np.log(vol96 / lr.rolling(96 * 30, min_periods=96 * 5).std().bfill())
    rng = np.log(bars.high / bars.low)
    f['range_rel'] = np.log((rng + 1e-6) / (rng.rolling(96 * 5, min_periods=96).mean() + 1e-6))
    lv = np.log1p(bars.volume)
    f['volu_z'] = (lv - lv.rolling(96 * 5, min_periods=96).mean()) / (lv.rolling(96 * 5, min_periods=96).std() + 1e-6)
    f['clv'] = ((c - bars.low) / (bars.high - bars.low).replace(0, np.nan)).fillna(0.5) * 2 - 1
    # calendar for the *next* bar (the one the decision will hold): known in advance, not lookahead
    nxt = bars.index + pd.Timedelta(minutes=BAR_MIN)
    tod = (nxt.hour * 60 + nxt.minute) / 1440.0
    f['tod_sin'], f['tod_cos'] = np.sin(2 * np.pi * tod), np.cos(2 * np.pi * tod)
    dow = nxt.dayofweek / 7.0
    f['dow_sin'], f['dow_cos'] = np.sin(2 * np.pi * dow), np.cos(2 * np.pi * dow)
    f = f.replace([np.inf, -np.inf], np.nan).fillna(0.0).clip(-6, 6)
    return f[FEATURES].astype(np.float32)


def build(src):
    OUT.mkdir(exist_ok=True)
    df, outages, hashes = load_minutes(src)
    bars, q = build_bars(df, outages)
    f = features(bars)
    days = sorted(q.index[q.usable])
    # a session needs its 96 own bars and a previous complete bar for the first decision
    pos = {t: i for i, t in enumerate(bars.index)}
    keep, idx = [], []
    for d in days:
        i0 = pos.get(d)
        # skip the warm-up week so no rolling window is back-filled from the future
        if i0 is None or i0 < 96 * 7 or i0 + BARS_PER_DAY > len(bars):
            continue
        if bars.index[i0 + BARS_PER_DAY - 1] != d + pd.Timedelta(minutes=BAR_MIN * (BARS_PER_DAY - 1)):
            continue
        keep.append(d); idx.append(i0)
    idx = np.array(idx)
    rows = idx[:, None] + np.arange(BARS_PER_DAY)[None, :]
    o = bars.open.values[rows]; c = bars.close.values[rows]
    obs = f.values[rows - 1]  # decision for bar j sees features at close of bar j-1
    np.savez_compressed(OUT / 'sessions.npz', days=np.array([str(d.date()) for d in keep]),
                        open=o.astype(np.float64), close=c.astype(np.float64),
                        obs=obs.astype(np.float32), features=np.array(FEATURES))
    audit = {
        'source': 'github.com/ff137/bitstamp-btcusd-minute-data (Bitstamp BTC/USD 1m)',
        'attribution': 'Zielak (mczielinski), Bitcoin Historical Data, Kaggle (CC BY-SA 4.0) for rows <= 2025-01-07',
        'input_sha256': hashes, 'minutes_loaded': int(len(df)),
        'first_minute_utc': str(pd.Timestamp(int(df.timestamp.iloc[0]), unit='s', tz='UTC')),
        'last_minute_utc': str(pd.Timestamp(int(df.timestamp.iloc[-1]), unit='s', tz='UTC')),
        'bar_minutes': BAR_MIN, 'sessions_usable': len(keep),
        'days_seen': int(len(q)), 'days_excluded': int((~q.usable).sum()),
        'excluded_examples': [str(d.date()) for d in q.index[~q.usable][:40]],
        'feature_names': FEATURES,
        'causality': 'obs for the decision held over bar j are features at close of bar j-1; fills at open of bar j',
        'created_utc': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
    }
    (OUT / 'data_audit.json').write_text(json.dumps(audit, indent=2))
    print(json.dumps({k: v for k, v in audit.items() if k != 'excluded_examples'}, indent=2))


def load_sessions():
    z = np.load(OUT / 'sessions.npz', allow_pickle=False)
    return {k: z[k] for k in z.files}


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default='../data_src/btcmin', help='clone of ff137/bitstamp-btcusd-minute-data')
    ap.add_argument('--v2', action='store_true', help='also build the sequence + VIX inputs for the deep model')
    a = ap.parse_args()
    build(a.src)
    if a.v2:
        build_v2(a.src)


# ---------- v2 inputs: raw bar sequence + Equibles VIX context ----------
SEQ_LEN = 64  # 16 hours of 15-minute bars
SEQ_CHANNELS = ['ret', 'high', 'low', 'gap', 'volume', 'range']
CONTEXT = ['vix_z', 'vix_chg']


def seq_channels(bars):
    """Per-bar OHLCV shape, scaled by trailing volatility; row i uses bars <= i only."""
    lr = np.log(bars.close).diff().fillna(0.0)
    vol = lr.rolling(96 * 5, min_periods=96).std().bfill()
    o, h, l, c = (np.log(bars[k]) for k in ('open', 'high', 'low', 'close'))
    lv = np.log1p(bars.volume)
    ch = pd.DataFrame({
        'ret': (c - o) / vol, 'high': (h - np.maximum(o, c)) / vol, 'low': (np.minimum(o, c) - l) / vol,
        'gap': (o - c.shift(1)).fillna(0.0) / vol,
        'volume': (lv - lv.rolling(96 * 5, min_periods=96).mean()) / (lv.rolling(96 * 5, min_periods=96).std() + 1e-6),
        'range': np.log((h - l + 1e-6) / ((h - l).rolling(96 * 5, min_periods=96).mean() + 1e-6)),
    }, index=bars.index)
    return ch.replace([np.inf, -np.inf], np.nan).fillna(0.0).clip(-6, 6).astype(np.float32)


def vix_context(days):
    """VIX (Equibles daily history) known before each UTC session: the latest close dated before that day."""
    v = pd.read_csv(ROOT / 'context/vix_daily_equibles.csv', parse_dates=['date']).set_index('date').close
    lv = np.log(v)
    z = (lv - lv.rolling(252, min_periods=60).mean()) / (lv.rolling(252, min_periods=60).std() + 1e-6)
    chg = lv.diff() / (lv.diff().rolling(252, min_periods=60).std() + 1e-6)
    ctx = pd.DataFrame({'vix_z': z, 'vix_chg': chg}).dropna()
    idx = ctx.index.searchsorted(pd.to_datetime(days), side='left') - 1  # strictly before the session date
    out = ctx.values[np.clip(idx, 0, None)]
    out[idx < 0] = 0.0
    return np.clip(out, -6, 6).astype(np.float32)


def build_v2(src):
    """sessions_v2.npy: [days, 96, 17 features + 2 VIX + 64x6 sequence] (memory-mapped by workers)."""
    df, outages, _ = load_minutes(src)
    bars, _ = build_bars(df, outages)
    base = load_sessions()
    pos = {t: i for i, t in enumerate(bars.index)}
    i0 = np.array([pos[pd.Timestamp(str(d), tz='UTC')] for d in base['days']])
    ch = seq_channels(bars).values
    rows = i0[:, None] + np.arange(BARS_PER_DAY)[None, :] - 1  # last completed bar before each decision
    lags = np.arange(SEQ_LEN)[::-1]
    seq = ch[rows[:, :, None] - lags[None, None, :]]  # [days, 96, 64, 6], oldest -> newest
    ctx = np.repeat(vix_context([str(d) for d in base['days']])[:, None, :], BARS_PER_DAY, 1)
    obs = np.concatenate([base['obs'], ctx, seq.transpose(0, 1, 3, 2).reshape(len(i0), BARS_PER_DAY, -1)], -1)
    np.save(OUT / 'sessions_v2_obs.npy', obs.astype(np.float32))
    print('v2 obs', obs.shape)


def load_sessions_v2():
    s = load_sessions()
    s['obs'] = np.load(OUT / 'sessions_v2_obs.npy', mmap_mode='r')
    return s
