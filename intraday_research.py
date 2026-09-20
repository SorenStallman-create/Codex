"""Independent-day development/freeze/evaluation for SPY and crypto."""
import argparse,csv,datetime as dt,gzip,json,math,random,statistics
from dataclasses import asdict,replace
from pathlib import Path
from app import Bar
from intraday_data import ROOT,OUT,SYMBOLS,NY,MINUTE,days,day_bounds,load
from intraday_engine import Config,configs,features,simulate,NAMES
from research_weekly import sha256,write_csv,iso

def now():return dt.datetime.now(dt.timezone.utc).isoformat()
def datasets():
 audit=json.loads((OUT/'data_audit.json').read_text());spy_audit=json.loads((OUT/'spy_audit.json').read_text())
 results={}
 for market in ('spy','crypto'):
  if market=='spy':
   path=OUT/'data/SPY_1m.jsonl';assert sha256(path)==spy_audit['data_sha256']
   raw=[json.loads(line) for line in path.read_text().splitlines()];data={'SPY':[Bar(int(r[0]),*map(float,r[1:])) for r in raw]}
   sessions=spy_audit['sessions'];splits={'develop':sessions[1:9],'validation':sessions[9:13],'holdout':sessions[13:]}
   fs={'SPY':{'step':1.,'min_qty':1.,'min_notional':1.,'max_qty':1e9}};cost={'fee':.00005,'slip':.0001}
  else:
   data={s:load(s) for s in SYMBOLS};fs=audit['quantity_filters'];cost={'fee':.001,'slip':.0002}
   for a in audit['audits']:assert sha256(OUT/'data'/(a['symbol']+'_1m.jsonl'))==a['sha256']
   ds=days();excluded=set(audit['excluded_days']);splits={k:[d for d in ds[a:b] if d not in excluded] for k,a,b in [('develop',0,45),('validation',45,60),('holdout',60,90)]}
  grouped={};fm={}
  for s,bars in data.items():
   fm[s]=features(bars);grouped[s]={}
   for b in bars:
    day=dt.datetime.fromtimestamp(b.time/1000,NY).date().isoformat();grouped[s].setdefault(day,[]).append(b)
  results[market]={'bars':grouped,'features':fm,'filters':fs,'splits':splits,'cost':cost,'symbols':list(data)}
 return results

def bounds(market,day):
 if market=='crypto':return day_bounds(day)
 d=dt.date.fromisoformat(day);return tuple(int(dt.datetime.combine(d,t,NY).timestamp()*1000) for t in (dt.time(9,30),dt.time(16)))

def run_day(market,ds,c,day,collect=False):
 start,end=bounds(market,day);capital=10000/len(ds['symbols'])
 runs={s:simulate(ds['bars'][s][day],ds['features'][s],c,ds['filters'][s],start,end,collect,capital) for s in ds['symbols']}
 lengths={len(r['curve']) for r in runs.values()};assert len(lengths)==1
 curve=[sum(x) for x in zip(*(r['curve'] for r in runs.values()))];peak=curve[0];dd=0.
 for v in curve:peak=max(peak,v);dd=max(dd,1-v/peak)
 trades=[{**t,'asset':s} for s,r in runs.items() for t in r['trade_rows']]
 fills=[{**t,'asset':s} for s,r in runs.items() for t in r['fill_rows']]
 summary={'market':market,'model':c.id,'day':day,'start_utc':iso(start),'end_utc':iso(end),'net_pnl':curve[-1]-10000,'return_pct':(curve[-1]/10000-1)*100,
 'drawdown_pct':100*dd,'trades':len(trades),'wins':sum(t['net_pnl']>0 for t in trades),'fees':sum(r['fees'] for r in runs.values()),'slippage':sum(r['slippage'] for r in runs.values()),'exposure_pct':sum(r['mean_exposure_pct'] for r in runs.values())/len(runs)}
 return summary,curve,trades,fills,runs

