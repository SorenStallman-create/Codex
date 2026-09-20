import unittest,json
from dataclasses import replace
from app import Bar
from intraday_engine import Config,Engine,features,simulate
from intraday_data import MINUTE,FIVE,day_bounds
FILTERS={'step':.001,'min_qty':.001,'max_qty':1e9,'min_notional':1}
START,END=day_bounds('2026-09-16')
def bar(t=START,o=100,h=100.2,l=99.9,c=100.1):return Bar(t,o,h,l,c,100)
def f(t=START):return {'closed_ms':t,'closed_utc':'2026-09-16T04:00:00Z','day':'2026-09-16','close':100,'previous_close':99,'atr':1,'previous_atr':1,'vwap':98,'previous_vwap':98,'previous_same_day':True,'rsi':40,'ema9':99,'ema21':98.5,'ema50':98,'ema50_prior6':97,'volume':200,'prior_volume20':100,'touch2':True,'high6':99}
class IntradayTests(unittest.TestCase):
 def test_risk_and_overnight_close(self):
  e=Engine(Config('momentum',2),FILTERS,START,END,capital=10000);t=END-20*MINUTE;e.process(bar(t),f(t));en=e.s['entry']
  self.assertLessEqual(en['planned_loss'],25+1e-8);self.assertLessEqual(en['qty']*en['entry_fill'],9500)
  e.process(bar(END-MINUTE));self.assertEqual(e.s['qty'],0);self.assertEqual(e.trades[-1]['exit_reason'],'day_end')
 def test_both_touched_stop_first(self):
  e=Engine(Config('momentum',2),FILTERS,START,END);e.process(bar(h=110,l=90),f())
  self.assertEqual(e.trades[0]['exit_reason'],'stop');self.assertLess(e.trades[0]['net_pnl'],0)
 def test_gap_stop(self):
  e=Engine(Config('momentum',2),FILTERS,START,END);e.process(bar(),f());e.process(bar(START+MINUTE,o=90,h=91,l=89,c=90))
  self.assertEqual(e.trades[-1]['exit_reason'],'gap_stop');self.assertAlmostEqual(e.trades[-1]['exit_fill'],90*.9998)
 def test_cost_hurdle(self):
  e=Engine(Config('momentum',2),FILTERS,START,END);feature=f();feature['atr']=.001;e.process(bar(),feature)
  self.assertEqual(e.s['qty'],0);self.assertIn('skip_insufficient_move_after_costs',e.events)
 def test_delay(self):
  e=Engine(Config('momentum',2,delay=1),FILTERS,START,END);e.process(bar(),f());self.assertEqual(e.s['qty'],0)
  e.process(bar(START+MINUTE,o=101,h=101.1,l=100.9,c=101));self.assertAlmostEqual(e.fills[0]['price'],101*1.0002)
 def test_no_new_entry_near_close(self):
  t=END-10*MINUTE;e=Engine(Config('momentum',2),FILTERS,START,END);e.process(bar(t),f(t));self.assertEqual(e.s['qty'],0)
 def test_future_duplicate_and_wrong_day_rejected(self):
  e=Engine(Config('momentum',2),FILTERS,START,END)
  with self.assertRaises(ValueError):e.process(bar(),f(START+FIVE))
  e.process(bar(),f())
  with self.assertRaises(ValueError):e.process(bar(),f())
  with self.assertRaises(ValueError):e.process(bar(END))
 def test_minute_max_hold(self):
  e=Engine(Config('momentum',2,max_hold=2),FILTERS,START,END);e.process(bar(),f());e.process(bar(START+2*MINUTE))
  self.assertEqual(e.trades[-1]['exit_reason'],'time_limit')
 def test_incremental_state_identical(self):
  c=Config('momentum',2);a=Engine(c,FILTERS,START,END);a.process(bar(),f());b=Engine(c,FILTERS,START,END,state=json.loads(json.dumps(a.s)))
  for t in range(START+MINUTE,START+10*MINUTE,MINUTE):a.process(bar(t));b.process(bar(t))
  self.assertEqual(a.s,b.s)
 def test_features_are_causal(self):
  data=[bar(START+i*MINUTE,o=100+i*.001,h=101+i*.001,l=99+i*.001,c=100+i*.001) for i in range(600)]
  full=features(data);prefix=features(data[:503]);self.assertEqual(prefix,{k:v for k,v in full.items() if k<=START+503*MINUTE})
 def test_missing_minute_cannot_create_signal(self):
  data=[bar(START+i*MINUTE) for i in range(600) if i!=498];self.assertNotIn(START+500*MINUTE,features(data))
 def test_daily_loss_is_halt(self):
  e=Engine(Config('momentum',2,daily_loss=.0001),FILTERS,START,END);e.process(bar(l=90),f());self.assertTrue(e.s['blocked'])

class IntradayEvidenceTests(unittest.TestCase):
 def test_daily_results_reconcile_and_flat_at_close(self):
  from pathlib import Path
  import csv
  root=Path(__file__).resolve().parents[1];folder=root/'research/intraday_v1'
  if not (folder/'daily_summary.csv').exists():self.skipTest('Run intraday evaluation first')
  sums={};cash={};inventory={};counts={}
  with (folder/'fills.csv').open() as f:
   for r in csv.DictReader(f):
    k=(r['market'],r['model'],r['day'],r['asset']);capital=10000 if r['market']=='spy' else 10000/3
    cash.setdefault(k,capital);inventory.setdefault(k,0.);q=float(r['qty']);gross=q*float(r['price']);fee=float(r['fee'])
    cash[k]+= -gross-fee if r['side']=='buy' else gross-fee;inventory[k]+=q if r['side']=='buy' else -q
    self.assertAlmostEqual(cash[k],float(r['cash']),places=7);self.assertGreaterEqual(cash[k],-1e-7)
  for q in inventory.values():self.assertAlmostEqual(q,0,places=7)
  with (folder/'trades.csv').open() as f:
   for r in csv.DictReader(f):
    k=(r['market'],r['model'],r['day']);sums[k]=sums.get(k,0)+float(r['net_pnl']);counts[k]=counts.get(k,0)+1
    self.assertLessEqual(float(r['planned_loss']),float(r['entry_equity'])*.0025+1e-6)
    self.assertLessEqual(float(r['hold_minutes']),120)
  with (folder/'daily_summary.csv').open() as f:
   for r in csv.DictReader(f):
    k=(r['market'],r['model'],r['day']);self.assertAlmostEqual(sums.get(k,0),float(r['net_pnl']),places=7);self.assertEqual(counts.get(k,0),int(r['trades']))
 def test_frozen_hashes_unchanged(self):
  from pathlib import Path
  from research_weekly import sha256
  root=Path(__file__).resolve().parents[1];folder=root/'research/intraday_v1'
  if not (folder/'frozen.json').exists():self.skipTest('Freeze candidates first')
  d=json.loads((folder/'frozen.json').read_text())
  self.assertEqual(sha256(folder/'PLAN.md'),d['plan_sha256'])
  for name,h in d['hashes'].items():self.assertEqual(sha256(root/name),h)
