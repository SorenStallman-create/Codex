"""Round 5 inputs: derivatives, order-book, sentiment and on-chain data, plus the remaining
Mosaic (Trading Lab) indicators, aligned causally to the 15-minute sessions.

Sources (public GitHub mirrors; exchange APIs are blocked from this container):
  PietroC21/Crypto-PerpetualFutures (MIT): hourly Binance perp + spot OHLCV, funding from
      Binance/OKX/Bybit/Deribit/Gate, Binance open interest. 2020-01-01 (OI 2020-12) to 2026-03-06.
  ErcinDedeoglu/crypto-market-data (CC BY 4.0): daily liquidations, taker buy/sell ratio,
      Coinbase premium, exchange netflow, stablecoin ratio. 2022-12-03 onward.
  akpasz/btc-data: daily Crypto Fear & Greed index (alternative.me). 2018-02-01 onward.
  focused_engine/app.py (Trading Lab): Andean 35, Laguerre 0.5, McGinley 20.

Causality: an hourly row stamped t is used only from the 15-minute bar that closes at t+1h;
a daily value for date d only from sessions on d+1 or later. Sessions outside the hourly
coverage are dropped for this variant.

    python3 ext_data.py --src ../../data_src/btcmin --ext ../../data_src/ext
"""
import argparse, json
from pathlib import Path
import numpy as np
import pandas as pd

from data import OUT, BARS_PER_DAY, build_bars, load_minutes, load_sessions

HOURLY = ['fund_bp', 'fund_xvenue_bp', 'fund_z30', 'basis_bp', 'basis_chg24',
          'oi_chg1h', 'oi_chg4h', 'oi_chg24h', 'oi_z30', 'perp_spot_vol', 'perp_vol_z']
DAILY = ['fear_greed', 'fear_greed_chg7', 'liq_long_z', 'liq_short_z', 'liq_imbalance', 'taker_ratio',
         'cb_premium', 'exch_netflow_z', 'stable_ratio_z', 'onchain_avail']
MOSAIC = [f'{s}_{n}' for s in ('m15', 'h4') for n in ('mcginley20', 'laguerre05', 'andean35')]
EXT_FEATURES = HOURLY + DAILY + MOSAIC
HOURLY_END = '2026-03-05'  # last full UTC day in the hourly mirror


