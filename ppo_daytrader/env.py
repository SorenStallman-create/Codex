"""Vectorized intraday environment shared by PPO training, baselines and evaluation.

One episode = one UTC day of 96 fifteen-minute bars. The account starts flat,
decides a target position at the open of every bar using only information from
completed bars, and is forced flat at the close of the last bar. Every change
of position pays `cost` (fee + slippage) per unit traded; perp mode also pays a
funding allowance whenever a position is held across 08:00 or 16:00 UTC.
"""
from dataclasses import dataclass
import numpy as np

BARS = 96


@dataclass(frozen=True)
class Market:
    name: str
    positions: tuple       # action index -> target position
    cost: float            # per side, fraction of notional (taker fee + slippage)
    funding: float = 0.0   # per 8h stamp held, fraction of notional (charged regardless of sign)
    maker_fee: float = None  # set -> entries/exits are resting limit orders at the bar open
    through: float = 0.0005  # a limit fills only if price trades this far through it during the bar

    @property
    def flat_action(self):
        return self.positions.index(0)


SPOT = Market('spot', (0, 1), cost=0.0020)                     # 0.15% fee + 0.05% slippage (Trading Lab model)
PERP = Market('perp', (-1, 0, 1), cost=0.0006, funding=0.0001)  # ~0.05% taker + 0.01% slippage, 0.01%/8h funding
# Limit-order variants: the order rests at the bar's open and fills only when the bar trades
# 5 bp through it (so fills arrive on adverse moves); unfilled orders lapse. Forced session
# exits stay taker orders.
SPOT_MAKER = Market('spot_maker', (0, 1), cost=0.0020, maker_fee=0.0010)
PERP_MAKER = Market('perp_maker', (-1, 0, 1), cost=0.0006, funding=0.0001, maker_fee=0.0002)
MARKETS = {'spot': SPOT, 'perp': PERP, 'spot_maker': SPOT_MAKER, 'perp_maker': PERP_MAKER}
FUNDING_BARS = (32, 64)  # bars that start at 08:00 and 16:00 UTC (15-minute sessions)


def funding_bars(n):
    return (n // 3, 2 * n // 3)


def resample(sess, k):
    """Coarser decision cadence: k consecutive bars become one. Decision obs stay causal
    (features at the close of the bar before the new bar's open)."""
    if k == 1:
        return sess
    d, n = sess['open'].shape
    out = dict(sess)
    out['open'] = sess['open'][:, ::k]; out['close'] = sess['close'][:, k - 1::k]
    out['high'] = sess['high'].reshape(d, n // k, k).max(-1); out['low'] = sess['low'].reshape(d, n // k, k).min(-1)
    out['obs'] = np.asarray(sess['obs'])[:, ::k]
    return out


def bar_returns(open_, close):
    """Simple returns: gap (prev close -> this open) and in-bar (open -> close)."""
    prev_close = np.concatenate([open_[:, :1], close[:, :-1]], axis=1)  # day's first gap is not held
    return open_ / prev_close - 1.0, close / open_ - 1.0


def step(market, prev_pos, target, gap, bar, lo, hi, j, n):
    """Hold prev_pos through the gap, try to trade to target at the bar open, hold over the bar.
    Returns (position actually held, log growth of equity). lo/hi are low/open-1 and high/open-1."""
    if market.maker_fee is None:
        pos, fee = target, market.cost
    else:
        filled = np.where(target > prev_pos, lo < -market.through, np.where(target < prev_pos, hi > market.through, True))
        pos, fee = np.where(filled, target, prev_pos), market.maker_fee
    g = np.log1p(prev_pos * gap)
    g = g + np.log1p(-fee * np.abs(pos - prev_pos))
    g = g + np.log1p(pos * bar)
    if market.funding and j in (FUNDING_BARS if n == BARS else funding_bars(n)):
        g = g + np.log1p(-market.funding * np.abs(pos))
    if j == n - 1:  # forced liquidation at the session close, always a taker order
        g = g + np.log1p(-market.cost * np.abs(pos))
    return pos, g


def step_reward(market, prev_pos, pos, gap, bar, j, n=BARS):
    """Taker-only shortcut kept for callers that already know the fill."""
    return step(market, prev_pos, pos, gap, bar, None, None, j, n)[1]


def hl_returns(sess, days):
    o = sess['open'][days]
    if 'high' not in sess:
        return np.zeros_like(o), np.zeros_like(o)
    return sess['low'][days] / o - 1.0, sess['high'][days] / o - 1.0


def state_features(pos, j, unreal, n=BARS):
    """Agent-state inputs appended to the market features."""
    return np.stack([pos.astype(np.float32),
                     np.full(pos.shape, 1.0 - j / n, np.float32),
                     np.clip(unreal * 100.0, -5, 5).astype(np.float32)], axis=-1)


def run_policy(market, sess, day_idx, act_fn, keep_obs=False):
    """Play `act_fn(obs[N, F+3], j) -> action indices[N]` over whole days. Returns per-step detail."""
    o, c, x = sess['open'][day_idx], sess['close'][day_idx], np.asarray(sess['obs'][day_idx])
    gap, bar = bar_returns(o, c)
    lo, hi = hl_returns(sess, day_idx)
    n, T = len(day_idx), o.shape[1]
    posmap = np.array(market.positions, np.float64)
    pos = np.zeros(n); unreal = np.zeros(n)
    logg = np.zeros((n, T)); held = np.zeros((n, T)); acts = np.zeros((n, T), np.int64)
    seen = np.zeros((n, T, x.shape[2] + 3), np.float32) if keep_obs else None
    for j in range(T):
        obs = np.concatenate([x[:, j], state_features(pos, j, unreal, T)], axis=1)
        a = act_fn(obs, j)
        if keep_obs:
            seen[:, j] = obs
        new, logg[:, j] = step(market, pos, posmap[a], gap[:, j], bar[:, j], lo[:, j], hi[:, j], j, T)
        unreal = np.where(new == pos, unreal + np.log1p(pos * gap[:, j]) + np.log1p(new * bar[:, j]),
                          np.log1p(new * bar[:, j]))
        unreal = np.where(new == 0, 0.0, unreal)
        held[:, j] = new; acts[:, j] = a; pos = new
    turnover = np.abs(np.diff(np.concatenate([np.zeros((n, 1)), held, np.zeros((n, 1))], axis=1), axis=1)).sum(1)
    return {'daily_log': logg.sum(1), 'daily_ret': np.expm1(logg.sum(1)), 'held': held,
            'actions': acts, 'turnover': turnover, 'step_log': logg, 'obs': seen}
