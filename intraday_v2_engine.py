"""Causal, independent-day ETF experiments. No broker endpoints."""
from dataclasses import dataclass,replace,asdict
import math,statistics
from intraday_v2_data import bounds,validate,STEP,MINUTE
CAPITAL=10000.
NAMES={'noise':'Time-of-day Momentum','orb':'Opening-range Breakout','late':'Closing Momentum','session':'Session Exposure','closing':'Unconditional Close','cash':'Cash'}
@dataclass(frozen=True)
class Config:
 family:str
 setting:float
 fee:float=.00005
 slip:float=.0001
 risk:float=.0025
 cap:float=.95
 delay:int=0
 vwap_gate:bool=True
 strength_gate:bool=True
 max_entries:int=0
 @property
 def id(self):return f'{self.family}-{self.setting:g}'
def candidates():return [Config(f,s) for f,ss in [('noise',(1.,1.5,2.)),('orb',(15.,30.,60.)),('late',(0.,.001,.002))] for s in ss]
def features(groups):
 """Exact prior14-session warmup; prefix-invariant and identical on replay/live."""
 days=sorted(groups);out={}
 for i,day in enumerate(days):
  validate(day,groups[day])
  if i<14:continue
  hist=[groups[d] for d in days[i-14:i]];bars=groups[day];pv=vol=0.;fm={}
  daily_range=statistics.mean(max(b[2] for b in h)-min(b[3] for b in h) for h in hist)
  prev=hist[-1][-1][4];cumhist=[]
  for h in hist:
   acc=0.;arr=[]
   for b in h:acc+=b[5];arr.append(acc)
   cumhist.append(arr)
  for j,b in enumerate(bars):
   t,o,hi,lo,c,v=b;pv+=(hi+lo+c)/3*v;vol+=v
   f={'closed_ms':t+STEP,'minute':(j+1)*5,'price':c,'open':bars[0][1],'previous_close':prev,'vwap':pv/vol if vol else c,
      'sigma':statistics.mean(abs(h[j][4]/h[0][1]-1) for h in hist),'mean_daily_range':daily_range,
      'volume_ratio':vol/statistics.mean(h[j] for h in cumhist) if statistics.mean(h[j] for h in cumhist)>0 else 0.,
      'first_return':bars[5][4]/prev-1 if j>=5 else None}
   for n in (15,30,60):
    if (j+1)*5>=n:f[f'high{n}']=max(x[2] for x in bars[:n//5]);f[f'low{n}']=min(x[3] for x in bars[:n//5])
   fm[t+STEP]=f
  out[day]=fm
 return out

def decision(c,f):
 m=f['minute'];p=f['price'];vw=f['vwap'];gate=p>vw or not c.vwap_gate
 if c.family=='noise':
  if m<30 or m>360 or m%30:return None
  upper=max(f['open'],f['previous_close'])*(1+(c.setting if c.strength_gate else 0)*f['sigma'])
  return p>upper and gate,p<max(upper,vw if c.vwap_gate else 0),'above_noise_band'
 if c.family=='orb':
  n=int(c.setting)
  if m<n:return None
  return m<150 and p>f[f'high{n}'] and gate and (f['volume_ratio']>=1 or not c.strength_gate),p<vw and c.vwap_gate,'opening_range_break'
 if c.family=='late':
  if m!=360:return None
  return f['first_return']>(c.setting if c.strength_gate else 0) and gate,False,'first30_predicts_last30'
 return None

def run_day(c,day,bars,fm,step=STEP,collect=True):
 validate(day,bars,step);start,end=bounds(day)
 cash=CAPITAL;qty=0;stop=target=None;entry=None;pending=None;entries=0;blocked=False;peak=CAPITAL;dd=adverse_dd=0.;fees=slippage=turnover=exposure=0.
 trades=[];fills=[];rows=[];curve=[CAPITAL];maxentries=c.max_entries or (2 if c.family=='noise' else 1)
 def sell(price,t,why):
  nonlocal cash,qty,stop,target,entry,pending,fees,slippage,turnover
  if not qty:return
  fill=price*(1-c.slip);fee=qty*fill*c.fee;s=qty*(price-fill);proceeds=qty*fill-fee;cash+=proceeds;fees+=fee;slippage+=s;turnover+=qty*price
  trades.append({**entry,'exit_ms':t,'exit_price':fill,'exit_fee':fee,'exit_slippage':s,'pnl':proceeds-entry['debit'],'reason_exit':why,'hold_minutes':(t-entry['entry_ms'])/MINUTE})
  fills.append({'time':t,'side':'sell','price':fill,'qty':qty,'fee':fee,'reason':why})
  qty=0;stop=target=entry=pending=None
 for b in bars:
  t,o,h,l,close,volume=b;action=[];f=fm.get(t)
  if f and f['closed_ms']>t:raise ValueError('Future feature')
  if qty and o<=stop:sell(o,t,'gap_stop');action.append('gap_stop')
  if blocked and qty:sell(o,t,'daily_loss');action.append('daily_loss')
  if f:
   d=decision(c,f)
   if d:
    go,leave,reason=d
    if qty and leave and pending is None:pending=('sell',t+c.delay*MINUTE,f,reason)
    elif not qty and go and not blocked and entries<maxentries and pending is None:pending=('buy',t+c.delay*MINUTE,f,reason)
  if c.family=='session' and t==start:pending=('buy',t,None,'regular_session_control')
  if c.family=='closing' and t==end-30*MINUTE:pending=('buy',t,None,'unconditional_close_control')
  if pending and pending[1]<=t:
   side,due,sf,reason=pending;pending=None
   if side=='sell':sell(o,t,'signal_exit');action.append('signal_exit')
   elif not qty and not blocked and t<end and entries<maxentries:
    fill=o*(1+c.slip)
    if c.family in ('session','closing'):st=0.
    elif c.family=='noise':st=min(o*.9995,max(sf['vwap'] if c.vwap_gate else 0,o-.5*sf['mean_daily_range']))
    elif c.family=='orb':st=min(o*.9995,sf[f'low{int(c.setting)}'])
    else:st=o-max(.0005*o,.25*sf['mean_daily_range'])
    loss=fill*(1+c.fee)-st*(1-c.slip)*(1-c.fee)
    q=math.floor(min(cash/(fill*(1+c.fee)),CAPITAL*c.cap/fill,CAPITAL*c.risk/loss if c.family not in ('session','closing') else 1e20))
    if q>0 and 0<=st<o:
     qty=q;stop=st;target=o+2*(o-st) if c.family=='orb' else math.inf;fee=q*fill*c.fee;s=q*(fill-o);debit=q*fill+fee;cash-=debit;fees+=fee;slippage+=s;turnover+=q*o;entries+=1
     entry={'entry_ms':t,'signal_ms':sf['closed_ms'] if sf else t,'entry_price':fill,'qty':q,'debit':debit,'entry_fee':fee,'entry_slippage':s,'initial_stop':st,'initial_target':None if math.isinf(target) else target,'planned_loss':q*loss,'reason_entry':reason,'signal':sf}
     fills.append({'time':t,'side':'buy','price':fill,'qty':q,'fee':fee,'reason':reason});action.append('buy')
    else:action.append('skip_size')
  # Conservative full-bar low mark can overstate post-stop exposure; never call it exact drawdown.
  adverse_dd=max(adverse_dd,1-(cash+qty*l)/peak)
  if qty and l<=stop:sell(stop,t,'stop_within_bar');action.append('stop')
  elif qty and h>=target:sell(target,t,'target_within_bar');action.append('target')
  if t+step==end:sell(close,end,'scheduled_close');pending=None;action.append('session_end')
  equity=cash+qty*close;peak=max(peak,equity);dd=max(dd,1-equity/peak);exposure+=qty*close/CAPITAL
  if equity<=CAPITAL*.99:blocked=True
  curve.append(equity)
  if collect:rows.append({'time':t+step,'equity':equity,'cash':cash,'qty':qty,'stop':stop,'action':'|'.join(action) or ('hold' if qty else 'cash'),'feature_available_ms':f['closed_ms'] if f else None})
 assert qty==0 and abs(sum(x['pnl'] for x in trades)-(cash-CAPITAL))<1e-7
 return {'day':day,'model':c.id,'pnl':cash-CAPITAL,'return_pct':(cash/CAPITAL-1)*100,'drawdown_pct':dd*100,'adverse_bar_mark_pct':adverse_dd*100,'trades':len(trades),'fees':fees,'slippage':slippage,'turnover':turnover,'exposure_pct':100*exposure/len(bars),'curve':curve,'trade_rows':trades,'fills':fills,'decisions':rows}
