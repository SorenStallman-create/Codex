"""Prespecified development, freeze, stress and transfer study."""
import argparse,csv,datetime as dt,gzip,json,random,statistics
from dataclasses import asdict,replace
from intraday_v2_data import ROOT,OUT,STEP,MINUTE,digest,NY,validate
from intraday_v2_engine import Config,candidates,features,run_day,NAMES
CODE=['intraday_v2_data.py','intraday_v2_engine.py','intraday_v2_research.py']
def write_json(p,v):p.write_text(json.dumps(v,indent=2,allow_nan=False))
def csvfile(p,rows):
 if not rows:return
 with p.open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
def small(r):return {k:v for k,v in r.items() if k not in ('curve','trade_rows','fills','decisions')}
def datasets():
 audit=json.loads((OUT/'data_audit.json').read_text());data={}
 for s in ('SPY','QQQ'):
  p=OUT/'data'/f'{s}.json';assert digest(p)==audit['audits'][s]['data_sha256'];data[s]=json.loads(p.read_text())
 fm={s:features(g) for s,g in data.items()};days=sorted(set(fm['SPY'])&set(fm['QQQ']));n=len(days);a=n//2;b=a+n//4
 return data,fm,{'development':days[:a],'validation':days[a:b],'evaluation':days[b:]}
def score(rows):
 x=[r['return_pct'] for r in rows]
 return statistics.mean(x)-.5*statistics.pstdev(x)-.5*statistics.mean(r['drawdown_pct'] for r in rows)-(.02 if sum(r['trades'] for r in rows)<5 else 0)
def summary(rows):
 x=[r['pnl'] for r in rows];n=len(x);turn=sum(r['turnover'] for r in rows);pnl=sum(x)
 return {'days':n,'mean_pnl':statistics.mean(x),'median_pnl':statistics.median(x),'total_pnl':pnl,'best_pnl':max(x),'worst_pnl':min(x),'positive':sum(v>1e-8 for v in x),'negative':sum(v< -1e-8 for v in x),'flat':sum(abs(v)<=1e-8 for v in x),'trades':sum(r['trades'] for r in rows),'fees':sum(r['fees'] for r in rows),'slippage':sum(r['slippage'] for r in rows),'max_daily_dd_pct':max(r['drawdown_pct'] for r in rows),'max_adverse_bar_mark_pct':max(r['adverse_bar_mark_pct'] for r in rows),'exposure_pct':statistics.mean(r['exposure_pct'] for r in rows),'leave_best_day_out_pnl':pnl-max(x),'extra_cost_break_even_bps_per_side':10000*pnl/turn if turn else None}
def bootstrap(rows):
 vals=[r['pnl'] for r in rows];rng=random.Random(20260918);n=len(vals);means=[]
 for _ in range(5000):
  sample=[]
  while len(sample)<n:
   j=rng.randrange(n);sample.extend(vals[(j+k)%n] for k in range(min(3,n)))
  means.append(statistics.mean(sample[:n]))
 means.sort();return [means[125],means[4874]]
