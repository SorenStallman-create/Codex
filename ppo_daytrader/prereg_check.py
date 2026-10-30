"""Round 5 pass/fail gates from prereg/ROUND5_PREREG.md, scored once after evaluation:
bootstrap bound, paired vs intraday buy-and-hold, placebo rank against random timing with
matched turnover, sub-period count, 10 and 25 bp/side cost stress, and an after-tax view.

    python3 prereg_check.py --variant ext
"""
import argparse, json
from dataclasses import replace
import numpy as np

import evaluate as ev
from env import MARKETS, run_policy
from ppo import policy_fn
from walkforward import fold_list, sessions_for
from data import OUT

TAX_ST = 0.2975  # Virginia short-term rate from the Mosaic context


def after_tax(daily, years):
    """Tax positive calendar-year gains at the short-term rate; a losing year carries its loss forward."""
    out, carry = {}, 0.0
    for y in sorted(set(years)):
        g = float(np.prod(1 + daily[years == y]) - 1)
        taxable = g + carry
        tax = TAX_ST * taxable if taxable > 0 else 0.0
        carry = min(taxable, 0.0)
        out[int(y)] = {'before_tax_pct': 100 * g, 'after_tax_pct': 100 * (g - tax)}
    return out


def main(variant, n_placebo=200):
    ev.set_variant(variant)
    if variant in ('ind', 'ext'):
        import rules_scan; rules_scan.F = ev.F
    sess = sessions_for(variant); fl = fold_list(variant, sess['days'])
    report = {'variant': variant, 'markets': {}}
    for mk in ('spot', 'perp'):
        m = MARKETS[mk]
        rows = {'daily': [], 'bh': [], 'turn': [], 'years': [], 'c10': [], 'c25': []}
        placebo = np.zeros(n_placebo); n_days = 0
        for f in fl:
            models = ev.load_models(mk, f['year'])
            if not models:
                continue
            days = f['test']; act = policy_fn(models)
            r = run_policy(m, sess, days, act)
            rows['daily'].append(r['daily_ret']); rows['turn'].append(r['turnover'])
            rows['bh'].append(run_policy(m, sess, days, ev.const(m, 1))['daily_ret'])
            rows['years'].append(np.full(len(days), f['year']))
            for k, c in (('c10', 0.0010), ('c25', 0.0025)):
                rows[k].append(run_policy(replace(m, cost=c), sess, days, act)['daily_ret'])
            sw = max(r['turnover'].mean() / r['held'].shape[1] / 2, 1e-4)
            for p in range(n_placebo):
                pr = run_policy(m, sess, days, ev.random_matched(m, sw, np.random.default_rng(10_000 * f['year'] + p)))
                placebo[p] += pr['daily_ret'].sum()
            n_days += len(days)
        cat = {k: np.concatenate(v) for k, v in rows.items()}
        d = cat['daily']; diff = d - cat['bh']
        by_year = {int(y): 100 * float(d[cat['years'] == y].mean()) for y in np.unique(cat['years'])}
        plc = placebo / n_days
        gates = {
            '1_mean_ci_above_zero': ev.block_boot(d)[0] > 0,
            '2_beats_intraday_bh': ev.block_boot(diff)[0] > 0,
            '3_placebo_rank_95': float((plc < d.mean()).mean()) >= 0.95,
            '4_positive_years_4_of_6': sum(v > 0 for v in by_year.values()) >= 4,
            '5_positive_at_10_and_25bp': bool(cat['c10'].mean() > 0 and cat['c25'].mean() > 0),
        }
        report['markets'][mk] = {
            'mean_day_pct': 100 * float(d.mean()), 'ci95': [100 * x for x in ev.block_boot(d)],
            'vs_bh_ci95': [100 * x for x in ev.block_boot(diff)], 'trades_per_day': float(cat['turn'].mean()),
            'placebo_percentile': 100 * float((plc < d.mean()).mean()),
            'placebo_mean_day_pct': 100 * float(plc.mean()), 'placebo_p95_day_pct': 100 * float(np.percentile(plc, 95)),
            'by_year_pct_day': by_year, 'cost10_mean_day_pct': 100 * float(cat['c10'].mean()),
            'cost25_mean_day_pct': 100 * float(cat['c25'].mean()),
            'yearly_tax': after_tax(d, cat['years']), 'gates': {k: bool(v) for k, v in gates.items()},
            'passes_all': bool(all(gates.values())),
        }
        print(mk, json.dumps({k: v for k, v in report['markets'][mk].items() if k != 'yearly_tax'}, indent=1), flush=True)
    (OUT / f'prereg_{variant}.json').write_text(json.dumps(report, indent=1))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--variant', default='ext')
    main(ap.parse_args().variant)
