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
    cost: float            # per side, fraction of notional
    funding: float = 0.0   # per 8h stamp held, fraction of notional (charged regardless of sign)

    @property
    def flat_action(self):
        return self.positions.index(0)


SPOT = Market('spot', (0, 1), cost=0.0020)                     # 0.15% fee + 0.05% slippage (Trading Lab model)
PERP = Market('perp', (-1, 0, 1), cost=0.0006, funding=0.0001)  # ~0.05% taker + 0.01% slippage, 0.01%/8h funding
MARKETS = {'spot': SPOT, 'perp': PERP}
FUNDING_BARS = (32, 64)  # bars that start at 08:00 and 16:00 UTC


def bar_returns(open_, close):
    """Simple returns: gap (prev close -> this open) and in-bar (open -> close)."""
    prev_close = np.concatenate([open_[:, :1], close[:, :-1]], axis=1)  # day's first gap is not held
    return open_ / prev_close - 1.0, close / open_ - 1.0


def step_reward(market, prev_pos, pos, gap, bar, j):
    """Log growth of equity for holding prev_pos through the gap, trading to pos, and holding pos over the bar."""
    g = np.log1p(prev_pos * gap)
    g = g + np.log1p(-market.cost * np.abs(pos - prev_pos))
    g = g + np.log1p(pos * bar)
    if market.funding and j in FUNDING_BARS:
        g = g + np.log1p(-market.funding * np.abs(pos))
    if j == BARS - 1:  # forced liquidation at the session close
        g = g + np.log1p(-market.cost * np.abs(pos))
    return g


def state_features(pos, j, unreal):
    """Agent-state inputs appended to the market features."""
    return np.stack([pos.astype(np.float32),
                     np.full(pos.shape, 1.0 - j / BARS, np.float32),
                     np.clip(unreal * 100.0, -5, 5).astype(np.float32)], axis=-1)


def run_policy(market, sess, day_idx, act_fn, keep_obs=False):
    """Play `act_fn(obs[N, F+3], j) -> action indices[N]` over whole days. Returns per-step detail."""
    o, c, x = sess['open'][day_idx], sess['close'][day_idx], sess['obs'][day_idx]
    gap, bar = bar_returns(o, c)
    n = len(day_idx)
    posmap = np.array(market.positions, np.float64)
    pos = np.zeros(n); unreal = np.zeros(n)
    logg = np.zeros((n, BARS)); held = np.zeros((n, BARS)); acts = np.zeros((n, BARS), np.int64)
    seen = np.zeros((n, BARS, x.shape[2] + 3), np.float32) if keep_obs else None
    for j in range(BARS):
        obs = np.concatenate([x[:, j], state_features(pos, j, unreal)], axis=1)
        a = act_fn(obs, j)
        if keep_obs:
            seen[:, j] = obs
        new = posmap[a]
        logg[:, j] = step_reward(market, pos, new, gap[:, j], bar[:, j], j)
        unreal = np.where(new == pos, unreal + np.log1p(pos * gap[:, j]) + np.log1p(new * bar[:, j]),
                          np.log1p(new * bar[:, j]))
        unreal = np.where(new == 0, 0.0, unreal)
        held[:, j] = new; acts[:, j] = a; pos = new
    turnover = np.abs(np.diff(np.concatenate([np.zeros((n, 1)), held, np.zeros((n, 1))], axis=1), axis=1)).sum(1)
    return {'daily_log': logg.sum(1), 'daily_ret': np.expm1(logg.sum(1)), 'held': held,
            'actions': acts, 'turnover': turnover, 'step_log': logg, 'obs': seen}