def score(rows):
 values=[r['return_pct'] for r in rows]
 return statistics.mean(values)-.5*statistics.pstdev(values)-.5*statistics.mean(r['drawdown_pct'] for r in rows)-(.02 if sum(r['trades'] for r in rows)<20 else 0)
def aggregate(rows):
 v=[r['return_pct'] for r in rows]
 return {'days':len(rows),'mean_daily_pct':statistics.mean(v),'median_daily_pct':statistics.median(v),'sum_daily_pnl':sum(r['net_pnl'] for r in rows),
 'best_day_pct':max(v),'worst_day_pct':min(v),'max_day_drawdown_pct':max(r['drawdown_pct'] for r in rows),'positive_days':sum(x>1e-10 for x in v),'flat_days':sum(abs(x)<=1e-10 for x in v),'negative_days':sum(x< -1e-10 for x in v),'trades':sum(r['trades'] for r in rows),'fees':sum(r['fees'] for r in rows),'slippage':sum(r['slippage'] for r in rows)}

def freeze():
 path=OUT/'frozen.json'
 if path.exists():raise RuntimeError('Already frozen; evaluation cannot overwrite selection.')
 data=datasets();development=[];validation=[];record={}
 for market,ds in data.items():
  rankings=[]
  for c0 in configs():
   c=replace(c0,**ds['cost']);rows=[run_day(market,ds,c,day)[0] for day in ds['splits']['develop']]
   development.extend(rows);rankings.append({'model':c.id,'config':asdict(c),'score':score(rows),'summary':aggregate(rows)})
   print(market,c.id,'development',round(rankings[-1]['score'],5),flush=True)
  chosen=[max([r for r in rankings if r['config']['family']==f],key=lambda r:r['score']) for f in NAMES];vranks=[]
  for selected in chosen:
   c=Config(**selected['config']);rows=[run_day(market,ds,c,day)[0] for day in ds['splits']['validation']];validation.extend(rows)
   vranks.append({'model':c.id,'config':asdict(c),'score':score(rows),'summary':aggregate(rows)})
  vranks.sort(key=lambda r:r['score'],reverse=True)
  record[market]={'development_rankings':rankings,'validation_rankings':vranks,'champion':vranks[0]['model'],'configs':[r['config'] for r in chosen],'splits':ds['splits']}
 write_csv(OUT/'development.csv',development);write_csv(OUT/'validation.csv',validation)
 frozen={'created_utc':now(),'markets':record,'hashes':{n:sha256(ROOT/n) for n in ['intraday_engine.py','intraday_data.py','intraday_spy_data.py','intraday_research.py']},'plan_sha256':sha256(OUT/'PLAN.md'),'audit_hashes':{n:sha256(OUT/n) for n in ['data_audit.json','spy_audit.json']}}
 path.write_text(json.dumps(frozen,indent=2));print('FROZEN', {m:r['champion'] for m,r in record.items()},flush=True)

def bootstrap(rows):
 vals=[r['return_pct'] for r in rows];n=len(vals);rng=random.Random(20260918);out=[]
 for _ in range(2000):
  sample=[]
  while len(sample)<n:
   j=rng.randrange(n);sample.extend(vals[(j+k)%n] for k in range(min(3,n)))
  out.append(statistics.mean(sample[:n]))
 out.sort();return {'low_mean_daily_pct':out[50],'high_mean_daily_pct':out[1949],'samples':2000,'block_days':min(3,n),'warning':'Descriptive resampling; very unreliable with five test days, and excludes selection uncertainty.'}

