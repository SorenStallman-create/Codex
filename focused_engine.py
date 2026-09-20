"""Causal long/cash engine shared by research and incremental paper replay."""
from __future__ import annotations
import datetime as dt
import math
from dataclasses import asdict, dataclass
from typing import Any
from app import Bar, ema, sma, rsi
from research_weekly import HOUR_MS, iso

FOUR=4*HOUR_MS
SLEEVE_CAPITAL=10_000/3

@dataclass(frozen=True)
class Config:
    family:str
    setting:int
    fee:float=.0015
    slip:float=.0005
    risk:float=.005
    cap:float=.5
    stop_atr:float=3.0
    delay:int=0
    regime:bool=True
    stops:bool=True
    risk_sizing:bool=True
    daily_limit:float=.02
    drawdown_limit:float=.15
    @property
    def id(self):return f'{self.family}-{self.setting}'

FAMILIES={'trend':'Trend Guard','breakout':'Breakout Guard','pullback':'Pullback Guard'}
SETTINGS={'trend':(10,20,30),'breakout':(20,40,60),'pullback':(25,30,35)}

def configurations():return [Config(f,n) for f,ns in SETTINGS.items() for n in ns]

def aggregate(hourly, incomplete_hours=()):
    groups={}
    for b in hourly: groups.setdefault(b.time//FOUR*FOUR,[]).append(b)
    out=[]
    for t,group in sorted(groups.items()):
        if not any(b.time in incomplete_hours for b in group) and len(group)==4 and [b.time for b in group]==[t+j*HOUR_MS for j in range(4)]:
            out.append(Bar(t,group[0].open,max(b.high for b in group),min(b.low for b in group),group[-1].close,sum(b.volume for b in group)))
    return out

def features(hourly, incomplete_hours=()):
    bars=aggregate(hourly,incomplete_hours);closes=[b.close for b in bars]
    avgs={n:sma(closes,n) for n in (10,20,30,50)}
    trend=ema(closes,200);rs=rsi(closes,14)
    trs=[b.high-b.low if i==0 else max(b.high-b.low,abs(b.high-bars[i-1].close),abs(b.low-bars[i-1].close)) for i,b in enumerate(bars)]
    atrs=[None]*len(bars)
    if len(bars)>=14:
        atrs[13]=sum(trs[:14])/14
        for i in range(14,len(bars)):atrs[i]=(atrs[i-1]*13+trs[i])/14
    out={}
    for i,b in enumerate(bars):
        if i<205:continue
        f={'signal_close_ms':b.time+FOUR,'signal_close_utc':iso(b.time+FOUR),'close':b.close,'atr':atrs[i],
           'ema200':trend[i],'ema200_prior6':trend[i-6],'rsi14':rs[i],'rsi14_previous':rs[i-1],
           'prior_low10':min(x.low for x in bars[i-10:i])}
        f.update({f'sma{n}':avgs[n][i] for n in avgs})
        f.update({f'prior_high{n}':max(x.high for x in bars[i-n:i]) for n in (20,40,60)})
        out[b.time+FOUR]=f
    return out

def rules(config,f):
    regime=f['close']>f['ema200'] and f['ema200']>f['ema200_prior6']
    allowed=regime or not config.regime
    if config.family=='trend':
        entry=f[f'sma{config.setting}']>f['sma50'] and allowed
        exit_=f[f'sma{config.setting}']<f['sma50'] or (config.regime and f['close']<f['ema200'])
    elif config.family=='breakout':
        entry=f['close']>f[f'prior_high{config.setting}'] and allowed
        exit_=f['close']<f['prior_low10'] or (config.regime and f['close']<f['ema200'])
    elif config.family=='pullback':
        entry=f['rsi14_previous']<=config.setting<f['rsi14'] and allowed
        exit_=f['rsi14']>=55 or (config.regime and f['close']<f['ema200'])
    else:raise ValueError(config.family)
    return entry,exit_,regime

def quantity(config,equity,cash,market_open,atr,filters):
    fill=market_open*(1+config.slip)
    # Planned worst fill at initial stop; gaps can exceed this sizing target.
    stop=max(0,market_open-config.stop_atr*atr)
    planned_exit=stop*(1-config.slip)
    loss_per_unit=fill*(1+config.fee)-planned_exit*(1-config.fee)
    by_risk=equity*config.risk/loss_per_unit if config.risk_sizing and loss_per_unit>0 else float('inf')
    q=min(by_risk,equity*config.cap/fill,cash/(fill*(1+config.fee)),filters['max_qty'])
    step=filters['step'];q=math.floor((q+1e-12)/step)*step
    if q<filters['min_qty'] or q*fill<filters['min_notional']:return 0,stop,loss_per_unit
    return q,stop,loss_per_unit

class Engine:
    def __init__(self,config:Config,filters:dict,capital=SLEEVE_CAPITAL,state=None):
        self.config=config;self.filters=filters;self.capital=capital
        self.s=dict(cash=capital,qty=0.,stop=None,entry=None,armed=True,cooldown_until=0,
                    pending=None,day=None,day_start=capital,day_blocked=False,peak=capital,
                    halted=False,halt_pending=False,last_time=None,last_feature=None,fees=0.,slippage=0.,turnover=0.)
        if state is not None:self.s.update(state)
        self.fills=[];self.trades=[];self.events=[]

    def _sell(self,market,time,reason,time_precision='hour_open'):
        s=self.s;c=self.config;q=s['qty']
        if not q:return
        fill=market*(1-c.slip);gross=q*fill;fee=gross*c.fee;slip=q*(market-fill)
        proceeds=gross-fee;s['cash']+=proceeds;s['fees']+=fee;s['slippage']+=slip;s['turnover']+=gross
        entry=s['entry'];pnl=proceeds-entry['debit']
        trade={**entry,'exit_time_utc':iso(time),'exit_time_ms':time,'exit_market':market,'exit_fill':fill,'exit_fee':fee,
               'exit_slippage':slip,'net_pnl':pnl,'return_pct':100*pnl/entry['debit'],
               'holding_hours':(time-entry['entry_time_ms'])/HOUR_MS,'exit_reason':reason,'exit_time_precision':time_precision}
        self.trades.append(trade)
        self.fills.append({'time_utc':iso(time),'side':'sell','market':market,'fill':fill,'qty':q,'fee':fee,'slippage':slip,'reason':reason,'cash_after':s['cash'],'time_precision':time_precision})
        s.update(qty=0.,stop=None,entry=None,armed=False,cooldown_until=time+FOUR,pending=None)
        self.events.append(reason)

    def _buy(self,bar,feature):
        s=self.s;c=self.config
        q,stop,planned_loss=quantity(c,s['cash'],s['cash'],bar.open,feature['atr'],self.filters)
        if not q:self.events.append('entry_rejected_minimum');return
        fill=bar.open*(1+c.slip);gross=q*fill;fee=gross*c.fee;slip=q*(fill-bar.open);debit=gross+fee
        s['cash']-=debit;s['qty']=q;s['stop']=stop if c.stops else None
        s['fees']+=fee;s['slippage']+=slip;s['turnover']+=gross
        s['entry']={'entry_time_utc':iso(bar.time),'entry_time_ms':bar.time,'entry_signal_close_utc':feature['signal_close_utc'],
                    'entry_market':bar.open,'entry_fill':fill,'qty':q,'entry_fee':fee,'entry_slippage':slip,'debit':debit,
                    'initial_stop':s['stop'],'planned_stop_loss':q*planned_loss,'entry_equity':s['cash']+debit,
                    'feature_json':__import__('json').dumps(feature,separators=(',',':'))}
        s['armed']=False
        self.fills.append({'time_utc':iso(bar.time),'side':'buy','market':bar.open,'fill':fill,'qty':q,'fee':fee,'slippage':slip,'reason':'rule_entry','cash_after':s['cash'],'time_precision':'hour_open'})
        self.events.append('rule_entry')

    def process(self,bar:Bar,feature:dict|None=None,collect=False):
        s=self.s;c=self.config
        if s['last_time'] is not None and bar.time<=s['last_time']:raise ValueError('Duplicate or reversed execution candle')
        self.events=[];eq_open=s['cash']+s['qty']*bar.open
        day=iso(bar.time)[:10]
        if s['day']!=day:s.update(day=day,day_start=eq_open,day_blocked=False)
        if eq_open<=s['day_start']*(1-c.daily_limit):s['day_blocked']=True
        if feature is not None:
            if feature['signal_close_ms']>bar.time:raise ValueError('Future feature')
            s['last_feature']=feature
            entry,exit_,regime=rules(c,feature)
            if not entry:s['armed']=True
            if s['qty'] and c.stops:
                s['stop']=max(s['stop'],feature['close']-c.stop_atr*feature['atr'])
            if s['qty'] and c.family=='pullback' and bar.time-s['entry']['entry_time_ms']>=42*FOUR:exit_=True
            if s['qty'] and exit_:
                s['pending']={'side':'sell','due':bar.time+c.delay*HOUR_MS,'feature':feature}
            elif not s['qty'] and entry and s['armed'] and bar.time>=s['cooldown_until'] and not s['halted'] and not s['day_blocked']:
                s['pending']={'side':'buy','due':bar.time+c.delay*HOUR_MS,'feature':feature}
        # Protective exits take priority over scheduled discretionary orders.
        if s['qty'] and c.stops and bar.open<=s['stop']:
            self._sell(bar.open,bar.time,'gap_stop')
        if s['halt_pending']:
            if s['qty']:self._sell(bar.open,bar.time,'drawdown_halt')
            s.update(halted=True,halt_pending=False,pending=None)
        pending=s['pending']
        if pending and pending['due']<=bar.time:
            s['pending']=None
            if pending['side']=='sell' and s['qty']:self._sell(bar.open,bar.time,'rule_exit')
            elif pending['side']=='buy' and not s['qty'] and not s['halted'] and not s['day_blocked']:
                self._buy(bar,pending['feature'])
        if s['qty'] and c.stops and bar.low<=s['stop']:
            self._sell(s['stop'],bar.time,'intrahour_stop','within_hour_unknown')
        equity=s['cash']+s['qty']*bar.close
        s['peak']=max(s['peak'],equity)
        if equity<=s['day_start']*(1-c.daily_limit):s['day_blocked']=True
        if equity<=s['peak']*(1-c.drawdown_limit) and not s['halted']:
            s.update(halt_pending=True,halted=True)
        s['last_time']=bar.time
        if not collect:return equity
        f=s['last_feature']
        return {'time_utc':iso(bar.time),'close_time_utc':iso(bar.time+HOUR_MS),'open':bar.open,'high':bar.high,'low':bar.low,'close':bar.close,
                'signal_close_utc':f['signal_close_utc'] if f else '', 'new_signal':feature is not None,
                'actions':'|'.join(self.events) or ('hold_long' if s['qty'] else 'hold_cash'),
                'cash':s['cash'],'qty':s['qty'],'stop':s['stop'],'equity':equity,'day_blocked':s['day_blocked'],'halted':s['halted'],
                'feature_json':__import__('json').dumps(f,separators=(',',':')) if feature else ''}

    def settle(self,bar):
        self._sell(bar.close,bar.time+HOUR_MS,'period_end_settlement','period_end')
        return self.s['cash']

def max_dd(curve):
    peak=curve[0];dd=0
    for v in curve:peak=max(peak,v);dd=max(dd,1-v/peak)
    return dd*100

def simulate(bars,feature_map,config,filters,start,end,collect=False,capital=SLEEVE_CAPITAL,settle=True):
    engine=Engine(config,filters,capital);curve=[capital];times=[start];rows=[];exposure=0;sum_exposure=0.;last=None
    for b in bars:
        if b.time<start:continue
        if b.time>=end:break
        row=engine.process(b,feature_map.get(b.time),collect=collect)
        equity=row['equity'] if collect else row
        curve.append(equity);times.append(b.time+HOUR_MS);last=b
        exposure+=engine.s['qty']>0
        sum_exposure+=engine.s['qty']*b.close/equity if equity else 0
        if collect:rows.append(row)
    if last is None:raise ValueError('No execution candles')
    if settle:
        ending=engine.settle(last);curve[-1]=ending
        if collect:rows.append({'time_utc':iso(end),'close_time_utc':iso(end),'actions':'period_end_settlement','cash':ending,'qty':0,'equity':ending})
    else:ending=curve[-1]
    pnls=[t['net_pnl'] for t in engine.trades];wins=sum(p>0 for p in pnls);loss=-sum(p for p in pnls if p<0);grosswin=sum(p for p in pnls if p>0)
    n=len(curve)-1
    result={'model':config.id,'return_pct':100*(ending/capital-1),'net_pnl':ending-capital,'ending_equity':ending,
            'max_drawdown_pct':max_dd(curve),'trades':len(pnls),'wins':wins,'win_rate_pct':100*wins/len(pnls) if pnls else None,
            'profit_factor':grosswin/loss if loss else None,'fees':engine.s['fees'],'slippage':engine.s['slippage'],
            'turnover':engine.s['turnover']/capital,'hours_in_market_pct':100*exposure/n,'mean_capital_exposure_pct':100*sum_exposure/n,
            'halted':engine.s['halted'],'signal_entries':sum(f['side']=='buy' for f in engine.fills),
            'curve':curve,'times':times,'trade_rows':engine.trades,'fill_rows':engine.fills,'decision_rows':rows,'state':engine.s}
    return result
