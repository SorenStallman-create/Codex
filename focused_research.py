"""Run the preregistered development, freeze, and historical evaluation stages."""
from __future__ import annotations
import argparse
import csv
import datetime as dt
import json
import math
import random
import statistics
from dataclasses import asdict,replace
from pathlib import Path
from app import StrategySpec,make_signals
from focused_data import ROOT,OUT,END,SYMBOLS,load,filters
from focused_engine import Config,features,configurations,simulate,max_dd,SLEEVE_CAPITAL,FAMILIES
from research_weekly import write_csv,sha256,iso,HOUR_MS,simulate as old_simulate


def stamp(s):return int(dt.datetime.fromisoformat(s.replace('Z','+00:00')).replace(tzinfo=dt.timezone.utc).timestamp()*1000)
def now():return dt.datetime.now(dt.timezone.utc).isoformat()
def slim(result):return {k:v for k,v in result.items() if k not in ('curve','times','trade_rows','fill_rows','decision_rows','state')}
def datasets():
    audit=json.loads((OUT/'data_audit.json').read_text());data={};fm={}
    for symbol in SYMBOLS:
        a=next(a for a in audit['audits'] if a['symbol']==symbol)
        if sha256(OUT/'data'/(symbol+'_1h.jsonl'))!=a['sha256']:raise ValueError('Data hash changed')
        data[symbol]=load(symbol)
        fm[symbol]=features(data[symbol],{stamp(x['open_utc']) for x in a['shortened_candles']})
    return data,fm,filters(),audit

def score(rows):
    values=[r['return_pct']-1.5*r['max_drawdown_pct'] for r in rows]
    return statistics.median(values)-.25*statistics.pstdev(values)-2*sum(r['trades']<5 for r in rows)

def period(data,fm,fs,config,start,end,collect=False):
    return {s:simulate(data[s],fm[s],config,fs[s],start,end,collect=collect) for s in SYMBOLS}

def portfolio(runs):
    times=runs[SYMBOLS[0]]['times']
    if any(r['times']!=times for r in runs.values()):raise ValueError('Asset clocks differ; require explicit valuation alignment')
    curve=[sum(r['curve'][i] for r in runs.values()) for i in range(len(times))]
    trades=[t for r in runs.values() for t in r['trade_rows']]
    pnl=[t['net_pnl'] for t in trades];loss=-sum(p for p in pnl if p<0)
    return {'return_pct':100*(curve[-1]/10000-1),'net_pnl':curve[-1]-10000,'ending_equity':curve[-1],
            'max_drawdown_pct':max_dd(curve),'trades':len(trades),'profitable_assets':sum(r['net_pnl']>0 for r in runs.values()),
            'profit_factor':sum(p for p in pnl if p>0)/loss if loss else None,
            'win_rate_pct':100*sum(p>0 for p in pnl)/len(pnl) if pnl else None,
            'fees':sum(r['fees'] for r in runs.values()),'slippage':sum(r['slippage'] for r in runs.values()),
            'turnover':sum(r['turnover'] for r in runs.values())/3,
            'mean_capital_exposure_pct':sum(r['mean_capital_exposure_pct'] for r in runs.values())/3,
            'halted_sleeves':sum(r['halted'] for r in runs.values()),'curve':curve,'times':times}

def daily_path(curve,times):
    points={}
    for t,v in zip(times[1:],curve[1:]):points[iso(t-1)[:10]]=v
    return [{'date':day,'equity':v} for day,v in sorted(points.items())]

def uncertainty(p):
    points=daily_path(p['curve'],p['times']);prev=10000;returns=[]
    for point in points:returns.append(point['equity']/prev-1);prev=point['equity']
    rng=random.Random(20260918);n=len(returns);draws=[]
    for _ in range(2000):
        sampled=[]
        while len(sampled)<n:
            start=rng.randrange(n);sampled.extend(returns[(start+j)%n] for j in range(7))
        draws.append(100*(math.prod(1+x for x in sampled[:n])-1))
    draws.sort()
    return {'method':'Circular 7-day blocks; 2000 samples; iid blocks assumed, selection uncertainty not included',
            'lower_95_pct':draws[int(.025*len(draws))],'upper_95_pct':draws[int(.975*len(draws))-1],
            'positive_resample_fraction':sum(x>0 for x in draws)/len(draws),'days':n,
            'mean_daily_return_pct':100*statistics.mean(returns),'median_daily_return_pct':100*statistics.median(returns),
            'daily_std_pct':100*statistics.pstdev(returns)}