def evaluate():
 frozen=json.loads((OUT/'frozen.json').read_text());assert sha256(OUT/'PLAN.md')==frozen['plan_sha256']
 for n,h in frozen['hashes'].items():assert sha256(ROOT/n)==h,'Frozen implementation changed: '+n
 for n,h in frozen['audit_hashes'].items():assert sha256(OUT/n)==h
 data=datasets();summaries=[];stress=[];trades=[];fills=[];assets=[];report={'created_utc':now(),'freeze':frozen,'markets':{}}
 fields=['market','model','day','asset','minute_ms','minute_utc','equity','cash','qty','stop','target','action','blocked','signal_utc','indicators']
 with gzip.open(OUT/'decisions.csv.gz','wt',newline='') as decisionfile:
  writer=csv.DictWriter(decisionfile,fieldnames=fields);writer.writeheader()
  for market,ds in data.items():
   record=frozen['markets'][market];selected=[Config(**c) for c in record['configs']];by_model={c.id:[] for c in selected};mreport={'daily':[],'models':[],'stress':[],'days':record['splits']['holdout']}
   target=OUT/'days'/market;target.mkdir(parents=True,exist_ok=True)
   for day in record['splits']['holdout']:
    start,end=bounds(market,day);detail={'day':day,'market':market,'start_ms':start,'end_ms':end,'symbols':ds['symbols'],'candles':{},'features':{},'models':{}}
    for s in ds['symbols']:
     detail['candles'][s]=[[b.time,b.open,b.high,b.low,b.close,b.volume] for b in ds['bars'][s][day]]
     detail['features'][s]=[f for t,f in ds['features'][s].items() if start<=t<end]
    for c in selected:
     summary,curve,ts,fs,rs=run_day(market,ds,c,day,True);summaries.append(summary);by_model[c.id].append(summary);mreport['daily'].append(summary)
     detail['models'][c.id]={'summary':summary,'curve':[round(v,5) for v in curve],'trades':ts,'fills':fs}
     tag={'market':market,'model':c.id,'day':day};trades.extend({**tag,**t} for t in ts);fills.extend({**tag,**f} for f in fs)
     for s,r in rs.items():
      for row in r['decision_rows']:writer.writerow({**tag,'asset':s,**row})
      assets.append({**tag,'asset':s,**{k:v for k,v in r.items() if k not in ('curve','trade_rows','fill_rows','decision_rows')}})
    (target/(day+'.json')).write_text(json.dumps(detail,separators=(',',':'),allow_nan=False))
   for c in selected:
    base=aggregate(by_model[c.id]);unc=bootstrap(by_model[c.id]);scenarios={}
    adverse=replace(c,fee=.0015,slip=.0005) if market=='crypto' else replace(c,fee=c.fee*5,slip=c.slip*5)
    for name,variant in [('zero_cost',replace(c,fee=0,slip=0)),('double_cost',replace(c,fee=c.fee*2,slip=c.slip*2)),('adverse_cost',adverse),('one_minute_delay',replace(c,delay=1))]:
     rows=[run_day(market,ds,variant,day)[0] for day in record['splits']['holdout']];a=aggregate(rows);scenarios[name]=a
     stress.extend({**r,'scenario':name} for r in rows);mreport['stress'].append({'model':c.id,'scenario':name,**a})
    checks={'positive_mean':base['mean_daily_pct']>0,'at_least_30_trades':base['trades']>=30,'positive_bootstrap_lower':unc['low_mean_daily_pct']>0,'positive_double_cost':scenarios['double_cost']['mean_daily_pct']>0,'positive_delayed':scenarios['one_minute_delay']['mean_daily_pct']>0,'daily_drawdown_at_most_2pct':base['max_day_drawdown_pct']<=2}
    mreport['models'].append({'id':c.id,'name':NAMES[c.family],'config':asdict(c),'summary':base,'uncertainty':unc,'gate':checks,'passes':all(checks.values())})
    print(market,c.id,'mean/day',round(base['mean_daily_pct'],4),'P&L',round(base['sum_daily_pnl'],2),'trades',base['trades'],'passes',all(checks.values()),flush=True)
   report['markets'][market]=mreport
 for name,rows in [('daily_summary',summaries),('stress_daily',stress),('trades',trades),('fills',fills),('asset_daily',assets)]:write_csv(OUT/(name+'.csv'),rows)
 (OUT/'results.json').write_text(json.dumps(report,indent=2,allow_nan=False));print('Evaluation saved.',flush=True)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('stage',choices=['freeze','evaluate']);a=p.parse_args();freeze() if a.stage=='freeze' else evaluate()
