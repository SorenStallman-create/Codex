"""Rebuild an auditable replay from the preserved, checksum-verified experiment."""
import csv
import json
from pathlib import Path
from app import strategy_catalog, make_signals
from research_weekly import validate_rows, simulate, diagnostics, buy_hold, write_csv, sha256, HOUR_MS, iso

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / 'research/weekly_20260917T1100Z'
OUTPUT = ROOT / 'research/repaired_replay'
QUOTAS = {'andean_laguerre': 7, 'mcginley': 3, 'sma': 4, 'ema': 4, 'donchian': 4, 'rsi': 3}

def read(name):
    with (SOURCE / name).open() as f:
        return list(csv.DictReader(f))

def select_balanced(ranks):
    return [r for r in ranks if int(r['selection_rank']) in {
        int(x['selection_rank']) for family, count in QUOTAS.items()
        for x in [r for r in ranks if r['family'] == family][:count]}]

def run():
    manifest = json.loads((SOURCE / 'manifest.json').read_text())
    for name in ('market_data.csv', 'selection_scores.csv'):
        if sha256(SOURCE / name) != manifest['artifacts'][name]['sha256']:
            raise ValueError('Source checksum mismatch: ' + name)
    candles = read('market_data.csv')
    raw = [[int(r['open_time_ms']), r['open'], r['high'], r['low'], r['close'], r['base_volume'],
            int(r['close_time_ms']), r['quote_volume'], r['trade_count'], r['taker_buy_base_volume'],
            r['taker_buy_quote_volume'], '0'] for r in candles]
    bars, _, audit = validate_rows(raw, raw[0][0], raw[-1][0] + HOUR_MS)
    ranks = read('selection_scores.csv')
    cohorts = {'original': ranks[:25], 'family_comparison': select_balanced(ranks)}
    specs = {s.id: s for s in strategy_catalog()}
    selected = {r['id']: r for cohort in cohorts.values() for r in cohort}
    index = {iso(b.time): i for i, b in enumerate(bars)}
    results, trades, decisions, equity, sensitivity = [], [], [], [], []
    payload = {'windows': manifest['window_definitions'], 'cohorts': {k: [r['id'] for r in v] for k,v in cohorts.items()},
               'models': selected, 'runs': [], 'asof': manifest['as_of_utc']}
    for model, rank in selected.items():
        spec = specs[model]
        signals, diag = make_signals(bars, spec), diagnostics(bars, spec)
        for window in manifest['window_definitions']:
            start, end = index[window['start']], index.get(window['end_exclusive'], len(bars))
            result = simulate(bars, signals, diag, start, end, model=model, window=window['id'], collect=True)
            summary = {k:v for k,v in result.items() if k not in ('decision_rows','trades_rows','equity_values')}
            summary.update(name=spec.name, family=spec.family, selection_rank=int(rank['selection_rank']),
                           buy_hold_return_pct=buy_hold(bars,start,end), excess_return_pct=result['net_return_pct']-buy_hold(bars,start,end))
            results.append(summary)
            trades.extend(result['trades_rows']); decisions.extend(result['decision_rows'])
            for n, value in enumerate(result['equity_values']):
                equity.append({'model':model,'window':window['id'],'point':n,'time_utc':iso(bars[start].time+min(n,end-start)*HOUR_MS), 'phase':'settlement' if n==end-start+1 else 'mark', 'equity':value})
            controls=[]
            for axis, cases in [('cost',[(0,0,.1),(.0015,.0005,.1),(.003,.001,.1)]),('allocation',[(.0015,.0005,.05),(.0015,.0005,.1),(.0015,.0005,.2)])]:
                for fee,slip,allocation in cases:
                    x=simulate(bars,signals,diag,start,end,fee=fee,slip=slip,allocation=allocation)
                    row={'model':model,'window':window['id'],'variable':axis,'fee':fee,'slippage':slip,'allocation':allocation,'return_pct':x['net_return_pct'],'trades':x['closed_trades']}
                    controls.append(row); sensitivity.append(row)
            payload['runs'].append({'summary':summary,'decisions':result['decision_rows'],'trades':result['trades_rows'],'equity':result['equity_values'],'controls':controls})
    OUTPUT.mkdir(exist_ok=True)
    for name,rows in [('summary',results),('trades',trades),('decisions',decisions),('equity',equity),('sensitivity',sensitivity)]:
        write_csv(OUTPUT/(name+'.csv'), rows)
    (OUTPUT/'data.json').write_text(json.dumps(payload,allow_nan=False))
    template=(ROOT/'web/replay.html').read_text()
    (OUTPUT/'index.html').write_text(template.replace('/*PAYLOAD*/',json.dumps(payload,allow_nan=False).replace('</','<\\/')))
    provenance={'source':str(SOURCE),'source_data_sha256':sha256(SOURCE/'market_data.csv'),'audit':audit,
        'asof':manifest['as_of_utc'],'selection':'Original cohort retained. Family comparison uses quotas and original pre-period ranks; quotas chosen after seeing results, so exploratory.',
        'family_quotas':QUOTAS,'training_method':'Four fixed 45-day scoring blocks; no rolling refit. Prior walk-forward wording was inaccurate.',
        'replication':'Andean and McGinley are local rule approximations. Exact configured video scripts are unavailable.',
        'boundary':'Starts in cash, follows previous completed candle target at first open, closes any open position at final close. Boundary trades are simulation choices.',
        'source_code':{n:sha256(ROOT/n) for n in ['app.py','research_weekly.py','repair_replay.py','web/replay.html']},
        'artifacts':{p.name:sha256(p) for p in OUTPUT.iterdir() if p.name!='manifest.json'}}
    (OUTPUT/'manifest.json').write_text(json.dumps(provenance,indent=2))
    print(OUTPUT)
    for key, cohort in cohorts.items():
        ids={r['id'] for r in cohort}
        for w in manifest['window_definitions']:
            rs=[r for r in results if r['model'] in ids and r['window']==w['id']]
            print(key,w['id'],'positive',sum(r['net_pnl']>0 for r in rs),'flat',sum(r['net_pnl']==0 for r in rs),'models',len(rs))
if __name__=='__main__': run()
