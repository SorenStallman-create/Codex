"""Public SPY minute-history feasibility pilot; no broker access."""
import datetime as dt,json,ssl,time,urllib.parse,urllib.request
from pathlib import Path
from app import Bar
from intraday_data import OUT,NY,MINUTE
from research_weekly import sha256,iso
CTX=ssl.create_default_context(cafile='/etc/ssl/cert.pem')
def request_spy(start,end):
 url='https://query1.finance.yahoo.com/v8/finance/chart/SPY?'+urllib.parse.urlencode({'period1':start//1000,'period2':end//1000,'interval':'1m','includePrePost':'false','events':'div,splits'})
 with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'}),context=CTX,timeout=25) as response:data=json.load(response)
 if data['chart']['error']:raise RuntimeError(data['chart']['error'])
 return data

def extract(data):
 r=data['chart']['result'][0];q=r['indicators']['quote'][0];out=[]
 for i,t in enumerate(r.get('timestamp',[])):
  values=[q[k][i] for k in ('open','high','low','close','volume')]
  if any(v is None for v in values):continue
  local=dt.datetime.fromtimestamp(t,NY)
  if local.weekday()>=5 or not dt.time(9,30)<=local.time()<dt.time(16):continue
  out.append(Bar(t*1000,*map(float,values)))
 return out

def main():
 end=int(dt.datetime(2026,9,18,tzinfo=NY).timestamp()*1000);start=end-28*24*60*MINUTE
 cache=OUT/'data';by_time={};sources=[]
 for n,cursor in enumerate(range(start,end,7*24*60*MINUTE)):
  finish=min(end,cursor+7*24*60*MINUTE);path=cache/f'SPY_page{n}.json'
  if not path.exists():path.write_text(json.dumps(request_spy(cursor,finish)));time.sleep(.3)
  for b in extract(json.loads(path.read_text())):
   if b.time in by_time and by_time[b.time]!=b:raise ValueError('Conflicting SPY source rows')
   by_time[b.time]=b
  sources.append({'file':path.name,'sha256':sha256(path)})
 groups={}
 for b in sorted(by_time.values(),key=lambda b:b.time):groups.setdefault(dt.datetime.fromtimestamp(b.time/1000,NY).date().isoformat(),[]).append(b)
 good={};excluded=[]
 for day,group in groups.items():
  d=dt.date.fromisoformat(day);s=int(dt.datetime.combine(d,dt.time(9,30),NY).timestamp()*1000)
  if [b.time for b in group]!=list(range(s,s+390*MINUTE,MINUTE)):
   excluded.append({'day':day,'bars':len(group),'reason':'Not a complete 390-minute regular session'});continue
  if any(not(b.low<=min(b.open,b.close)<=max(b.open,b.close)<=b.high) or b.volume<0 or min(b.open,b.close,b.low)<=0 for b in group):raise ValueError('Invalid SPY OHLCV')
  good[day]=group
 target=cache/'SPY_1m.jsonl'
 target.write_text(''.join(json.dumps([b.time,b.open,b.high,b.low,b.close,b.volume])+'\n' for group in good.values() for b in group))
 audit={'market':'SPY','provider':'Yahoo Finance chart endpoint; public research feed, not broker or SIP execution certification','retrieved_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'sessions':list(good),'candles':sum(map(len,good.values())),'excluded_days':excluded,'sources':sources,'data_sha256':sha256(target),'session':'09:30–16:00 America/New_York; regular hours only','fees_note':'Scenario: 0.005% fee allowance plus 0.01% adverse slippage each side. Account fees unknown; whole shares only.'}
 (OUT/'spy_audit.json').write_text(json.dumps(audit,indent=2));print(json.dumps(audit,indent=2))
if __name__=='__main__':main()
