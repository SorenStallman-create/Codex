"""Score PPO ensembles against baselines on every untouched test year, then
open up the network: attributions, permutation tests, activations, policy surfaces."""
import json
from dataclasses import replace
from pathlib import Path
import numpy as np
import torch

from data import OUT, FEATURES, CONTEXT, SEQ_CHANNELS, SEQ_LEN, STATE, load_sessions
from env import MARKETS, bar_returns, run_policy, resample
from ppo import ActorCritic, DeepActorCritic, INPUTS, INPUTS_DEEP, INPUTS_IND, policy_fn
from walkforward import PROTOCOL, VARIANTS, fold_list, sessions_for

VARIANT, MODELS, SEEDS = 'mlp', VARIANTS['mlp']['models'], VARIANTS['mlp']['seeds']
F = {n: i for i, n in enumerate(INPUTS)}
MF = list(FEATURES)  # market inputs the linear baseline and policy surface read


def set_variant(v):
    global VARIANT, MODELS, SEEDS, INPUTS, F, MF
    VARIANT, MODELS, SEEDS = v, VARIANTS[v]['models'], VARIANTS[v]['seeds']
    INPUTS = {'deep': INPUTS_DEEP, 'ind': INPUTS_IND}.get(v, FEATURES + STATE)
    F = {n: i for i, n in enumerate(INPUTS)}
    MF = INPUTS[:-len(STATE)] if v == 'ind' else list(FEATURES)


def groups():
    """Named input groups for attribution: single features, or a whole raw-bar channel for the deep model."""
    if VARIANT != 'deep':
        return {n: [F[n]] for n in INPUTS}
    g = {n: [F[n]] for n in FEATURES + CONTEXT + STATE}
    for c in SEQ_CHANNELS:
        g[f'bars:{c}'] = [F[f'{c}@t-{SEQ_LEN - k}'] for k in range(SEQ_LEN)]
    return g


def summary_cols():
    """Inputs shown in unit-correlation views (the newest bar stands in for each raw channel)."""
    if VARIANT != 'deep':
        return list(INPUTS)
    return FEATURES + CONTEXT + [f'{c}@t-1' for c in SEQ_CHANNELS] + STATE
RNG = np.random.default_rng(7)


def pos_to_action(market, pos):
    lut = {p: i for i, p in enumerate(market.positions)}
    return np.select([pos == p for p in lut], list(lut.values()))


# ---------- baselines (all play the same environment and pay the same costs) ----------
def const(market, pos):
    a = market.positions.index(pos)
    return lambda obs, j: np.full(len(obs), a)


def sign_rule(market, feat, flip=False):
    lo = market.positions.index(-1) if -1 in market.positions else market.flat_action
    hi = market.positions.index(1)
    def act(obs, j):
        s = obs[:, F[feat]] * (-1 if flip else 1)
        return np.where(s > 0, hi, lo)
    return act


def random_matched(market, switch_prob, rng):
    k = len(market.positions)
    def act(obs, j):
        cur_a = pos_to_action(market, obs[:, F['position']])
        sw = rng.random(len(obs)) < switch_prob
        other = (cur_a + rng.integers(1, k, len(obs))) % k
        return np.where(sw, other, cur_a)
    return act


def fit_linear(sess, days, lam=10.0):
    """Ridge forecast of the next bar's return from the market features: the 'signal ceiling' check."""
    x = np.asarray(sess['obs'][days])[..., :len(MF)].reshape(-1, len(MF)).astype(np.float64)
    _, bar = bar_returns(sess['open'][days], sess['close'][days])
    y = bar.reshape(-1)
    xm, ym = x.mean(0), y.mean()
    xc = x - xm
    w = np.linalg.solve(xc.T @ xc + lam * len(x) * 1e-4 * np.eye(x.shape[1]), xc.T @ (y - ym))
    return lambda X: (X[:, :len(MF)] - xm) @ w + ym