def develop():
    freeze_path=OUT/'frozen_selection.json'
    if freeze_path.exists():raise RuntimeError('Selection already frozen. Use evaluate; do not silently overwrite the protocol.')
    data,fm,fs,audit=datasets();rows=[];rank=[]
    for c in configurations():
        local=[]
        for year in (2021,2022,2023):
            runs=period(data,fm,fs,c,stamp(f'{year}-01-01'),stamp(f'{year+1}-01-01'))
            local.extend({**slim(r),'family':c.family,'setting':c.setting,'asset':s,'period':str(year)} for s,r in runs.items())
        rows.extend(local);rank.append({'id':c.id,'family':c.family,'setting':c.setting,'score':score(local)})
        print('Development',c.id,'score',round(rank[-1]['score'],3),flush=True)
    write_csv(OUT/'development.csv',rows);write_csv(OUT/'development_ranking.csv',sorted(rank,key=lambda r:r['score'],reverse=True))
    selected=[max([r for r in rank if r['family']==f],key=lambda r:(r['score'],-r['setting'])) for f in FAMILIES]
    validation=[];vranks=[];stability=[]
    for c in configurations():
        runs=period(data,fm,fs,c,stamp('2024-01-01'),stamp('2025-01-01'))
        local=[{**slim(r),'family':c.family,'setting':c.setting,'asset':s,'period':'2024'} for s,r in runs.items()]
        stability.extend(local)
        if any(c.id==r['id'] for r in selected):
            validation.extend(local);p=portfolio(runs)
            vranks.append({'id':c.id,'family':c.family,'setting':c.setting,'score':score(local),'portfolio':slim(p)})
    vranks.sort(key=lambda r:(r['score'],-r['setting']),reverse=True)
    write_csv(OUT/'validation.csv',validation);write_csv(OUT/'validation_parameter_stability.csv',stability)
    frozen={'created_utc':now(),'plan_sha256':sha256(OUT/'TEST_PLAN.md'),'engine_sha256':sha256(ROOT/'focused_engine.py'),
            'runner_sha256':sha256(ROOT/'focused_research.py'),'data_audit_sha256':sha256(OUT/'data_audit.json'),
            'selected_configs':[asdict(Config(r['family'],r['setting'])) for r in selected],
            'validation_ranking':vranks,'champion_id':vranks[0]['id'],
            'holdout_start':'2025-01-01T00:00:00Z','holdout_end_exclusive':'2025-11-19T00:00:00Z',
            'selection_note':'Settings use 2021-2023 only. Champion uses 2024 only. Parameter-neighbor 2024 tests are diagnostic; no settings changed.'}
    freeze_path.write_text(json.dumps(frozen,indent=2,allow_nan=False))
    print('FROZEN',frozen['champion_id'],'before holdout',flush=True)


def control_runs(data,start,end):
    result=[]
    for allocation in (.1,.5):
        curves=[];tot_trades=0
        for symbol in SYMBOLS:
            bars=data[symbol];i0=next(i for i,b in enumerate(bars) if b.time>=start);i1=next((i for i,b in enumerate(bars) if b.time>=end),len(bars))
            signals=make_signals(bars,StrategySpec('control','sma','SMA 20/50',{'fast':20,'slow':50}))
            r=old_simulate(bars,signals,[{}]*len(bars),i0,i1,allocation=allocation)
            # Old runner includes duplicate terminal point; retain final net settlement.
            curve=r['equity_values'][:-1];curve[-1]=r['equity_values'][-1]
            curves.append([v/3 for v in curve]);tot_trades+=r['closed_trades']
        curve=[sum(vals) for vals in zip(*curves)]
        result.append({'name':f'Original hourly SMA20/50 · {allocation:.0%} entry allocation','return_pct':100*(curve[-1]/10000-1),'max_drawdown_pct':max_dd(curve),'trades':tot_trades,'note':'No stops or volatility risk sizing; exposure cap comparison only.'})
    curves=[]
    for symbol in SYMBOLS:
        bars=[b for b in data[symbol] if start<=b.time<end];budget=SLEEVE_CAPITAL*.5;qty=budget/(bars[0].open*1.0005);cash=SLEEVE_CAPITAL-budget*1.0015
        curve=[SLEEVE_CAPITAL]+[cash+qty*b.close for b in bars];curve[-1]=cash+qty*bars[-1].close*.9995*.9985;curves.append(curve)
    curve=[sum(vals) for vals in zip(*curves)]
    result.append({'name':'Buy & hold · 50% initial allocation','return_pct':100*(curve[-1]/10000-1),'max_drawdown_pct':max_dd(curve),'trades':3,'note':'Equal initial asset sleeves; no rebalance; allocation drifts with price.'})
    result.append({'name':'Cash','return_pct':0,'max_drawdown_pct':0,'trades':0,'note':'No interest.'})
    return result


