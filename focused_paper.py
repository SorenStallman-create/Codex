"""Incremental candle-based forward paper runner. Never sends exchange orders.

init creates a new paper clock at the NEXT UTC hour; step consumes only later
completed candles. Historical warmup is never counted as forward performance.
"""
from __future__ import annotations
import argparse
import datetime as dt
import fcntl
import json
import math
import os
from pathlib import Path
from focused_data import OUT,CACHE,END,SYMBOLS,request
from focused_engine import Config,Engine,features,SLEEVE_CAPITAL
from focused_research import stamp
from research_weekly import HOUR_MS,iso,sha256
from app import Bar

STATE=OUT/'paper_state.json'

def wall_time():return int(dt.datetime.now(dt.timezone.utc).timestamp()*1000)
def save(state):
    tmp=STATE.with_suffix('.tmp');tmp.write_text(json.dumps(state,indent=2,allow_nan=False));os.replace(tmp,STATE)

def initialize():
    if STATE.exists():raise RuntimeError('Paper state already exists; use step. It will not be reset.')
    frozen=json.loads((OUT/'frozen_selection.json').read_text());config=next(c for c in frozen['selected_configs'] if f"{c['family']}-{c['setting']}"==frozen['champion_id'])
    audit=json.loads((OUT/'data_audit.json').read_text());start=(wall_time()//HOUR_MS+1)*HOUR_MS
    state={'created_utc':iso(wall_time()),'start_utc':iso(start),'start_ms':start,'model':frozen['champion_id'],
           'config':config,'frozen_selection_sha256':sha256(OUT/'frozen_selection.json'),
           'engine_sha256':sha256(Path(__file__).with_name('focused_engine.py')),
           'kind':'Candle-based forward paper simulation, not exchange testnet or actual fills',
           'sleeves':{s:Engine(Config(**config),audit['quantity_filters'][s]).s for s in SYMBOLS},
           'quantity_filters':audit['quantity_filters'],'fills':[],'trades':[],'decisions':[],
           'processed_hours':{s:0 for s in SYMBOLS},'last_step_utc':None,'equity':10000.,
           'status':'Initialized; waiting for candles after the forward start. Zero forward performance yet.'}
    save(state);print(json.dumps({k:state[k] for k in ['model','start_utc','kind','status']},indent=2))

def all_rows(symbol,cutoff):
    raw=[json.loads(line) for line in (CACHE/(symbol+'_1h.jsonl')).read_text().splitlines()]
    extension=OUT/'data'/(symbol+'_forward.jsonl')
    extra=[json.loads(line) for line in extension.read_text().splitlines()] if extension.exists() else []
    cursor=int(extra[-1][0])+HOUR_MS if extra else END
    if cursor<cutoff:
        with extension.open('a') as f:
            while cursor<cutoff:
                page=request('klines',{'symbol':symbol,'interval':'1h','startTime':cursor,'endTime':cutoff-1,'limit':1000})
                if not isinstance(page,list) or not page:raise RuntimeError('Forward data unavailable')
                for r in page:
                    if not cursor<=int(r[0])<cutoff:raise ValueError('Forward timestamp out of bounds')
                    f.write(json.dumps(r,separators=(',',':'))+'\n')
                extra.extend(page);cursor=int(page[-1][0])+HOUR_MS
    raw.extend(extra)
    previous=-1
    for r in raw:
        t=int(r[0]);o,h,l,c,v=map(float,r[1:6])
        if t<=previous or t%HOUR_MS or not t<=int(r[6])<t+HOUR_MS or min(o,h,l,c)<=0 or l>min(o,c) or h<max(o,c) or v<0 or not all(math.isfinite(x) for x in (o,h,l,c,v)):raise ValueError('Invalid forward history')
        previous=t
    return raw

def advance():
    if not STATE.exists():raise RuntimeError('Initialize with: python3 focused_paper.py init')
    state=json.loads(STATE.read_text())
    if state['engine_sha256']!=sha256(Path(__file__).with_name('focused_engine.py')) or state['frozen_selection_sha256']!=sha256(OUT/'frozen_selection.json'):
        raise ValueError('Frozen bot changed; do not mix versions in forward results')
    cutoff=wall_time()//HOUR_MS*HOUR_MS
    if cutoff<=state['start_ms']:
        print('Waiting for first completed forward candle after '+state['start_utc']);return
    total=0;counts=[]
    for symbol in SYMBOLS:
        raw=all_rows(symbol,cutoff)
        bars=[Bar(int(r[0]),*map(float,r[1:6])) for r in raw if int(r[0])<cutoff]
        incomplete={int(r[0]) for r in raw if int(r[6])!=int(r[0])+HOUR_MS-1}
        fm=features(bars,incomplete);engine=Engine(Config(**state['config']),state['quantity_filters'][symbol],state=state['sleeves'][symbol])
        last=engine.s['last_time'];start=max(state['start_ms'],last+HOUR_MS if last is not None else state['start_ms']);equity=None;count=0
        for b in bars:
            if b.time<start:continue
            row=engine.process(b,fm.get(b.time),collect=True);equity=row['equity'];count+=1
            state['decisions'].append({'asset':symbol,**row})
        state['sleeves'][symbol]=engine.s;state['processed_hours'][symbol]+=count;counts.append(count)
        state['fills'].extend({'asset':symbol,**row} for row in engine.fills)
        state['trades'].extend({'asset':symbol,**row} for row in engine.trades)
        total+=engine.s['cash']+engine.s['qty']*bars[-1].close
    state.update(last_step_utc=iso(wall_time()),equity=total,status='Forward candle replay active; fills remain simulated.')
    save(state);print(json.dumps({'processed_new_hours':dict(zip(SYMBOLS,counts)),'paper_equity':total,'fills':len(state['fills']),'trades':len(state['trades'])},indent=2))

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['init','step','status']);args=parser.parse_args()
    OUT.mkdir(exist_ok=True)
    with (OUT/'paper.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if args.action=='init':initialize()
        elif args.action=='step':advance()
        else:
            s=json.loads(STATE.read_text());print(json.dumps({k:v for k,v in s.items() if k not in ['sleeves','decisions','fills','trades']},indent=2))
if __name__=='__main__':main()
