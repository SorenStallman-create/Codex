"""Independent contracts for daily v2 research; no network or frozen-file writes."""
import datetime as dt
import math
import unittest
from dataclasses import replace
from intraday_v2_data import bounds, validate, STEP, MINUTE
from intraday_v2_engine import Config, CAPITAL, features, run_day

DAY='2026-09-17'
def bars(day=DAY,step=STEP):
 s,e=bounds(day)
 return [[t,100.,100.1,99.9,100.,1000.] for t in range(s,e,step)]
def late_feature(t):
 return {'closed_ms':t,'minute':360,'price':100.,'vwap':99.,'first_return':.01,'mean_daily_range':4.}
def fixture(step=STEP):
 bs=bars(step=step);s,e=bounds(DAY);t=e-30*MINUTE
 return bs,{t:late_feature(t)},t

class V2Contracts(unittest.TestCase):
 def test_exact_session_grid(self):
  good=bars()
  variants=[good[1:],good[:-1],good[:20]+good[21:],good[:20]+[good[19]]+good[20:],[good[-1]]]
  for bad in variants:
   with self.subTest(length=len(bad)):
    with self.assertRaises(ValueError):run_day(Config('cash',0),DAY,bad,{})
 def test_nonfinite_and_invalid_ohlc(self):
  for col,value in [(1,float('nan')),(2,float('inf')),(3,0.),(5,-1.)]:
   bs=bars();bs[0][col]=value
   with self.assertRaises(ValueError):validate(DAY,bs)
 def test_no_signal_is_cash(self):
  result=run_day(Config('late',0),DAY,bars(),{})
  self.assertEqual(result['pnl'],0);self.assertEqual(result['trades'],0)
  self.assertTrue(all(v==CAPITAL for v in result['curve']))
 def test_explicit_fee_slippage_and_cash_identity(self):
  bs,fm,t=fixture();cfg=Config('late',0);result=run_day(cfg,DAY,bs,fm);tr=result['trade_rows'][0]
  # At flat $100, stop is $99. Buy/sell fills and both fees determine exact P&L.
  buy=100*(1+cfg.slip);sell=100*(1-cfg.slip)
  risk_per_share=buy*(1+cfg.fee)-99*(1-cfg.slip)*(1-cfg.fee)
  q=math.floor(min(CAPITAL/(buy*(1+cfg.fee)),CAPITAL*cfg.cap/buy,CAPITAL*cfg.risk/risk_per_share))
  expected=q*(sell-buy)-q*cfg.fee*(buy+sell)
  self.assertEqual(tr['qty'],q);self.assertAlmostEqual(result['pnl'],expected)
  self.assertAlmostEqual(result['fees'],q*cfg.fee*(buy+sell))
  self.assertAlmostEqual(result['slippage'],q*100*2*cfg.slip)
  self.assertAlmostEqual(result['pnl'],-result['fees']-result['slippage'])
  self.assertLessEqual(tr['planned_loss'],CAPITAL*cfg.risk)
  self.assertTrue(all(r['cash']>=0 for r in result['decisions']))
  self.assertEqual(result['decisions'][-1]['qty'],0)
  self.assertEqual(tr['exit_ms'],bounds(DAY)[1]);self.assertEqual(tr['reason_exit'],'scheduled_close')
 def test_gap_stop_uses_worse_open(self):
  bs,fm,t=fixture();i=next(i for i,b in enumerate(bs) if b[0]==t)+1
  bs[i]=[bs[i][0],90.,91.,89.,90.,1000.]
  result=run_day(Config('late',0),DAY,bs,fm);tr=result['trade_rows'][0]
  self.assertEqual(tr['reason_exit'],'gap_stop');self.assertAlmostEqual(tr['exit_price'],90*.9999)
  self.assertLess(tr['pnl'],-tr['planned_loss'])
 def test_stop_and_target_collision_is_stop(self):
  bs=bars();s,e=bounds(DAY);t=s+30*MINUTE;i=next(i for i,b in enumerate(bs) if b[0]==t)
  bs[i]=[t,100.,103.,98.,100.,1000.]
  fm={t:{'closed_ms':t,'minute':30,'price':100.,'vwap':98.,'high15':99.,'low15':99.,'volume_ratio':2.}}
  result=run_day(Config('orb',15),DAY,bs,fm)
  self.assertEqual(result['trade_rows'][0]['reason_exit'],'stop_within_bar')
  self.assertAlmostEqual(result['trade_rows'][0]['exit_price'],99*.9999)
 def test_one_minute_delay_requires_minute_execution_grid(self):
  bs,fm,t=fixture(MINUTE);result=run_day(Config('late',0,delay=1),DAY,bs,fm,step=MINUTE)
  self.assertEqual(result['trade_rows'][0]['entry_ms'],t+MINUTE)
  self.assertEqual(result['trade_rows'][0]['signal_ms'],t)
  bs,fm,t=fixture();coarse=run_day(Config('late',0,delay=1),DAY,bs,fm)
  self.assertEqual(coarse['trade_rows'][0]['entry_ms'],t+STEP)
 def test_future_feature_rejected(self):
  bs,fm,t=fixture();fm[t]['closed_ms']=t+MINUTE
  with self.assertRaises(ValueError):run_day(Config('late',0),DAY,bs,fm)
 def test_feature_prefix_causality(self):
  first=dt.date(2026,8,24);days=[]
  for i in range(30):
   d=first+dt.timedelta(days=i)
   if d.weekday()<5:days.append(d.isoformat())
  days=days[:15];groups={day:bars(day) for day in days};before=features(groups)
  last=days[-1];cut=40;changed={day:[b[:] for b in series] for day,series in groups.items()}
  for b in changed[last][cut:]:b[1:5]=[150.,160.,140.,155.]
  after=features(changed);cutoff=groups[last][cut][0]
  self.assertEqual({k:v for k,v in before[last].items() if k<=cutoff},{k:v for k,v in after[last].items() if k<=cutoff})
  extra=(first-dt.timedelta(days=3)).isoformat();extended={extra:bars(extra),**groups}
  self.assertEqual(features(extended)[last],before[last])
 def test_future_bar_cannot_change_earlier_execution(self):
  bs,fm,t=fixture();base=run_day(Config('late',0),DAY,bs,fm)
  altered=[b[:] for b in bs];cut=len(bs)-2
  for b in altered[cut:]:b[1:5]=[80.,81.,79.,80.]
  changed=run_day(Config('late',0),DAY,altered,fm)
  self.assertEqual(base['decisions'][:cut],changed['decisions'][:cut])
  self.assertEqual(base['fills'][0],changed['fills'][0])

if __name__=='__main__':unittest.main()
