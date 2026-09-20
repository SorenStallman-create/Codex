"""Public research-only ETF dataset; strict regular-session validation."""
import datetime as dt,json,ssl,urllib.parse,urllib.request,hashlib,math
from pathlib import Path
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parent
OUT=ROOT/'research/intraday_v2';NY=ZoneInfo('America/New_York');MINUTE=60000;STEP=5*MINUTE
def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def bounds(day):
 d=dt.date.fromisoformat(day)
 return tuple(int(dt.datetime.combine(d,t,NY).timestamp()*1000) for t in (dt.time(9,30),dt.time(16)))
def validate(day,bars,step=STEP):
 start,end=bounds(day)
 if [b[0] for b in bars]!=list(range(start,end,step)):raise ValueError(f'Incomplete/duplicate/unordered session {day}: {len(bars)} bars')
 for b in bars:
  t,o,h,l,c,v=b
  if not all(math.isfinite(x) for x in b) or not 0<l<=min(o,c)<=max(o,c)<=h or v<0:raise ValueError('Invalid OHLCV')
def fetch(symbol):
 start=int(dt.datetime(2026,7,21,tzinfo=NY).timestamp());end=int(dt.datetime(2026,9,18,tzinfo=NY).timestamp())
 url='https://query1.finance.yahoo.com/v8/finance/chart/'+symbol+'?'+urllib.parse.urlencode({'period1':start,'period2':end,'interval':'5m','includePrePost':'false','events':'div,splits'})
 req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'})
 with urllib.request.urlopen(req,context=ssl.create_default_context(cafile='/etc/ssl/cert.pem'),timeout=45) as r:return json.load(r)
def main():
 (OUT/'data').mkdir(exist_ok=True,parents=True);audits={}
 for symbol in ('SPY','QQQ'):
  raw=OUT/'data'/f'{symbol}_raw.json'
  if not raw.exists():raw.write_text(json.dumps(fetch(symbol)))
  payload=json.loads(raw.read_text());error=payload['chart']['error']
  if error:raise RuntimeError(error)
  result=payload['chart']['result'][0];q=result['indicators']['quote'][0];groups={};duplicate=0;seen={};nulls=0
  for i,t in enumerate(result['timestamp']):
   local=dt.datetime.fromtimestamp(t,NY)
   if local.weekday()>=5 or not dt.time(9,30)<=local.time()<dt.time(16):continue
   values=[q[k][i] for k in ('open','high','low','close','volume')]
   if any(v is None for v in values):nulls+=1;continue
   b=[t*1000,*map(float,values)]
   if t in seen:
    duplicate+=1
    if seen[t]!=b:raise ValueError('Conflicting duplicate')
    continue
   seen[t]=b;groups.setdefault(local.date().isoformat(),[]).append(b)
  good={};excluded=[]
  for day,bars in sorted(groups.items()):
   try:validate(day,bars)
   except ValueError as e:excluded.append({'day':day,'reason':str(e)});continue
   good[day]=bars
  path=OUT/'data'/f'{symbol}.json';path.write_text(json.dumps(good,separators=(',',':')))
  audits[symbol]={'sessions':list(good),'bars':sum(map(len,good.values())),'excluded':excluded,'duplicate_rows':duplicate,'null_rows':nulls,'raw_sha256':digest(raw),'data_sha256':digest(path),'corporate_events':result.get('events',{})}
 report={'retrieved_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'provider':'Yahoo public chart, unverified consolidated coverage','interval_minutes':5,'audits':audits}
 (OUT/'data_audit.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
if __name__=='__main__':main()