def freeze():
 if (OUT/'frozen.json').exists():raise RuntimeError('Freeze already exists; do not overwrite after inspecting outcomes')
 data,fm,splits=datasets();rank=[];allrows=[]
 for c in candidates():
  rows=[small(run_day(c,d,data['SPY'][d],fm['SPY'][d])) for d in splits['development']]
  rank.append({'config':asdict(c),'score':score(rows),'summary':summary(rows)});allrows.extend({'phase':'development',**r} for r in rows)
 selected=[max([r for r in rank if r['config']['family']==f],key=lambda x:x['score']) for f in ('noise','orb','late')];vr=[]
 for r in selected:
  c=Config(**r['config']);rows=[small(run_day(c,d,data['SPY'][d],fm['SPY'][d])) for d in splits['validation']]
  vr.append({'config':asdict(c),'score':score(rows),'summary':summary(rows)});allrows.extend({'phase':'validation',**r} for r in rows)
 chosen=max(vr,key=lambda x:x['score']);frozen={'created_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'splits':splits,'all_candidate_development':rank,'validation':vr,'selected':[r['config'] for r in selected],'primary':Config(**chosen['config']).id,'hashes':{n:digest(ROOT/n) for n in CODE},'plan_hash':digest(OUT/'PLAN.md'),'audit_hash':digest(OUT/'data_audit.json'),'status':'EXPLORATORY; previously seen SPY dates; not untouched OOS'}
 write_json(OUT/'frozen.json',frozen);csvfile(OUT/'selection.csv',allrows);print(json.dumps(frozen,indent=2))
def evaluate():
 frozen=json.loads((OUT/'frozen.json').read_text())
 assert frozen['plan_hash']==digest(OUT/'PLAN.md') and frozen['audit_hash']==digest(OUT/'data_audit.json')
 for n,h in frozen['hashes'].items():assert h==digest(ROOT/n),f'Frozen source changed: {n}'
 data,fm,splits=datasets();cs=[Config(**c) for c in frozen['selected']];controls=[Config(f,0) for f in ('cash','session','closing')]
 details={};daily=[];trades=[];fills=[];stresses=[];ablations=[];markets={};decisions=[]
 for s in data:
  details[s]={};markets[s]={}
  for phase,days in splits.items():
   for d in days:
    detail={'bars':data[s][d],'models':{}}
    for c in cs+controls:
     r=run_day(c,d,data[s][d],fm[s][d]);tag={'symbol':s,'phase':phase};daily.append({**tag,**small(r)})
     detail['models'][c.id]={k:v for k,v in r.items() if k!='decisions'}
     trades.extend({'symbol':s,'phase':phase,'day':d,'model':c.id,**{k:(json.dumps(v) if k=='signal' else v) for k,v in t.items()}} for t in r['trade_rows'])
     fills.extend({'symbol':s,'phase':phase,'day':d,'model':c.id,**f} for f in r['fills'])
     decisions.extend({'symbol':s,'phase':phase,'day':d,'model':c.id,**row} for row in r['decisions'])
    details[s][d]=detail
  for c in cs:
   scenarios={'zero_cost':replace(c,fee=0,slip=0),'double_cost':replace(c,fee=c.fee*2,slip=c.slip*2),'five_times_cost':replace(c,fee=c.fee*5,slip=c.slip*5),'delay_5min':replace(c,delay=5)}
   for name,sc in scenarios.items():
    for d in splits['evaluation']:stresses.append({'symbol':s,'scenario':name,**small(run_day(sc,d,data[s][d],fm[s][d]))})
   rows=[r for r in daily if r['symbol']==s and r['phase']=='evaluation' and r['model']==c.id];sm=summary(rows);sm['ci_mean_pnl']=bootstrap(rows)
   sm['stress']={name:summary([r for r in stresses if r['symbol']==s and r['model']==c.id and r['scenario']==name]) for name in scenarios}
   baseline='closing-0' if c.family=='late' else 'session-0';control=[r for r in daily if r['symbol']==s and r['phase']=='evaluation' and r['model']==baseline]
   sm['excess_vs_control_pnl']=sm['total_pnl']-sum(r['pnl'] for r in control);sm['control']=baseline
   sm['gates']={'120_sessions':sm['days']>=120,'100_trades':sm['trades']>=100,'positive_mean':sm['mean_pnl']>0,'positive_lower_ci':sm['ci_mean_pnl'][0]>0,'double_cost_positive':sm['stress']['double_cost']['total_pnl']>0,'delay_positive':sm['stress']['delay_5min']['total_pnl']>0,'beats_control':sm['excess_vs_control_pnl']>0}
   sm['qualified']=all(sm['gates'].values());markets[s][c.id]=sm
  primary=next(c for c in cs if c.id==frozen['primary'])
  for name,c in {'without_vwap':replace(primary,vwap_gate=False),'up_to_4_entries':replace(primary,max_entries=4),'without_strength_filter':replace(primary,strength_gate=False)}.items():
   for d in splits['evaluation']:ablations.append({'symbol':s,'ablation':name,**small(run_day(c,d,data[s][d],fm[s][d]))})
 # Identical 5m signals, different historical execution resolution on audited overlapping SPY days.
 groups={};minute_path=ROOT/'research/intraday_v1/data/SPY_1m.jsonl'
 for line in minute_path.read_text().splitlines():
  b=json.loads(line);d=dt.datetime.fromtimestamp(b[0]/1000,NY).date().isoformat();groups.setdefault(d,[]).append(b)
 resolution=[];overlap=[]
 for d,bs in groups.items():
  if d not in details['SPY']:continue
  validate(d,bs,MINUTE)
  agg=[[bs[j][0],bs[j][1],max(x[2] for x in bs[j:j+5]),min(x[3] for x in bs[j:j+5]),bs[j+4][4],sum(x[5] for x in bs[j:j+5])] for j in range(0,len(bs),5)]
  maxdiff=max(abs(x[k]-y[k]) for x,y in zip(agg,data['SPY'][d]) for k in (1,2,3,4));vol_diff=sum(x[5]-y[5] for x,y in zip(agg,data['SPY'][d]))
  overlap.append({'day':d,'max_ohlc_difference':maxdiff,'one_min_minus_five_min_volume':vol_diff})
  for c in cs:
   r=run_day(c,d,bs,fm['SPY'][d],MINUTE);base=details['SPY'][d]['models'][c.id]
   resolution.append({'day':d,'model':c.id,'one_min_pnl':r['pnl'],'five_min_pnl':base['pnl'],'difference':r['pnl']-base['pnl'],'one_min_trades':r['trades'],'five_min_trades':base['trades']})
 result={'created_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'freeze':frozen,'markets':markets,'daily':daily,'stress':stresses,'ablations':ablations,'resolution':resolution,'overlap':overlap,'details':details,'names':NAMES,'qualified_any':any(m['qualified'] for v in markets.values() for m in v.values()),'live_status':'Historical exploratory study; v2 does not submit orders or claim live paper fills.'}
 write_json(OUT/'results.json',result)
 for name,rows in [('daily',daily),('trades',trades),('fills',fills),('stress',stresses),('ablations',ablations),('resolution',resolution),('overlap_audit',overlap)]:csvfile(OUT/(name+'.csv'),rows)
 with gzip.open(OUT/'decisions.csv.gz','wt',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(decisions[0]));w.writeheader();w.writerows(decisions)
 for s,v in markets.items():
  for k,r in v.items():print(s,k,'mean/day',round(r['mean_pnl'],2),'total',round(r['total_pnl'],2),'trades',r['trades'],'CI',r['ci_mean_pnl'],'qualified',r['qualified'])
 print('primary',frozen['primary'],'days',splits)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('stage',choices=['freeze','evaluate']);a=p.parse_args();globals()[a.stage]()
