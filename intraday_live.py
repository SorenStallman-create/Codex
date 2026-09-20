"""Finite-session public-data paper monitor. No exchange order endpoints."""
import argparse,datetime as dt,fcntl,json,os,time,traceback
from pathlib import Path
from app import Bar
from intraday_data import OUT,NY,MINUTE,fetch,day_bounds,day_key
from intraday_spy_data import request_spy,extract
from intraday_engine import Config,Engine,features
from intraday_research import bounds
from research_weekly import iso,sha256
STATE=OUT/'live_state.json';PUBLIC=OUT/'live.json';STOP=OUT/'STOP_LIVE'
def wall():return int(time.time()*1000)
def save(path,data):
 temp=path.with_suffix('.tmp');temp.write_text(json.dumps(data,separators=(',',':'),allow_nan=False));os.replace(temp,path)
def next_spy_day(t):
 n=dt.datetime.fromtimestamp(t/1000,NY);day=n.date()
 if n.time()>=dt.time(16):day+=dt.timedelta(days=1)
 while day.weekday()>=5:day+=dt.timedelta(days=1)
 return day.isoformat()
def initialize():
 frozen=json.loads((OUT/'frozen.json').read_text());audit=json.loads((OUT/'data_audit.json').read_text());start=(wall()//MINUTE+1)*MINUTE
 state={'created_utc':iso(wall()),'started_ms':start,'engine_hash':sha256(Path(__file__).with_name('intraday_engine.py')),'freeze_hash':sha256(OUT/'frozen.json'),'status':'running','markets':{},'last_poll_utc':None,'last_error':None}
 for market in ('spy','crypto'):
  day=next_spy_day(start) if market=='spy' else day_key(start);a,b=bounds(market,day);anchor=max(start,a)
  symbols=['SPY'] if market=='spy' else list(audit['quantity_filters']);fs={'SPY':{'step':1.,'min_qty':1.,'min_notional':1.,'max_qty':1e9}} if market=='spy' else audit['quantity_filters']
  models={}
  for config in frozen['markets'][market]['configs']:
   c=Config(**config);models[c.id]={'config':config,'states':{s:Engine(c,fs[s],a,b,capital=10000/len(symbols)).s for s in symbols},'curve':[10000.],'trades':[],'fills':[],'decisions':[]}
  state['markets'][market]={'day':day,'day_start_ms':a,'day_end_ms':b,'start_ms':anchor,'last_processed_ms':anchor-MINUTE,'symbols':symbols,'filters':fs,'models':models,'candles':{s:[] for s in symbols},'features':{s:[] for s in symbols},'status':'waiting_for_session' if anchor>a and market=='spy' and wall()<a else 'waiting_for_first_complete_minute'}
 save(STATE,state);publish(state);return state

def publish(state):
 payload={'updated_utc':iso(wall()),'created_utc':state['created_utc'],'status':state['status'],'worker_pid':os.getpid(),'last_poll_utc':state['last_poll_utc'],'last_error':state['last_error'],'execution_note':'Forward candle-based paper fills, observed after minute closes; not broker confirmations.','markets':{}}
 for market,m in state['markets'].items():
  models={}
  for model,r in m['models'].items():
   equity=sum(s['equity'] for s in r['states'].values());peak=10000;dd=0
   for v in r['curve']:peak=max(peak,v);dd=max(dd,1-v/peak)
   summary={'net_pnl':equity-10000,'return_pct':100*(equity/10000-1),'drawdown_pct':100*dd,'trades':len(r['trades']),'wins':sum(t['net_pnl']>0 for t in r['trades']),'fees':sum(s['fees'] for s in r['states'].values()),'slippage':sum(s['slippage'] for s in r['states'].values()),'exposure_pct':None}
   models[model]={'summary':summary,'curve':r['curve'],'trades':r['trades'],'fills':r['fills'],'latest_decisions':r['decisions'][-len(m['symbols']):]}
  payload['markets'][market]={'market':market,'day':m['day'],'status':m['status'],'start_ms':m['start_ms'],'end_ms':max(m['start_ms'],m['last_processed_ms']+MINUTE),'session_end_ms':m['day_end_ms'],'symbols':m['symbols'],'candles':m['candles'],'features':m['features'],'models':models}
 save(PUBLIC,payload)

def step(state):
 if state['engine_hash']!=sha256(Path(__file__).with_name('intraday_engine.py')) or state['freeze_hash']!=sha256(OUT/'frozen.json'):raise ValueError('Frozen implementation changed')
 cutoff=wall()//MINUTE*MINUTE;state['last_poll_utc']=iso(wall());state['last_error']=None
 for market,m in state['markets'].items():
  a,b=m['day_start_ms'],m['day_end_ms'];end=min(cutoff,b);start=m['last_processed_ms']+MINUTE
  if cutoff<=m['start_ms']:
   m['status']='market_closed_waiting_for_session' if cutoff<a else 'waiting_for_first_complete_minute';continue
  if start>=b:m['status']='session_complete';continue
  if end<=start:continue
  by_symbol={};fms={}
  for symbol in m['symbols']:
   if market=='spy':
    history=extract(request_spy(a-6*24*60*MINUTE,end));history=[x for x in history if x.time<end]
   else:
    raw=fetch(symbol,a-12*60*MINUTE,end);history=[Bar(int(r[0]),*map(float,r[1:6])) for r in raw]
   histmap={x.time:x for x in history}
   if any(t not in histmap for t in range(start,end,MINUTE)):raise ValueError(f'{symbol}: missing live minutes; processing paused')
   if any(not(x.low<=min(x.open,x.close)<=max(x.open,x.close)<=x.high) for x in history):raise ValueError('Invalid live OHLC')
   by_symbol[symbol]=histmap;fms[symbol]=features(history)
   m['candles'][symbol]=[[x.time,x.open,x.high,x.low,x.close,x.volume] for x in history if m['start_ms']<=x.time<end]
   m['features'][symbol]=[f for t,f in fms[symbol].items() if m['start_ms']<=t<end]
  for model,r in m['models'].items():
   engines={s:Engine(Config(**r['config']),m['filters'][s],a,b,state=r['states'][s],capital=10000/len(m['symbols'])) for s in m['symbols']}
   for t in range(start,end,MINUTE):
    total=0
    for s,e in engines.items():
     row=e.process(by_symbol[s][t],fms[s].get(t),True);total+=row['equity'];r['decisions'].append({'asset':s,**row})
    r['curve'].append(round(total,6))
   for s,e in engines.items():
    r['states'][s]=e.s;r['trades'].extend({'asset':s,**t} for t in e.trades);r['fills'].extend({'asset':s,**f} for f in e.fills)
  m['last_processed_ms']=end-MINUTE;m['status']='session_complete' if end==b else 'monitoring_completed_minutes'
 return state

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--watch',action='store_true');args=parser.parse_args()
 with (OUT/'live.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  state=json.loads(STATE.read_text()) if STATE.exists() else initialize()
  while True:
   if STOP.exists():state['status']='stopped_by_user';save(STATE,state);publish(state);break
   try:
    candidate=json.loads(json.dumps(state));state=step(candidate)
    if all(m['status']=='session_complete' for m in state['markets'].values()):state['status']='sessions_complete'
    save(STATE,state);publish(state);print(iso(wall()),[(k,m['status']) for k,m in state['markets'].items()],flush=True)
   except Exception as exc:
    state['last_error']=str(exc);state['last_poll_utc']=iso(wall());save(STATE,state);publish(state);print('Paused data step:',repr(exc),flush=True)
   if not args.watch or state['status']=='sessions_complete':break
   time.sleep(20)
if __name__=='__main__':main()
