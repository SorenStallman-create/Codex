"""Small, inspectable actor-critic trained with PPO (clipped surrogate + GAE).

Separate actor and critic MLPs (2 x 64, tanh) keep the policy's computation
easy to attribute: every action logit is a function of 20 named inputs.
"""
import copy, json, time
from dataclasses import dataclass, asdict
import numpy as np
import torch
import torch.nn as nn

from env import MARKETS, bar_returns, hl_returns, step, state_features, run_policy
from data import FEATURES, STATE, CONTEXT, SEQ_CHANNELS, SEQ_LEN

INPUTS = FEATURES + STATE
INPUTS_DEEP = FEATURES + CONTEXT + [f'{c}@t-{SEQ_LEN - k}' for c in SEQ_CHANNELS for k in range(SEQ_LEN)] + STATE


@dataclass(frozen=True)
class PPOConfig:
    hidden: int = 64
    n_envs: int = 128           # days per rollout (each rollout = one full day per env)
    updates: int = 400
    lr: float = 3e-4
    epochs: int = 4
    minibatch: int = 2048
    clip: float = 0.2
    gamma: float = 0.99
    lam: float = 0.95
    ent_coef: float = 0.01
    vf_coef: float = 0.5
    max_grad: float = 0.5
    reward_scale: float = 100.0  # log-growth in percent
    eval_every: int = 10
    arch: str = 'mlp'            # 'mlp' (compact, 2x64) or 'deep' (conv over raw bars + VIX)


def mlp(n_in, hidden, n_out, out_gain):
    layers = [nn.Linear(n_in, hidden), nn.Tanh(), nn.Linear(hidden, hidden), nn.Tanh(), nn.Linear(hidden, n_out)]
    for l in layers:
        if isinstance(l, nn.Linear):
            nn.init.orthogonal_(l.weight, np.sqrt(2)); nn.init.zeros_(l.bias)
    nn.init.orthogonal_(layers[-1].weight, out_gain)
    return nn.Sequential(*layers)


class ActorCritic(nn.Module):
    def __init__(self, n_in, n_act, hidden=64):
        super().__init__()
        self.actor = mlp(n_in, hidden, n_act, 0.01)
        self.critic = mlp(n_in, hidden, 1, 1.0)

    def forward(self, x):
        return self.actor(x), self.critic(x).squeeze(-1)

    def hidden(self, x):
        """Post-activation outputs of both actor hidden layers (for activation views)."""
        h1 = self.actor[1](self.actor[0](x))
        h2 = self.actor[3](self.actor[2](h1))
        return h1, h2


def summary(model):
    rows = []
    for name, m in model.named_modules():
        if isinstance(m, (nn.Linear, nn.Tanh)):
            p = sum(t.numel() for t in m.parameters())
            shape = f'{m.in_features} -> {m.out_features}' if isinstance(m, nn.Linear) else 'elementwise'
            rows.append({'layer': name, 'type': type(m).__name__, 'shape': shape, 'params': p})
    return {'rows': rows, 'total_params': sum(p.numel() for p in model.parameters()), 'inputs': INPUTS}


def policy_fn(models, greedy=True):
    """Action function over numpy obs; averages probabilities across an ensemble of models."""
    def act(obs, j):
        with torch.no_grad():
            x = torch.as_tensor(obs)
            p = torch.stack([torch.softmax(m(x)[0], -1) for m in models]).mean(0)
        return p.argmax(-1).numpy() if greedy else torch.multinomial(p, 1).squeeze(-1).numpy()
    return act


def evaluate(models, market, sess, days):
    r = run_policy(market, sess, days, policy_fn(models))
    return float(r['daily_log'].mean()), r


