"""Render artifacts/results.json into a single self-contained HTML report."""
import json
from pathlib import Path
from data import OUT

ROOT = Path(__file__).resolve().parent
KEEP_HIST = ('update', 'train_day_log_pct', 'val_day_log_pct', 'ent', 'trades_per_day', 'explained_var', 'kl', 'clipfrac', 'vf')


def slim(res):
    for mk in res['markets'].values():
        for y, runs in mk['training'].items():
            mk['training'][y] = [{'meta': {k: r['meta'][k] for k in ('seed', 'best_update', 'best_val_day_log_pct', 'train_range', 'val_range', 'train_days', 'val_days', 'seconds')},
                                  'summary': r['meta']['summary'], 'config': r['meta']['config'],
                                  'history': [{k: round(h[k], 5) for k in KEEP_HIST if k in h} for h in r['history']]}
                                 for r in runs]
        for y, yr in mk['years'].items():
            for k, v in yr.items():
                if isinstance(v, dict) and 'equity' in v:
                    v.pop('equity')
        for v in mk['pooled'].values():
            v['equity'] = [float(f'{e:.5g}') for e in v['equity']]
    return res


def main():
    models = {'mlp': slim(json.loads((OUT / 'results.json').read_text()))}
    if (OUT / 'results_deep.json').exists():
        models['deep'] = slim(json.loads((OUT / 'results_deep.json').read_text()))
    narrative = json.loads((ROOT / 'narrative.json').read_text()) if (ROOT / 'narrative.json').exists() else {}
    tpl = (ROOT / 'report_template.html').read_text()
    html = tpl.replace('/*__DATA__*/null', json.dumps({'models': models, **narrative}, separators=(',', ':')))
    (OUT / 'report.html').write_text(html)
    print('wrote', OUT / 'report.html', f'{len(html) / 1e6:.2f} MB')


if __name__ == '__main__':
    main()
