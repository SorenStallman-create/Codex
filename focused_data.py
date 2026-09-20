"""Public market-data cache for the frozen three-bot protocol."""
import concurrent.futures
import datetime as dt
import json
import math
import ssl
import time
import urllib.parse
import urllib.request
from pathlib import Path
from app import Bar
from research_weekly import sha256, iso, HOUR_MS

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'research/focused_bot'
CACHE=OUT/'data'
SYMBOLS=('BTCUSDT','ETHUSDT','SOLUSDT')
URL='https://data-api.binance.vision/api/v3/'
START=int(dt.datetime(2020,12,1,tzinfo=dt.timezone.utc).timestamp()*1000)
END=int(dt.datetime(2026,9,18,tzinfo=dt.timezone.utc).timestamp()*1000)
CTX=ssl.create_default_context(cafile='/etc/ssl/cert.pem')

def request(path,params):
    url=URL+path+'?'+urllib.parse.urlencode(params)
    for attempt in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'TradingLab-Research/2.0'}),context=CTX,timeout=30) as r:
                return json.load(r)
        except Exception:
            if attempt==4: raise
            time.sleep(min(2**attempt,8))

def download(symbol):
    path=CACHE/(symbol+'_1h.jsonl')
    rows=[]
    if path.exists():
        rows=[json.loads(line) for line in path.read_text().splitlines()]
    cursor=int(rows[-1][0])+HOUR_MS if rows else START
    pages=0
    with path.open('a') as f:
        while cursor<END:
            page=request('klines',{'symbol':symbol,'interval':'1h','startTime':cursor,'endTime':END-1,'limit':1000})
            if not page or not isinstance(page,list): raise RuntimeError(f'Incomplete response {symbol}: {page}')
            if int(page[0][0])<cursor: raise RuntimeError('Pagination reversed')
            for row in page:
                if int(row[0])>=END: raise RuntimeError('Incomplete/future candle returned')
                f.write(json.dumps(row,separators=(',',':'))+'\n')
            f.flush();rows.extend(page);cursor=int(page[-1][0])+HOUR_MS;pages+=1
            if pages%10==0: print(symbol,len(rows),'candles',iso(cursor),flush=True)
            time.sleep(.1)
    audit=validate(rows,symbol)
    print(symbol,'done',len(rows),'gaps',audit['missing_hours'],flush=True)
    return audit

def validate(rows,symbol):
    seen=set();gaps=[];shortened=[];previous=START-HOUR_MS
    for r in rows:
        t=int(r[0]);o,h,l,c,v=map(float,r[1:6])
        if t in seen or t<=previous or t%HOUR_MS or not t<=int(r[6])<t+HOUR_MS: raise ValueError('Invalid timestamp or duplicate')
        if not all(math.isfinite(x) for x in (o,h,l,c,v)) or min(o,h,l,c)<=0 or v<0 or l>min(o,c) or h<max(o,c): raise ValueError('Invalid OHLCV')
        if t>previous+HOUR_MS:gaps.append({'from':iso(previous+HOUR_MS),'to_exclusive':iso(t),'hours':(t-previous)//HOUR_MS-1})
        if int(r[6])!=t+HOUR_MS-1:shortened.append({'open_utc':iso(t),'reported_close_utc':iso(int(r[6])+1)})
        seen.add(t);previous=t
    if not rows or int(rows[-1][0])+HOUR_MS!=END:raise ValueError('History does not reach cutoff')
    return {'symbol':symbol,'rows':len(rows),'first':iso(int(rows[0][0])),'end_exclusive':iso(END),'missing_hours':sum(g['hours'] for g in gaps),'gaps':gaps,'shortened_candles':shortened,'duplicates':0,'invalid':0,'sha256':sha256(CACHE/(symbol+'_1h.jsonl'))}

def load(symbol):
    with (CACHE/(symbol+'_1h.jsonl')).open() as f:
        return [Bar(int(r[0]),*map(float,r[1:6])) for r in (json.loads(line) for line in f)]

def filters():
    path=CACHE/'exchange_info.json'
    if not path.exists():
        data=request('exchangeInfo',{'symbols':json.dumps(SYMBOLS,separators=(',',':'))})
        path.write_text(json.dumps(data,indent=2))
    data=json.loads(path.read_text());out={}
    for s in data['symbols']:
        fs={f['filterType']:f for f in s['filters']};lot=fs['LOT_SIZE'];n=fs.get('NOTIONAL',fs.get('MIN_NOTIONAL',{}))
        out[s['symbol']]={'step':float(lot['stepSize']),'min_qty':float(lot['minQty']),'max_qty':float(lot['maxQty']),'min_notional':max(10,float(n.get('minNotional',10)))}
    if set(out)!=set(SYMBOLS):raise ValueError('Missing public quantity filters')
    return out

def main():
    CACHE.mkdir(parents=True,exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        audits=list(pool.map(download,SYMBOLS))
    report={'created_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'source':URL+'klines','audits':audits,'quantity_filters':filters(),'filter_note':'Current exchange snapshot applied as a historical proxy; account-specific fee tier unknown.'}
    (OUT/'data_audit.json').write_text(json.dumps(report,indent=2))
    print('Data audit saved.',flush=True)
if __name__=='__main__':main()