def linear_rule(market, pred):
    lo = market.positions.index(-1) if -1 in market.positions else market.flat_action
    hi, flat = market.positions.index(1), market.flat_action
    def act(obs, j):
        p = pred(obs.astype(np.float64))
        cur = pos_to_action(market, obs[:, F['position']])
        # trade only when the forecast clears one side of the cost; otherwise keep the position
        fee = market.cost if market.maker_fee is None else market.maker_fee
        return np.where(p > fee, hi, np.where(p < -fee, lo, cur))
    return act


def ic(pred, sess, days):
    x = np.asarray(sess['obs'][days])[..., :len(MF)].reshape(-1, len(MF)).astype(np.float64)
    _, bar = bar_returns(sess['open'][days], sess['close'][days])
    p, y = pred(x), bar.reshape(-1)
    rk = lambda v: np.argsort(np.argsort(v))
    return float(np.corrcoef(rk(p), rk(y))[0, 1])


# ---------- statistics ----------
def block_boot(x, stat=np.mean, n=2000, block=7, rng=RNG):
    x = np.asarray(x); m = len(x)
    starts = rng.integers(0, m, (n, int(np.ceil(m / block))))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(n, -1)[:, :m] % m
    s = np.array([stat(x[i]) for i in idx])
    return [float(np.percentile(s, 2.5)), float(np.percentile(s, 97.5))]


def metrics(r, bh=None, period_days=1):
    """One row per session. For multi-day chunks (swing) the per-day figures are the chunk's
    geometric per-day rate, Sharpe is annualized by chunks per year, and the bootstrap block is
    two chunks (about two months) instead of seven days."""
    d = r['daily_ret']; P = period_days
    per_day = (lambda x: np.expm1(np.log1p(x) / P)) if P > 1 else (lambda x: x)
    blk = 7 if P == 1 else 2
    eq = np.cumprod(1 + d); peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))[1:]
    out = {'days': int(len(d) * P), 'periods': int(len(d)), 'period_days': P,
           'mean_day_pct': 100 * float(per_day(d).mean()), 'ci95_mean_day_pct': [100 * v for v in block_boot(per_day(d), block=blk)],
           'total_return_pct': 100 * float(eq[-1] - 1), 'sharpe_ann': float(d.mean() / (d.std() + 1e-12) * np.sqrt(365 / P)),
           'max_drawdown_pct': 100 * float((eq / peak - 1).min()), 'win_day_pct': 100 * float((d > 0).mean()),
           'trades_per_day': float(r['turnover'].mean() / P), 'exposure_pct': 100 * float(np.abs(r['held']).mean()),
           'equity': [float(v) for v in eq]}
    if bh is not None:
        diff = per_day(d) - per_day(bh)
        out['vs_intraday_bh_mean_pct'] = 100 * float(diff.mean())
        out['vs_intraday_bh_ci95_pct'] = [100 * v for v in block_boot(diff, block=blk)]
    return out


def load_models(market, year):
    m = MARKETS[market]; out = []
    for s in SEEDS:
        p = MODELS / f'{market}_{year}_s{s}.pt'
        if p.exists():
            net = DeepActorCritic(len(m.positions)) if VARIANT == 'deep' else ActorCritic(len(INPUTS), len(m.positions))
            net.load_state_dict(torch.load(p)); net.eval()
            out.append(net)
    return out


# ---------- inspection ----------
def logit_margins(net, x, market):
    """Action logit minus flat logit, for each non-flat action."""
    lg = net(x)[0]; fl = market.flat_action
    return {f'{"long" if p > 0 else "short"}_vs_flat': lg[:, i] - lg[:, fl]
            for i, p in enumerate(market.positions) if p != 0}


def integrated_gradients(models, market, X, base, steps=32):
    out = {}
    x = torch.as_tensor(X); b = torch.as_tensor(base)[None]
    for net in models:
        total = None
        for k in range(1, steps + 1):
            xi = (b + (k / steps) * (x - b)).requires_grad_(True)
            margins = logit_margins(net, xi, market)
            grads = {n: torch.autograd.grad(v.sum(), xi, retain_graph=True)[0] for n, v in margins.items()}
            total = grads if total is None else {n: total[n] + grads[n] for n in grads}
        for n, g in total.items():
            ig = ((x - b) * g / steps).detach().numpy()
            out.setdefault(n, []).append(ig)
    return {n: {'mean_abs': np.mean([np.abs(a).mean(0) for a in v], 0).tolist(),
                'mean_signed': np.mean([a.mean(0) for a in v], 0).tolist()} for n, v in out.items()}


