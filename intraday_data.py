"""Validated one-minute source for independent New York day experiments."""
import concurrent.futures,datetime as dt,json,math,time
from pathlib import Path
from zoneinfo import ZoneInfo
from app import Bar
from focused_data import request,filters as public_filters
from research_weekly import sha256,iso
ROOT=Path(__file__).resolve().parent;OUT=ROOT/'research/intraday_v1';CACHE=OUT/'data'
SYMBOLS=('BTCUSDT','ETHUSDT','SOLUSDT');MINUTE=60000;FIVE=5*MINUTE;NY=ZoneInfo('America/New_York')
END=int(dt.datetime(2026,9,17,tzinfo=NY).timestamp()*1000)
START=int((dt.datetime(2026,9,17,tzinfo=NY)-dt.timedelta(days=91)).timestamp()*1000)
def day_key(t):return dt.datetime.fromtimestamp(t/1000,NY).date().isoformat()
def day_bounds(day):
 d=dt.datetime.combine(dt.date.fromisoformat(day),dt.time(),NY)
 return int(d.timestamp()*1000),int((d+dt.timedelta(days=1)).timestamp()*1000)
def days():
 first=dt.datetime.fromtimestamp(END/1000,NY).date()-dt.timedelta(days=90)
 return [(first+dt.timedelta(days=i)).isoformat() for i in range(90)]
def fetch(symbol,start,end):
 out=[];cursor=start
 while cursor<end:
  page=request('klines',{'symbol':symbol,'interval':'1m','startTime':cursor,'endTime':end-1,'limit':1000})
  if not isinstance(page,list) or not page:raise ValueError(f'Missing public data for {symbol} at {iso(cursor)}')
  if int(page[0][0])<cursor or int(page[-1][0])>=end:raise ValueError('Pagination bounds')
  out.extend(page);cursor=int(page[-1][0])+MINUTE
 return out

def download(symbol):
 path=CACHE/(symbol+'_1m.jsonl');raw=[json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
 cursor=int(raw[-1][0])+MINUTE if raw else START;pages=0
 with path.open('a') as f:
  while cursor<END:
   page=request('klines',{'symbol':symbol,'interval':'1m','startTime':cursor,'endTime':END-1,'limit':1000})
   if not isinstance(page,list) or not page:raise ValueError('Empty response')
   if int(page[0][0])<cursor or int(page[-1][0])>=END:raise ValueError('Pagination reversed or future bar')
   for row in page:f.write(json.dumps(row,separators=(',',':'))+'\n')
   f.flush();raw.extend(page);cursor=int(page[-1][0])+MINUTE;pages+=1
   if pages%30==0:print(symbol,len(raw),iso(cursor),flush=True)
   time.sleep(.08)
 audit=validate(raw,symbol);print(symbol,'validated',len(raw),flush=True);return audit

def validate(raw,symbol):
 seen=set();bad_days=set();previous=START-MINUTE;gaps=[];shortened=[]
 for r in raw:
  t=int(r[0]);o,h,l,c,v=map(float,r[1:6])
  if t in seen or t<=previous or t%MINUTE or not t<=int(r[6])<t+MINUTE:raise ValueError('Invalid or duplicate timestamp')
  if not all(math.isfinite(x) for x in (o,h,l,c,v)) or min(o,h,l,c)<=0 or l>min(o,c) or h<max(o,c) or v<0:raise ValueError('Invalid OHLCV')
  if t>previous+MINUTE:
   missing=list(range(previous+MINUTE,t,MINUTE));bad_days.update(day_key(m) for m in missing);gaps.append({'start':iso(missing[0]),'minutes':len(missing)})
  if int(r[6])!=t+MINUTE-1:shortened.append(iso(t));bad_days.add(day_key(t))
  seen.add(t);previous=t
 if previous+MINUTE!=END:raise ValueError('Incomplete data cutoff')
 return {'symbol':symbol,'candles':len(raw),'gaps':gaps,'shortened':shortened,'excluded_days':sorted(bad_days),'sha256':sha256(CACHE/(symbol+'_1m.jsonl')),'first':iso(int(raw[0][0])),'end_exclusive':iso(END)}

def load(symbol):
 with (CACHE/(symbol+'_1m.jsonl')).open() as f:return [Bar(int(r[0]),*map(float,r[1:6])) for r in (json.loads(line) for line in f)]
def main():
 CACHE.mkdir(parents=True,exist_ok=True)
 with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:audits=list(pool.map(download,SYMBOLS))
 excluded=sorted(set(day for a in audits for day in a['excluded_days']))
 (OUT/'data_audit.json').write_text(json.dumps({'created_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'source':'https://data-api.binance.vision/api/v3/klines','audits':audits,'excluded_days':excluded,'quantity_filters':public_filters(),'filter_note':'Current exchange filter snapshot is a historical proxy.'},indent=2))
 print('Saved audit. Excluded days:',excluded,flush=True)
if __name__=='__main__':main()
