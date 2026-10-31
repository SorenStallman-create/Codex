"""Build the Round 6 edge-map page from artifacts/edge_map.json (pre-registered run) and
artifacts/edge_by_year_exploratory.json (the per-year look taken after the test data was seen).

    python3 build_edge_page.py   ->  artifacts/edge_map.html
"""
import json
from pathlib import Path

from data import OUT

HERE = Path(__file__).parent


def main():
    em = json.loads((OUT / 'edge_map.json').read_text())
    by_year = json.loads((OUT / 'edge_by_year_exploratory.json').read_text())
    data = {
        'costs': em['costs_round_trip_bp'],
        'horizons': {h: {'ic': v['ic'], 'curve': v['coverage_curve'], 'deciles': v['decile_mean_bp'],
                         'vol': v['by_vol_tercile_top10_gross_bp'], 'hour': v['by_hour_top10_gross_bp']}
                     for h, v in em['edge_map'].items()},
        'primary': {m: {k: v[k] for k in ('mean_day_pct', 'ci95', 'by_year_pct_day', 'trades', 'hit_rate_pct',
                                          'placebo_percentile', 'gates', 'yearly_tax')}
                    for m, v in em['primary'].items()},
        'by_year': by_year,
    }
    html = (HERE / 'edge_map_template.html').read_text().replace('/*DATA*/null', json.dumps(data))
    path = OUT / 'edge_map.html'
    path.write_text(html)
    print('wrote', path, f'{path.stat().st_size / 1e3:.0f} kB')


if __name__ == '__main__':
    main()
