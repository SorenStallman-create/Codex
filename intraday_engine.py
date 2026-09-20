"""Minute execution engine for three independent daily paper strategies."""
from __future__ import annotations
import json,math
from dataclasses import dataclass
from app import Bar,ema,rsi
from intraday_data import MINUTE,FIVE,day_key
from research_weekly import iso

CAPITAL=10000/3
NAMES={'momentum':'Momentum Pullback','breakout':'Volume Breakout','reversion':'VWAP Reversion'}
SETTINGS={'momentum':(2,3,4),'breakout':(6,12,18),'reversion':(1.,1.5,2.)}
@dataclass(frozen=True)
class Config:
 family:str
 setting:float
 fee:float=.001
 slip:float=.0002
 risk:float=.0025
 cap:float=.95
 stop_atr:float=1.5
 reward:float=3.
 min_edge_cost:float=1.5
 max_trades:int=4
 max_hold:int=120
 cooldown:int=15
 daily_loss:float=.01
 delay:int=0
 @property
 def id(self):return f'{self.family}-{self.setting:g}'
def configs():return [Config(f,n) for f,values in SETTINGS.items() for n in values]

def features(minutes):
 groups={};vwap={};last_day=None;pv=volume=0.
 for b in minutes:
  day=day_key(b.time)
  if day!=last_day:pv=volume=0.;last_day=day
  pv+=(b.high+b.low+b.close)/3*b.volume;volume+=b.volume
  vwap[b.time]=pv/volume if volume else b.close
  groups.setdefault(b.time//FIVE*FIVE,[]).append(b)
 bars=[];vwaps=[]
 for t,g in sorted(groups.items()):
  if len(g)!=5 or [b.time for b in g]!=[t+j*MINUTE for j in range(5)]:continue
  bars.append(Bar(t,g[0].open,max(b.high for b in g),min(b.low for b in g),g[-1].close,sum(b.volume for b in g)))
  vwaps.append(vwap[g[-1].time])
 closes=[b.close for b in bars];es={n:ema(closes,n) for n in (9,21,50)};rs=rsi(closes,14)
 trs=[max(b.high-b.low,abs(b.high-bars[i-1].close),abs(b.low-bars[i-1].close)) if i else b.high-b.low for i,b in enumerate(bars)]
 atrs=[0.]*len(bars)
 if len(bars)>=14:
  atrs[13]=sum(trs[:14])/14
  for i in range(14,len(bars)):atrs[i]=(atrs[i-1]*13+trs[i])/14
 out={}
 for i in range(56,len(bars)):
  b=bars[i];f={'closed_ms':b.time+FIVE,'closed_utc':iso(b.time+FIVE),'day':day_key(b.time),'close':b.close,'previous_close':bars[i-1].close,
    'atr':atrs[i],'previous_atr':atrs[i-1],'vwap':vwaps[i],'previous_vwap':vwaps[i-1],
    'previous_same_day':day_key(bars[i-1].time)==day_key(b.time),'rsi':rs[i],
    'ema9':es[9][i],'ema21':es[21][i],'ema50':es[50][i],'ema50_prior6':es[50][i-6],
    'volume':b.volume,'prior_volume20':sum(x.volume for x in bars[i-20:i])/20}
  f.update({f'touch{n}':any(bars[j].low<=es[9][j] for j in range(i-n+1,i+1)) for n in (2,3,4)})
  f.update({f'high{n}':max(x.high for x in bars[i-n:i]) for n in (6,12,18)})
  out[b.time+FIVE]=f
 return out

def rules(c,f):
 if c.family=='momentum':
  entry=f['ema9']>f['ema21']>f['ema50'] and f['close']>f['vwap'] and f[f'touch{int(c.setting)}'] and f['close']>f['ema9'] and f['close']>f['previous_close']
  exit_=f['close']<f['ema21'] or f['close']<f['vwap']
  reason='Rising EMA9/21/50, VWAP support and pullback recovery'
 elif c.family=='breakout':
  entry=f['close']>f[f'high{int(c.setting)}'] and f['close']>f['vwap'] and f['volume']>1.2*f['prior_volume20']
  exit_=f['close']<f['ema9'] or f['close']<f['vwap'];reason='Prior-channel break with above-average volume and VWAP support'
 elif c.family=='reversion':
  entry=f['previous_same_day'] and f['previous_close']<f['previous_vwap']-c.setting*f['previous_atr'] and f['close']>f['previous_close'] and f['rsi']<45 and f['ema50']>=f['ema50_prior6']-f['atr']
  exit_=f['rsi']>60;reason='Below-VWAP stretch begins recovering without steep EMA50 decline'
 else:raise ValueError(c.family)
 return bool(entry),bool(exit_),reason

class Engine:
 def __init__(self,c,filters,start,end,state=None,capital=CAPITAL):
  self.c=c;self.filters=filters;self.start=start;self.end=end;self.capital=capital
  self.s={'cash':capital,'qty':0.,'stop':None,'target':None,'entry':None,'pending':None,'entries':0,'blocked':False,'kill_pending':False,
          'cooldown_until':0,'fees':0.,'slippage':0.,'last_time':None,'last_feature':None,'equity':capital,'peak':capital,'maxdd':0.}
  if state:self.s.update(state)
  self.trades=[];self.fills=[];self.events=[]
 def sell(self,price,t,reason,precision='minute_open'):
  s=self.s;c=self.c
  if not s['qty']:return
  q=s['qty'];fill=price*(1-c.slip);fee=q*fill*c.fee;slip=q*(price-fill);proceeds=q*fill-fee;s['cash']+=proceeds
  s['fees']+=fee;s['slippage']+=slip;e=s['entry'];pnl=proceeds-e['debit']
  self.trades.append({**e,'exit_ms':t,'exit_utc':iso(t),'exit_fill':fill,'exit_fee':fee,'exit_slippage':slip,'net_pnl':pnl,'hold_minutes':(t-e['entry_ms'])/MINUTE,'exit_reason':reason,'time_precision':precision})
  self.fills.append({'time_ms':t,'time_utc':iso(t),'side':'sell','price':fill,'qty':q,'fee':fee,'slippage':slip,'reason':reason,'cash':s['cash'],'time_precision':precision})
  s.update(qty=0.,stop=None,target=None,entry=None,pending=None,cooldown_until=t+c.cooldown*MINUTE)
  self.events.append(reason)
 def buy(self,b,f,reason):
  c=self.c;s=self.s;price=b.open;fill=price*(1+c.slip);distance=c.stop_atr*f['atr'];stop=price-distance
  target=f['vwap'] if c.family=='reversion' else price+c.reward*distance
  costs=2*(c.fee+c.slip)*price
  if distance<=0 or stop<=0 or target-price<c.min_edge_cost*costs:
   self.events.append('skip_insufficient_move_after_costs');return
  loss=fill*(1+c.fee)-stop*(1-c.slip)*(1-c.fee)
  qty=min(s['cash']*c.risk/loss,s['cash']*c.cap/fill,s['cash']/(fill*(1+c.fee)),self.filters['max_qty'])
  step=self.filters['step'];qty=math.floor((qty+1e-12)/step)*step
  if qty<self.filters['min_qty'] or qty*fill<self.filters['min_notional']:
   self.events.append('skip_order_minimum');return
  equity=s['cash'];debit=qty*fill*(1+c.fee);fee=qty*fill*c.fee;slip=qty*(fill-price)
  s['cash']-=debit;s.update(qty=qty,stop=stop,target=target);s['entries']+=1;s['fees']+=fee;s['slippage']+=slip
  s['entry']={'entry_ms':b.time,'entry_utc':iso(b.time),'signal_utc':f['closed_utc'],'entry_fill':fill,'entry_market':price,'qty':qty,'debit':debit,'entry_fee':fee,'entry_slippage':slip,'initial_stop':stop,'initial_target':target,'risk_distance':distance,'planned_loss':qty*loss,'entry_equity':equity,'reason':reason,'indicators':json.dumps(f,separators=(',',':'))}
  self.fills.append({'time_ms':b.time,'time_utc':iso(b.time),'side':'buy','price':fill,'qty':qty,'fee':fee,'slippage':slip,'reason':reason,'cash':s['cash'],'time_precision':'minute_open'})
  self.events.append('buy')
 def process(self,b,f=None,collect=False):
  s=self.s;c=self.c;self.events=[]
  if not self.start<=b.time<self.end:raise ValueError('Candle outside this daily account')
  if s['last_time'] is not None and b.time<=s['last_time']:raise ValueError('Duplicate/reversed minute')
  if f:
   if f['closed_ms']>b.time:raise ValueError('Future information')
   if f['day']==day_key(b.time):
    s['last_feature']=f;entry,exit_,reason=rules(c,f)
    if s['qty'] and f['close']>=s['entry']['entry_market']+s['entry']['risk_distance']:
     s['stop']=max(s['stop'],f['close']-c.stop_atr*f['atr'])
    if s['qty'] and exit_:s['pending']={'side':'sell','due':b.time+c.delay*MINUTE,'f':f,'reason':'signal_exit'}
    elif not s['qty'] and entry and not s['blocked'] and s['entries']<c.max_trades and b.time>=s['cooldown_until'] and b.time<self.end-15*MINUTE:
     s['pending']={'side':'buy','due':b.time+c.delay*MINUTE,'f':f,'reason':reason}
  if s['qty'] and b.open<=s['stop']:self.sell(b.open,b.time,'gap_stop')
  if s['kill_pending']:
   self.sell(b.open,b.time,'daily_loss_exit');s['kill_pending']=False;s['pending']=None
  if s['qty'] and b.time-s['entry']['entry_ms']>=c.max_hold*MINUTE:self.sell(b.open,b.time,'time_limit')
  pending=s['pending']
  if pending and pending['due']<=b.time:
   s['pending']=None
   if pending['side']=='sell':self.sell(b.open,b.time,'signal_exit')
   elif not s['qty'] and not s['blocked'] and b.time<self.end-15*MINUTE:self.buy(b,pending['f'],pending['reason'])
  # Stop first if minute range touches both stop and target.
  if s['qty'] and b.low<=s['stop']:self.sell(s['stop'],b.time,'stop','within_minute_unknown')
  elif s['qty'] and b.high>=s['target']:self.sell(s['target'],b.time,'profit_target','within_minute_unknown')
  if b.time+MINUTE==self.end:
   self.sell(b.close,self.end,'day_end','scheduled_day_close');s['pending']=None
  equity=s['cash']+s['qty']*b.close;s['equity']=equity;s['peak']=max(s['peak'],equity);s['maxdd']=max(s['maxdd'],1-equity/s['peak'])
  if equity<=self.capital*(1-c.daily_loss):s['blocked']=True;s['kill_pending']=bool(s['qty'])
  s['last_time']=b.time
  if not collect:return equity
  last=s['last_feature']
  return {'minute_ms':b.time,'minute_utc':iso(b.time),'equity':equity,'cash':s['cash'],'qty':s['qty'],'stop':s['stop'],'target':s['target'],'action':'|'.join(self.events) or ('hold_long' if s['qty'] else 'hold_cash'),'blocked':s['blocked'],'signal_utc':last['closed_utc'] if last else '', 'indicators':json.dumps(f,separators=(',',':')) if f else ''}

def simulate(bars,fm,c,filters,start,end,collect=False,capital=CAPITAL):
 e=Engine(c,filters,start,end,capital=capital);curve=[capital];rows=[];exposure=0.;minute_count=0
 for b in bars:
  if b.time<start:continue
  if b.time>=end:break
  row=e.process(b,fm.get(b.time),collect);equity=row['equity'] if collect else row
  curve.append(equity);minute_count+=1;exposure+=e.s['qty']*b.close/equity
  if collect:rows.append(row)
 if not minute_count or e.s['last_time']+MINUTE!=end:raise ValueError('Incomplete historical day')
 if e.s['qty']:raise ValueError('Overnight position leaked')
 return {'net_pnl':e.s['cash']-capital,'return_pct':100*(e.s['cash']/capital-1),'drawdown_pct':100*e.s['maxdd'],'fees':e.s['fees'],'slippage':e.s['slippage'],'trades':len(e.trades),'mean_exposure_pct':100*exposure/minute_count,'curve':curve,'trade_rows':e.trades,'fill_rows':e.fills,'decision_rows':rows}