def z(x, n):
    return (x - x.rolling(n, min_periods=n // 4).mean()) / (x.rolling(n, min_periods=n // 4).std() + 1e-12)


def hourly_frame(ext):
    b = Path(ext) / 'Crypto-PerpetualFutures/data/BTC'
    bn = pd.read_parquet(b / 'binance_BTC_2020-01-01_2026-03-06.parquet').set_index('timestamp')
    bn.index = pd.to_datetime(bn.index, utc=True)
    master = pd.read_parquet(b / 'BTC_master.parquet')
    oi_c = pd.read_parquet(b / 'open_interest/BTCUSDT.parquet').open_interest  # Binance contracts, from 2020-12
    oi_c.index = pd.to_datetime(oi_c.index, utc=True)
    idx = pd.date_range('2020-01-01', '2026-03-06 23:00', freq='1h', tz='UTC')
    bn, master, oi_c = bn.reindex(idx), master.reindex(idx), oi_c.reindex(idx)
    f = pd.DataFrame(index=idx)
    fund = bn.funding_rate_raw_binance.ffill(limit=12)  # settled every 8h, carried until the next stamp
    f['fund_bp'] = fund * 1e4
    xv = master[[c for c in master.columns if c.startswith('funding_rate_1h_')]].ffill(limit=12)
    f['fund_xvenue_bp'] = xv.mean(axis=1) * 8e4
    f['fund_z30'] = z(fund, 24 * 30)
    basis = (bn.vwap_perp_binance / bn.vwap_spot_binance - 1) * 1e4
    f['basis_bp'] = basis.clip(-100, 100) / 5
    f['basis_chg24'] = (basis - basis.shift(24)).clip(-100, 100) / 5
    # (the mirror's OKX best bid/ask covers <1% of hours, so no order-book input is built)
    oi = np.log(oi_c.where(oi_c > 0)).ffill(limit=3)
    oi_sd = oi.diff().rolling(24 * 30, min_periods=24 * 5).std()
    for h in (1, 4, 24):
        f[f'oi_chg{h}h'] = (oi - oi.shift(h)) / (oi_sd * np.sqrt(h))
    f['oi_z30'] = z(oi, 24 * 30)
    f['perp_spot_vol'] = z(np.log((bn.volume_perp_binance + 1) / (bn.volume_spot_binance + 1)), 24 * 30)
    f['perp_vol_z'] = z(np.log1p(bn.volume_perp_binance), 24 * 30)
    # an hourly row stamped t (bar open) is complete at t+1h: that is the close of the 15m bar labelled t+45m
    f.index = f.index + pd.Timedelta(minutes=45)
    return f


def daily_frame(ext):
    def series(path, key=None):
        d = json.loads(Path(path).read_text())
        if key:  # akpasz format
            rows = d['series'][key]
            return pd.Series({pd.Timestamp(a, tz='UTC'): float(v) for a, v in rows})
        return pd.Series({pd.Timestamp(r['timestamp'], unit='ms', tz='UTC'): r['value'] for r in d['data']}).astype(float)
    cq = Path(ext) / 'crypto-market-data/data/daily'
    fg = series(Path(ext) / 'btc-data/data/fear_greed.json', 'index')
    s = {n: series(cq / f'{n}.json') for n in ('btc_long_liquidations_usd', 'btc_short_liquidations_usd', 'btc_taker_buy_sell_ratio',
                                               'btc_coinbase_premium_index', 'btc_exchange_netflow', 'btc_exchange_stablecoins_ratio')}
    idx = pd.date_range('2018-01-01', '2026-11-01', freq='D', tz='UTC')
    f = pd.DataFrame(index=idx)
    fg = fg.reindex(idx)
    f['fear_greed'] = (fg - 50) / 25
    f['fear_greed_chg7'] = (fg - fg.shift(7)) / 25
    ll, ls = [np.log1p(s[k].reindex(idx)) for k in ('btc_long_liquidations_usd', 'btc_short_liquidations_usd')]
    f['liq_long_z'], f['liq_short_z'] = z(ll, 90), z(ls, 90)
    f['liq_imbalance'] = ll - ls
    f['taker_ratio'] = np.log(s['btc_taker_buy_sell_ratio'].reindex(idx)) * 10
    f['cb_premium'] = s['btc_coinbase_premium_index'].reindex(idx) * 5
    f['exch_netflow_z'] = z(s['btc_exchange_netflow'].reindex(idx), 90)
    f['stable_ratio_z'] = z(np.log(s['btc_exchange_stablecoins_ratio'].reindex(idx)), 90)
    f['onchain_avail'] = s['btc_taker_buy_sell_ratio'].reindex(idx).notna().astype(float)
    return f.shift(1)  # the value for date d is used from session d+1


def mosaic_frame(bars):
    """Andean (35), Laguerre (0.5) and McGinley (20) from app.py, scale-free, at 15m and 4h."""
    def one(b):
        c = b.close.values; o = b.open.values; n = len(c)
        atr = (b.high - b.low).rolling(14, min_periods=1).mean().values + 1e-9
        mcg = np.empty(n); mcg[0] = c[0]
        for i in range(1, n):
            ratio = c[i] / mcg[i - 1]
            mcg[i] = mcg[i - 1] + (c[i] - mcg[i - 1]) / max(20 * ratio ** 4, 1e-12)
        lag = np.empty(n); l0 = l1 = l2 = l3 = c[0]; g = 0.5
        for i in range(n):
            p0, p1, p2 = l0, l1, l2
            l0 = (1 - g) * c[i] + g * l0; l1 = -g * l0 + p0 + g * l1; l2 = -g * l1 + p1 + g * l2; l3 = -g * l2 + p2 + g * l3
            lag[i] = (l0 + 2 * l1 + 2 * l2 + l3) / 6
        a = 2 / 36; up1 = dn1 = c[0]; up2 = dn2 = c[0] ** 2; bull = np.empty(n); bear = np.empty(n)
        for i in range(n):
            up1 = max(c[i], o[i], up1 - a * (up1 - c[i])); up2 = max(c[i] ** 2, o[i] ** 2, up2 - a * (up2 - c[i] ** 2))
            dn1 = min(c[i], o[i], dn1 + a * (c[i] - dn1)); dn2 = min(c[i] ** 2, o[i] ** 2, dn2 + a * (c[i] ** 2 - dn2))
            bull[i] = np.sqrt(max(0, dn2 - dn1 * dn1)); bear[i] = np.sqrt(max(0, up2 - up1 * up1))
        return pd.DataFrame({'mcginley20': (c - mcg) / atr / 2, 'laguerre05': (c - lag) / atr, 'andean35': (bull - bear) / atr},
                            index=b.index)
    f15 = one(bars).add_prefix('m15_')
    b4 = bars.resample('4h', label='left', closed='left').agg(
        {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
    f4 = one(b4).add_prefix('h4_')
    f4.index = f4.index + pd.Timedelta(hours=3, minutes=45)  # complete at the close of the 15m bar labelled t+3h45
    return f15.join(f4.reindex(bars.index, method='ffill'))


def ext_frame(bars, ext):
    h = hourly_frame(ext).reindex(bars.index, method='ffill', tolerance=pd.Timedelta(hours=12))
    d = daily_frame(ext).reindex(pd.DatetimeIndex(bars.day))
    d.index = bars.index
    f = h.join(d).join(mosaic_frame(bars))
    return f[EXT_FEATURES].replace([np.inf, -np.inf], np.nan).fillna(0.0).clip(-6, 6).astype(np.float32)


def build(src, ext):
    df, outages, _ = load_minutes(src)
    bars, _ = build_bars(df, outages)
    f = ext_frame(bars, ext)
    base = load_sessions()
    ind = np.load(OUT / 'sessions_ind_obs.npy', mmap_mode='r')
    days = pd.to_datetime(base['days']).tz_localize('UTC')
    keep = np.where((days >= pd.Timestamp('2020-01-08', tz='UTC')) & (days <= pd.Timestamp(HOURLY_END, tz='UTC')))[0]
    pos = {t: i for i, t in enumerate(bars.index)}
    i0 = np.array([pos[days[k]] for k in keep])
    rows = i0[:, None] + np.arange(BARS_PER_DAY)[None, :] - 1  # last completed bar before each decision
    obs = np.concatenate([np.asarray(ind[keep]), f.values[rows]], -1)
    np.save(OUT / 'sessions_ext_obs.npy', obs.astype(np.float32))
    np.save(OUT / 'sessions_ext_keep.npy', keep)
    print('ext obs', obs.shape, 'days', str(days[keep[0]].date()), '->', str(days[keep[-1]].date()))
    print((f.loc['2021':'2026-03'].abs() > 0).mean().round(2).to_string())


def load_sessions_ext():
    s = load_sessions(); keep = np.load(OUT / 'sessions_ext_keep.npy')
    out = {k: (v[keep] if isinstance(v, np.ndarray) and v.ndim and len(v) == len(s['days']) else v) for k, v in s.items()}
    out['obs'] = np.load(OUT / 'sessions_ext_obs.npy', mmap_mode='r')
    return out


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default='../../data_src/btcmin')
    ap.add_argument('--ext', default='../../data_src/ext')
    a = ap.parse_args()
    build(a.src, a.ext)
