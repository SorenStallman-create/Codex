"""The Trading Lab's long-term indicators (Trend Guard, Breakout Guard, Pullback Guard from
focused_engine.py: SMA 20/50, EMA200 regime, Donchian channels, RSI14, ATR14) rebuilt as
causal network inputs at two speeds:

  m15_*  the same formulas on 15-minute bars (the short-term equivalent)
  h4_*   the original 4-hour versions, taken from the last *completed* 4-hour bar

Every value at 15-minute bar i uses only bars <= i. Writes artifacts/sessions_ind_obs.npy
[days, 96, 17 base + len(IND_FEATURES)], row j = inputs at the close of bar j-1.

    python3 indicators.py --src ../data_src/btcmin
"""
import argparse
import numpy as np
import pandas as pd

from data import OUT, BARS_PER_DAY, build_bars, load_minutes, load_sessions

SPEEDS = ('m15', 'h4')
PER_SPEED = ['sma20_50', 'close_ema200', 'ema200_slope', 'regime', 'breakout20', 'breakdown10',
             'rsi14', 'atr_rel', 'trend_guard', 'breakout_guard', 'pullback_guard']
IND_FEATURES = [f'{s}_{n}' for s in SPEEDS for n in PER_SPEED] + ['vwap_dev', 'obv_slope']


def rsi(c, n=14):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def guard_frame(b):
    """focused_engine.features + rules, vectorized. Columns are scale-free so one network can read both speeds."""
    c, h, l = b.close, b.high, b.low
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    sma20, sma50 = c.rolling(20).mean(), c.rolling(50).mean()
    ema200 = c.ewm(span=200, adjust=False).mean()
    hi20, lo10 = h.shift(1).rolling(20).max(), l.shift(1).rolling(10).min()
    r = rsi(c)
    regime = (c > ema200) & (ema200 > ema200.shift(6))
    f = pd.DataFrame(index=b.index)
    f['sma20_50'] = (sma20 - sma50) / atr
    f['close_ema200'] = (c - ema200) / atr / 4
    f['ema200_slope'] = (ema200 - ema200.shift(6)) / atr
    f['regime'] = regime.astype(float)
    f['breakout20'] = (c - hi20) / atr
    f['breakdown10'] = (c - lo10) / atr / 2
    f['rsi14'] = (r - 50) / 25
    f['atr_rel'] = np.log(atr / atr.rolling(200, min_periods=50).mean())
    # the three rule states exactly as the long-term bots would hold them (entry true -> 1, exit -> 0)
    f['trend_guard'] = ((sma20 > sma50) & regime).astype(float)
    f['breakout_guard'] = hold_state((c > hi20) & regime, (c < lo10) | (c < ema200))
    f['pullback_guard'] = hold_state((r.shift() <= 35) & (r > 35) & regime, (r >= 55) | (c < ema200))
    return f


def hold_state(entry, exit_):
    s = np.zeros(len(entry)); on = 0.0
    for i, (e, x) in enumerate(zip(entry.values, exit_.values)):
        on = 0.0 if (on and x) else (1.0 if e else on)
        s[i] = on
    return pd.Series(s, index=entry.index)


def indicator_frame(bars):
    f15 = guard_frame(bars).add_prefix('m15_')
    b4 = bars.resample('4h', label='left', closed='left').agg(
        {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna()
    f4 = guard_frame(b4).add_prefix('h4_')
    # a 4-hour bar labelled t is complete at t+4h; the 15-minute bar closing then is labelled t+3h45
    f4.index = f4.index + pd.Timedelta(hours=3, minutes=45)
    f = f15.join(f4.reindex(bars.index, method='ffill'))
    # session VWAP deviation and on-balance-volume slope (the volume side of the old 15-minute screen)
    pv = (bars.close * bars.volume).groupby(bars.day).cumsum()
    vwap = pv / bars.volume.groupby(bars.day).cumsum().replace(0, np.nan)
    atr = (bars.high - bars.low).rolling(96).mean()
    f['vwap_dev'] = (bars.close - vwap) / atr / 4
    obv = (np.sign(bars.close.diff()).fillna(0) * bars.volume).cumsum()
    f['obv_slope'] = (obv - obv.shift(16)) / (bars.volume.rolling(96 * 5, min_periods=96).mean() * 16)
    return f[IND_FEATURES].replace([np.inf, -np.inf], np.nan).fillna(0.0).clip(-6, 6).astype(np.float32)


def build(src):
    df, outages, _ = load_minutes(src)
    bars, _ = build_bars(df, outages)
    f = indicator_frame(bars)
    base = load_sessions()
    pos = {t: i for i, t in enumerate(bars.index)}
    i0 = np.array([pos[pd.Timestamp(str(d), tz='UTC')] for d in base['days']])
    rows = i0[:, None] + np.arange(BARS_PER_DAY)[None, :] - 1  # last completed bar before each decision
    obs = np.concatenate([base['obs'], f.values[rows]], -1)
    np.save(OUT / 'sessions_ind_obs.npy', obs.astype(np.float32))
    print('indicator obs', obs.shape)


def load_sessions_ind():
    s = load_sessions()
    s['obs'] = np.load(OUT / 'sessions_ind_obs.npy', mmap_mode='r')
    return s


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default='../data_src/btcmin')
    build(ap.parse_args().src)