def permutation(models, market, sess, days, base_log):
    """Shuffle one input across days at each bar during live play; record the change in net result."""
    res = {}
    greedy = policy_fn(models)
    for name, i in groups().items():
        rng = np.random.default_rng(1)
        def act(obs, j, i=i, rng=rng):
            o = obs.copy(); o[:, i] = o[rng.permutation(len(o))][:, i]
            return greedy(o, j)
        r = run_policy(market, sess, days, act)
        res[name] = {'delta_day_log_pct': 100 * float(r['daily_log'].mean() - base_log),
                     'delta_trades_per_day': float(r['turnover'].mean())}
    return res


def inspect(market, year, models, sess, days, played):  # noqa: C901
    X = played['obs'].reshape(-1, len(INPUTS))
    sample = X[RNG.choice(len(X), min(4000, len(X)), replace=False)]
    base = X.mean(0).astype(np.float32)
    net = models[0]
    with torch.no_grad():
        xt = torch.as_tensor(sample)
        h1, h2 = net.hidden(xt)
        probs = torch.softmax(net(xt)[0], -1).numpy()
        vals = net(xt)[1].numpy()
    h2n = h2.numpy()
    # unit <-> input correlation (which inputs drive each last-layer unit)
    cols = [F[n] for n in summary_cols()]
    corr = np.nan_to_num(np.corrcoef(np.concatenate([sample[:, cols], h2n], 1).T)[:len(cols), len(cols):])
    # PCA of last hidden layer
    hc = h2n - h2n.mean(0); u, s, vt = np.linalg.svd(hc, full_matrices=False)
    pcs = hc @ vt[:2].T; var = (s ** 2 / (s ** 2).sum())[:2]
    # which units the action head leans on: output weight x unit activity
    w_out = (net.pi if VARIANT == 'deep' else net.actor[4]).weight.detach().numpy()
    fl = market.flat_action
    margin_w = {f'{"long" if p > 0 else "short"}_vs_flat': (w_out[i] - w_out[fl]).tolist()
                for i, p in enumerate(market.positions) if p != 0}
    order = np.argsort(-np.abs(corr).max(0))
    # policy surface over the two most influential market features (others at test mean, flat, mid-day)
    ig_raw = integrated_gradients(models, market, sample[:1500], base)
    G_ = groups()
    ig = {k: {'names': list(G_), 'mean_abs': [float(np.sum(np.array(v['mean_abs'])[ix])) for ix in G_.values()],
              'mean_signed': [float(np.sum(np.array(v['mean_signed'])[ix])) for ix in G_.values()]} for k, v in ig_raw.items()}
    lag_profile = None
    if VARIANT == 'deep':
        lag_profile = {c: np.array(ig_raw['long_vs_flat']['mean_abs'])[G_[f'bars:{c}']].round(5).tolist() for c in SEQ_CHANNELS}
    key = 'long_vs_flat'
    imp = np.array(ig_raw[key]['mean_abs'][:len(MF)])
    top2 = [MF[i] for i in np.argsort(-imp)[:2]]
    grid = np.linspace(-3, 3, 25).astype(np.float32)
    G = np.tile(base, (25 * 25, 1)); G[:, F['position']] = 0; G[:, F['time_left']] = 0.5; G[:, F['unreal']] = 0
    G[:, F[top2[0]]] = np.repeat(grid, 25); G[:, F[top2[1]]] = np.tile(grid, 25)
    with torch.no_grad():
        P = torch.stack([torch.softmax(m(torch.as_tensor(G))[0], -1) for m in models]).mean(0).numpy()
    # day replays: best, median and worst test day for the ensemble
    order_days = np.argsort(played['daily_log'])
    picks = {'worst': int(order_days[0]), 'median': int(order_days[len(order_days) // 2]), 'best': int(order_days[-1])}
    replays = {}
    for tag, k in picks.items():
        xo = torch.as_tensor(played['obs'][k])
        with torch.no_grad():
            p = torch.stack([torch.softmax(m(xo)[0], -1) for m in models]).mean(0).numpy()
            v = torch.stack([m(xo)[1] for m in models]).mean(0).numpy()
            _, hh = net.hidden(xo)
        replays[tag] = {'day': str(sess['days'][days[k]]), 'close': sess['close'][days[k]].tolist(),
                        'open': sess['open'][days[k]].tolist(), 'held': played['held'][k].tolist(),
                        'probs': p.round(4).tolist(), 'value': v.round(4).tolist(),
                        'day_ret_pct': 100 * float(played['daily_ret'][k]),
                        'h2': hh.numpy()[:, order[:24]].round(3).T.tolist()}
    pick = RNG.choice(len(sample), 1500, replace=False)
    return {
        'model_year': year, 'integrated_gradients': ig, 'lag_profile': lag_profile, 'corr_inputs': summary_cols(),
        'hidden_desc': 'trunk layer 2 (128 GELU units)' if VARIANT == 'deep' else 'actor layer 2 (64 tanh units)',
        'unit_input_corr': corr[:, order].round(3).tolist(), 'unit_order': order.tolist(),
        'pca': {'xy': pcs[pick].round(3).tolist(), 'p_action': probs[pick].round(3).tolist(),
                'value': vals[pick].round(3).tolist(), 'explained': var.round(3).tolist()},
        'head_weights': {k: np.array(v)[order].round(3).tolist() for k, v in margin_w.items()},
        'surface': {'x': top2[0], 'y': top2[1], 'grid': grid.tolist(), 'probs': P.round(4).tolist()},
        'replays': replays,
        'dead_units_pct': 100 * float((np.abs(h2n).std(0) < 0.02).mean()),
        'saturated_pct': 100 * float((np.abs(h2n) > 0.97).mean()) if VARIANT != 'deep' else None,
    }


def main(variant='mlp'):
    set_variant(variant)
    if variant == 'ind':
        from rules_scan import RULES, state_rule  # noqa: F401  (module-level names used above)
        globals().update(RULES=RULES, state_rule=state_rule)
    sess = sessions_for(variant); fl = fold_list(variant, sess['days'])
    PD = VARIANTS[variant].get('period_days', 1)
    out = {'protocol': PROTOCOL, 'variant': variant, 'inputs': list(groups()), 'markets': {}}
    for mk in VARIANTS[variant].get('markets', PROTOCOL['markets']):
        market = MARKETS[mk]
        res = {'cost_per_side': market.cost, 'funding_per_8h': market.funding, 'positions': market.positions,
               'years': {}, 'training': {}, 'pooled': {}}
        pooled = {}
        for f in fl:
            y = f['year']; models = load_models(mk, y)
            if not models:
                continue
            days = f['test']
            ens = run_policy(market, sess, days, policy_fn(models), keep_obs=True)
            bh = run_policy(market, sess, days, const(market, 1))['daily_ret']
            pred = fit_linear(sess, np.concatenate([f['train'], f['val']]))
            sw = max(ens['turnover'].mean() / ens['held'].shape[1] / 2, 1e-4)
            strategies = {
                'ppo_ensemble': ens,
                **{f'ppo_seed{s}': run_policy(market, sess, days, policy_fn([m])) for s, m in zip(SEEDS, models)},
                'flat': run_policy(market, sess, days, const(market, 0)),
                'intraday_buy_hold': run_policy(market, sess, days, const(market, 1)),
                **({'momentum_1h': run_policy(market, sess, days, sign_rule(market, 'r4')),
                    'momentum_4h': run_policy(market, sess, days, sign_rule(market, 'r16')),
                    'reversion_1h': run_policy(market, sess, days, sign_rule(market, 'r4', flip=True))} if PD == 1 else
                   # swing bars are 4h: r16 = 64h (~2.7 days), r64 = ~10.7 days, dev96 = price vs its 16-day mean
                   {'trend_3d': run_policy(market, sess, days, sign_rule(market, 'r16')),
                    'trend_11d': run_policy(market, sess, days, sign_rule(market, 'r64')),
                    'trend_16d_mean': run_policy(market, sess, days, sign_rule(market, 'dev96')),
                    'reversion_3d': run_policy(market, sess, days, sign_rule(market, 'r16', flip=True))}),
                **({'breakout_guard_4h': run_policy(market, sess, days, state_rule(market, **RULES['breakout_guard_4h'])),
                    'trend_guard_4h': run_policy(market, sess, days, state_rule(market, **RULES['trend_guard_4h'])),
                    'trend_4h_dip_15m': run_policy(market, sess, days, state_rule(market, **RULES['trend_4h_dip_15m']))}
                   if VARIANT == 'ind' else {}),
                'linear_forecast': run_policy(market, sess, days, linear_rule(market, pred)),
                'random_matched': run_policy(market, sess, days, random_matched(market, sw, np.random.default_rng(y))),
            }
            yr = {k: metrics(v, bh, PD) for k, v in strategies.items()}
            for k, v in strategies.items():
                pooled.setdefault(k, []).append(v)
            # cost stress on the frozen ensemble (trained at the base cost)
            yr['cost_stress'] = {}
            for mult in (0.0, 0.5, 1.0, 2.0):
                mm = replace(market, cost=market.cost * mult)
                r = run_policy(mm, sess, days, policy_fn(models))
                yr['cost_stress'][str(mult)] = metrics(r, None, PD)['mean_day_pct']
            yr['signal_ceiling'] = {'linear_ic_test': ic(pred, sess, days),
                                    'linear_ic_train': ic(pred, sess, np.concatenate([f['train'], f['val']]))}
            # how directional is the ensemble's own preference? correlation of P(long)-P(short) with the held bar's return
            X = ens['obs'].reshape(-1, len(INPUTS))
            with torch.no_grad():
                P = torch.stack([torch.softmax(m(torch.as_tensor(X))[0], -1) for m in models]).mean(0).numpy()
            score = P[:, market.positions.index(1)] - (P[:, market.positions.index(-1)] if -1 in market.positions else 0)
            _, bar = bar_returns(sess['open'][days], sess['close'][days])
            rk = lambda v: np.argsort(np.argsort(v))
            yr['signal_ceiling']['ppo_score_ic_test'] = float(np.corrcoef(rk(score), rk(bar.reshape(-1)))[0, 1])
            yr['test_range'] = [str(sess['days'][days[0]]), str(sess['days'][days[-1]])]
            yr['btc_close_to_close_pct'] = 100 * float(sess['close'][days[-1], -1] / sess['open'][days[0], 0] - 1)
            res['years'][str(y)] = yr
            res['training'][str(y)] = [json.loads((MODELS / f'{mk}_{y}_s{s}.json').read_text()) for s in SEEDS
                                       if (MODELS / f'{mk}_{y}_s{s}.json').exists()]
            print(mk, y, {k: round(v['mean_day_pct'], 4) for k, v in yr.items() if isinstance(v, dict) and 'mean_day_pct' in v}, flush=True)
            if y == max(x['year'] for x in fl if load_models(mk, x['year'])):
                res['permutation'] = permutation(models, market, sess, days, ens['daily_log'].mean())
                res['inspection'] = inspect(market, y, models, sess, days, ens)
        if not pooled:
            continue
        # pooled out-of-sample, every test year concatenated
        bh_all = np.concatenate([r['daily_ret'] for r in pooled['intraday_buy_hold']])
        for k, rs in pooled.items():
            cat = {'daily_ret': np.concatenate([r['daily_ret'] for r in rs]),
                   'turnover': np.concatenate([r['turnover'] for r in rs]),
                   'held': np.concatenate([r['held'] for r in rs])}
            res['pooled'][k] = metrics(cat, bh_all, PD)
        out['markets'][mk] = res
    out['data_audit'] = json.loads((OUT / 'data_audit.json').read_text())
    path = OUT / ('results.json' if variant == 'mlp' else f'results_{variant}.json')
    path.write_text(json.dumps(out))
    print('wrote', path)


if __name__ == '__main__':
    main()