def train(market_name, sess, train_days, val_days, seed, cfg=PPOConfig(), log=print):
    market = MARKETS[market_name]
    torch.manual_seed(seed); rng = np.random.default_rng(seed)
    torch.set_num_threads(1)
    n_act = len(market.positions)
    n_in = sess['obs'].shape[2] + 3
    model = DeepActorCritic(n_act) if cfg.arch == 'deep' else ActorCritic(n_in, n_act, cfg.hidden)
    opt = torch.optim.Adam(model.parameters(), lr=cfg.lr, eps=1e-5)
    posmap = np.array(market.positions, np.float64)
    history, best = [], (-np.inf, None, -1)
    N, T = cfg.n_envs, sess['open'].shape[1]
    t0 = time.time()
    for upd in range(cfg.updates):
        for g in opt.param_groups:
            g['lr'] = cfg.lr * (1 - upd / cfg.updates)
        days = rng.choice(train_days, N, replace=True)
        o, c, x = sess['open'][days], sess['close'][days], np.asarray(sess['obs'][days])
        gap, bar = bar_returns(o, c)
        lo, hi = hl_returns(sess, days)
        obs_buf = np.zeros((T, N, n_in), np.float32); act_buf = np.zeros((T, N), np.int64)
        logp_buf = np.zeros((T, N), np.float32); val_buf = np.zeros((T, N), np.float32)
        rew_buf = np.zeros((T, N), np.float32)
        pos = np.zeros(N); unreal = np.zeros(N)
        with torch.no_grad():
            for j in range(T):
                obs = np.concatenate([x[:, j], state_features(pos, j, unreal, T)], 1)
                logits, v = model(torch.as_tensor(obs))
                dist = torch.distributions.Categorical(logits=logits)
                a = dist.sample()
                new, g = step(market, pos, posmap[a.numpy()], gap[:, j], bar[:, j], lo[:, j], hi[:, j], j, T)
                rew_buf[j] = cfg.reward_scale * g
                unreal = np.where(new == pos, unreal + np.log1p(pos * gap[:, j]) + np.log1p(new * bar[:, j]),
                                  np.log1p(new * bar[:, j]))
                unreal = np.where(new == 0, 0.0, unreal)
                obs_buf[j], act_buf[j], logp_buf[j], val_buf[j] = obs, a.numpy(), dist.log_prob(a).numpy(), v.numpy()
                pos = new
        # GAE; each rollout is exactly one episode per env, terminal after bar 95
        adv = np.zeros((T, N), np.float32); last = np.zeros(N, np.float32)
        for j in reversed(range(T)):
            nv = val_buf[j + 1] if j + 1 < T else 0.0
            delta = rew_buf[j] + cfg.gamma * nv - val_buf[j]
            last = delta + cfg.gamma * cfg.lam * last * (j + 1 < T)
            adv[j] = last
        ret = adv + val_buf
        b_obs = torch.as_tensor(obs_buf.reshape(-1, n_in)); b_act = torch.as_tensor(act_buf.reshape(-1))
        b_logp = torch.as_tensor(logp_buf.reshape(-1)); b_adv = torch.as_tensor(adv.reshape(-1))
        b_ret = torch.as_tensor(ret.reshape(-1))
        stats = {'pg': [], 'vf': [], 'ent': [], 'kl': [], 'clipfrac': []}
        idx = np.arange(N * T)
        for _ in range(cfg.epochs):
            rng.shuffle(idx)
            for s in range(0, len(idx), cfg.minibatch):
                mb = torch.as_tensor(idx[s:s + cfg.minibatch])
                logits, v = model(b_obs[mb])
                dist = torch.distributions.Categorical(logits=logits)
                logp = dist.log_prob(b_act[mb]); ratio = (logp - b_logp[mb]).exp()
                a_ = b_adv[mb]; a_ = (a_ - a_.mean()) / (a_.std() + 1e-8)
                pg = -torch.min(ratio * a_, ratio.clamp(1 - cfg.clip, 1 + cfg.clip) * a_).mean()
                vf = 0.5 * ((v - b_ret[mb]) ** 2).mean()
                ent = dist.entropy().mean()
                loss = pg + cfg.vf_coef * vf - cfg.ent_coef * ent
                opt.zero_grad(); loss.backward()
                nn.utils.clip_grad_norm_(model.parameters(), cfg.max_grad); opt.step()
                with torch.no_grad():
                    stats['pg'].append(pg.item()); stats['vf'].append(vf.item()); stats['ent'].append(ent.item())
                    stats['kl'].append(((ratio - 1) - (logp - b_logp[mb])).mean().item())
                    stats['clipfrac'].append(((ratio - 1).abs() > cfg.clip).float().mean().item())
        ev = 1 - np.var(ret - val_buf) / (np.var(ret) + 1e-8)
        acts = act_buf
        rec = {'update': upd, 'train_day_log_pct': float(rew_buf.sum(0).mean()),
               'trades_per_day': float((np.diff(np.concatenate([np.zeros((1, N)), posmap[acts]]), axis=0) != 0).sum(0).mean()),
               'explained_var': float(ev), **{k: float(np.mean(v)) for k, v in stats.items()}}
        if upd % cfg.eval_every == 0 or upd == cfg.updates - 1:
            model.eval()
            vlog, vr = evaluate([model], market, sess, val_days)
            rec['val_day_log_pct'] = 100 * vlog
            rec['val_turnover'] = float(vr['turnover'].mean())
            rec['val_exposure'] = float(np.abs(vr['held']).mean())
            # checkpoint selection uses validation only; the test period is never seen here
            if vlog > best[0]:
                best = (vlog, copy.deepcopy(model.state_dict()), upd)
            model.train()
            log(f'[{market_name} seed{seed}] upd {upd:3d} train {rec["train_day_log_pct"]:+.3f}%/day '
                f'val {rec["val_day_log_pct"]:+.3f}%/day turn {rec["val_turnover"]:.2f} ent {rec["ent"]:.3f} '
                f'{time.time() - t0:.0f}s')
        history.append(rec)
    model.load_state_dict(best[1]); model.eval()
    return model, history, {'best_update': best[2], 'best_val_day_log_pct': 100 * best[0], 'config': asdict(cfg)}


