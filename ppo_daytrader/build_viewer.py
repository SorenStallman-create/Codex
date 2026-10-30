"""Bundle exported network recordings (export_viewer.py) into artifacts/network_viewer.html."""
import json, sys
from pathlib import Path
from data import OUT

ROOT = Path(__file__).resolve().parent
# (file stem, chip label, one-line description); missing files are skipped
MODELS = [
    ('viewer_ind_perp_nofee_2026', 'Perp · zero fees', 'Long/short perp with the Trend/Breakout/Pullback Guard indicators as inputs, trained and played with no fees, slippage or funding. It trades a lot and makes money here, but it is a diagnostic: real fees would wipe this out'),
    ('viewer_ind_perp_2022', 'Perp · real fees', 'The same 44-input network trained with real perp costs (0.06% per side plus funding). It learned that almost no trade clears the fee, so it stays flat on most days. The sample days below are days it did trade'),
    ('viewer_ind_spot_nofee_2026', 'Spot · zero fees', 'Long/flat spot with the Guard indicators, no fees (diagnostic)'),
    ('viewer_mlp_perp_2026', 'Perp · first network', 'The first compact network: 17 price, volume and calendar inputs, long/short perp at real fees'),
]


def main():
    models = []
    for stem, short, title in MODELS:
        p = OUT / f'{stem}.json'
        if p.exists():
            models.append({**json.loads(p.read_text()), 'short': short, 'title': title})
    html = (ROOT / 'viewer_template.html').read_text().replace('/*__DATA__*/null', json.dumps({'models': models}, separators=(',', ':')))
    (OUT / 'network_viewer.html').write_text(html)
    print('wrote', OUT / 'network_viewer.html', f'{len(html) / 1e6:.2f} MB', [m['short'] for m in models])


if __name__ == '__main__':
    main()