def evaluate():
    frozen=json.loads((OUT/'frozen_selection.json').read_text())
    for path,key in [(OUT/'TEST_PLAN.md','plan_sha256'),(ROOT/'focused_engine.py','engine_sha256'),(OUT/'data_audit.json','data_audit_sha256')]:
        if sha256(path)!=frozen[key]:raise ValueError('Frozen input changed: '+str(path))
    data,fm,fs,audit=datasets();configs=[Config(**c) for c in frozen['selected_configs']]
    periods={'holdout':(stamp(frozen['holdout_start']),stamp(frozen['holdout_end_exclusive'])),
             'regression':(stamp(frozen['holdout_end_exclusive']),END)}
    report={'created_utc':now(),'freeze':frozen,'data_audit':audit,'runs':[],'stress':[],'ablations':[],'controls':{},'gates':[]}
    summaries=[];all_trades=[];all_fills=[];all_decisions=[];all_equity=[]
    cached={}
    for period_id,(start,end) in periods.items():
        report['controls'][period_id]=control_runs(data,start,end)
        for c in configs:
            runs=period(data,fm,fs,c,start,end,collect=True);p=portfolio(runs);cached[(period_id,c.id)]=p
            rows=[{**slim(r),'asset':s,'period':period_id,'family':c.family,'setting':c.setting} for s,r in runs.items()]
            summaries.extend(rows)
            for symbol,r in runs.items():
                tag={'period':period_id,'model':c.id,'asset':symbol}
                all_trades.extend({**tag,**t} for t in r['trade_rows']);all_fills.extend({**tag,**t} for t in r['fill_rows']);all_decisions.extend({**tag,**t} for t in r['decision_rows'])
                all_equity.extend({**tag,'time_utc':iso(t),'equity':v} for t,v in zip(r['times'],r['curve']))
            u=uncertainty(p) if period_id=='holdout' else None
            report['runs'].append({'period':period_id,'start':iso(start),'end_exclusive':iso(end),'model':c.id,'name':FAMILIES[c.family],
                                   'summary':slim(p),'assets':rows,'daily':daily_path(p['curve'],p['times']),'uncertainty':u,
                                   'trades':[{**t,'asset':s} for s,r in runs.items() for t in r['trade_rows']]})
            print(period_id,c.id,'return',round(p['return_pct'],3),'DD',round(p['max_drawdown_pct'],3),'trades',p['trades'],flush=True)
    start,end=periods['holdout']
    for c in configs:
        for name,variant in [('zero_cost',replace(c,fee=0,slip=0)),('double_cost',replace(c,fee=c.fee*2,slip=c.slip*2)),('one_hour_delay',replace(c,delay=1))]:
            p=portfolio(period(data,fm,fs,variant,start,end));report['stress'].append({'model':c.id,'scenario':name,**slim(p)})
    champion=next(c for c in configs if c.id==frozen['champion_id'])
    for name,variant in [('no_regime_filter',replace(champion,regime=False)),('no_stop',replace(champion,stops=False)),('fixed_50pct_size',replace(champion,risk_sizing=False))]:
        p=portfolio(period(data,fm,fs,variant,start,end));report['ablations'].append({'model':champion.id,'scenario':name,**slim(p)})
    for run in [r for r in report['runs'] if r['period']=='holdout']:
        s=run['summary'];stresses={r['scenario']:r for r in report['stress'] if r['model']==run['model']}
        checks={'positive_net_return':s['return_pct']>0,'two_positive_assets':s['profitable_assets']>=2,'at_least_30_trades':s['trades']>=30,
                'drawdown_at_most_15pct':s['max_drawdown_pct']<=15,'positive_double_cost':stresses['double_cost']['return_pct']>0,
                'positive_one_hour_delay':stresses['one_hour_delay']['return_pct']>0,'bootstrap_lower_above_zero':run['uncertainty']['lower_95_pct']>0}
        report['gates'].append({'model':run['model'],'passes':all(checks.values()),'checks':checks})
    for name,rows in [('summary',summaries),('trades',all_trades),('fills',all_fills),('decisions',all_decisions),('equity',all_equity),('stress',report['stress']),('ablations',report['ablations'])]:write_csv(OUT/(name+'.csv'),rows)
    (OUT/'results.json').write_text(json.dumps(report,indent=2,allow_nan=False))
    (OUT/'evidence_manifest.json').write_text(json.dumps({'created_utc':now(),'files':{p.name:{'bytes':p.stat().st_size,'sha256':sha256(p)} for p in OUT.iterdir() if p.suffix in ('.csv','.json','.md') and p.name!='evidence_manifest.json'}},indent=2))
    print('GATES',json.dumps(report['gates']),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['develop','evaluate']);args=parser.parse_args()
    develop() if args.stage=='develop' else evaluate()