class DeepActorCritic(nn.Module):
    """Higher-capacity variant: a dilated 1-D conv encoder reads the last 64 raw bars
    (6 channels), fused with the 17 engineered features, VIX context and agent state.
    Actor and critic share the encoder; ~0.03M parameters."""
    def __init__(self, n_act, n_feat=17, n_ctx=2, channels=6, seq=64, n_state=3, width=128):
        super().__init__()
        self.n_feat, self.n_ctx, self.channels, self.seq = n_feat, n_ctx, channels, seq
        # 15-minute bars are paired into 30-minute patches, then a causal dilated stack
        # (left padding only) gives the last patch a receptive field over all 32 patches
        self.conv = nn.Sequential(
            nn.Conv1d(channels, 24, 2, stride=2), nn.GELU(),
            nn.ConstantPad1d((4, 0), 0.0), nn.Conv1d(24, 24, 5, dilation=1), nn.GELU(),
            nn.ConstantPad1d((8, 0), 0.0), nn.Conv1d(24, 32, 5, dilation=2), nn.GELU(),
            nn.ConstantPad1d((16, 0), 0.0), nn.Conv1d(32, 32, 5, dilation=4), nn.GELU())
        fused = 32 * 2 + n_feat + n_ctx + n_state
        self.trunk = nn.Sequential(nn.Linear(fused, width), nn.GELU(), nn.Linear(width, width), nn.GELU())
        self.pi = nn.Linear(width, n_act); self.v = nn.Linear(width, 1)
        nn.init.orthogonal_(self.pi.weight, 0.01); nn.init.zeros_(self.pi.bias)

    def split(self, x):
        a = self.n_feat + self.n_ctx; b = a + self.channels * self.seq
        return x[:, :a], x[:, a:b].reshape(-1, self.channels, self.seq), x[:, b:]

    def encode(self, x):
        flat, seq, state = self.split(x)
        h = self.conv(seq)
        z = torch.cat([h[..., -1], h.mean(-1), flat, state], -1)
        return self.trunk(z)

    def forward(self, x):
        z = self.encode(x)
        return self.pi(z), self.v(z).squeeze(-1)

    def hidden(self, x):
        z = self.encode(x)
        return z, z
