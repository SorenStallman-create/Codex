import json
import unittest
from dataclasses import replace
from app import Bar
from focused_engine import Config, Engine, features, quantity, simulate, HOUR_MS, FOUR

FILTERS={'step':.00001,'min_qty':.00001,'max_qty':1e9,'min_notional':10}
def bar(t=0,o=100,h=102,l=99,c=101):return Bar(t,o,h,l,c,100)
def feature(t=0,close=100,fast=102,atr=1):
 return {'signal_close_ms':t,'signal_close_utc':'1970-01-01T00:00:00Z','close':close,'atr':atr,'ema200':95,'ema200_prior6':94,'rsi14':40,'rsi14_previous':29,'sma20':fast,'sma50':100,'prior_low10':90,'prior_high20':99}

class FocusedEngineTests(unittest.TestCase):
 def test_risk_and_cap_includes_round_trip_cost(self):
  c=Config('trend',20);q,stop,loss=quantity(c,10000,10000,100,1,FILTERS)
  self.assertLessEqual(q*loss,50+1e-8)
  self.assertLessEqual(q*100*(1+c.slip),5000)
  self.assertAlmostEqual(q/FILTERS['step'],round(q/FILTERS['step']))
 def test_future_feature_is_rejected(self):
  e=Engine(Config('trend',20),FILTERS)
  with self.assertRaises(ValueError):e.process(bar(),feature(FOUR))
 def test_same_hour_stop_is_possible(self):
  e=Engine(Config('trend',20),FILTERS);e.process(bar(l=90),feature())
  self.assertEqual(len(e.trades),1);self.assertEqual(e.trades[0]['exit_reason'],'intrahour_stop')
  self.assertLess(e.s['cash'],e.capital)
 def test_gap_stop_uses_open_not_stop(self):
  e=Engine(Config('trend',20),FILTERS);e.process(bar(),feature())
  e.process(bar(HOUR_MS,o=80,h=82,l=79,c=81))
  self.assertAlmostEqual(e.trades[0]['exit_fill'],80*(1-.0005))
  self.assertEqual(e.trades[0]['exit_reason'],'gap_stop')
 def test_delay_is_an_actual_hour(self):
  e=Engine(Config('trend',20,delay=1),FILTERS);e.process(bar(),feature())
  self.assertEqual(e.s['qty'],0)
  e.process(bar(HOUR_MS,o=105,h=106,l=104,c=105))
  self.assertEqual(e.fills[0]['time_utc'],'1970-01-01T01:00:00Z')
  self.assertAlmostEqual(e.fills[0]['fill'],105*1.0005)
 def test_no_same_signal_reentry_after_stop(self):
  e=Engine(Config('trend',20),FILTERS);e.process(bar(l=90),feature())
  e.process(bar(FOUR),feature(FOUR));self.assertEqual(e.s['qty'],0)
  e.process(bar(FOUR*2),feature(FOUR*2,fast=99))
  e.process(bar(FOUR*3),feature(FOUR*3));self.assertGreater(e.s['qty'],0)
 def test_minimum_order_rejection(self):
  q,_,_=quantity(Config('trend',20),1,1,100,1,FILTERS);self.assertEqual(q,0)
 def test_duplicate_bar_rejected(self):
  e=Engine(Config('trend',20),FILTERS);e.process(bar())
  with self.assertRaises(ValueError):e.process(bar())
 def test_incremental_state_equals_continuous(self):
  c=Config('trend',20);a=Engine(c,FILTERS);a.process(bar(),feature())
  b=Engine(c,FILTERS,state=json.loads(json.dumps(a.s)))
  for t in (HOUR_MS,2*HOUR_MS,3*HOUR_MS):
   x=bar(t,o=101,h=104,l=100,c=103);a.process(x);b.process(x)
  self.assertEqual(a.s,b.s)
 def test_feature_prefix_invariance(self):
  data=[bar(i*HOUR_MS,o=100+i*.01,h=102+i*.01,l=98+i*.01,c=101+i*.01) for i in range(1000)]
  full=features(data);prefix=features(data[:900])
  self.assertEqual(prefix,{k:v for k,v in full.items() if k<=900*HOUR_MS})
 def test_missing_hour_blocks_four_hour_signal(self):
  data=[bar(i*HOUR_MS) for i in range(1000) if i!=990]
  self.assertNotIn(992*HOUR_MS,features(data))
 def test_daily_loss_blocks_next_entry(self):
  c=Config('trend',20,stops=False,risk_sizing=False,drawdown_limit=.99)
  e=Engine(c,FILTERS);e.process(bar(),feature())
  e.process(bar(HOUR_MS,o=90,h=91,l=89,c=90))
  self.assertTrue(e.s['day_blocked'])
 def test_drawdown_halt_exits_next_open(self):
  e=Engine(Config('trend',20,stops=False,risk_sizing=False,drawdown_limit=.1),FILTERS)
  e.process(bar(),feature());e.process(bar(HOUR_MS,o=70,h=71,l=69,c=70))
  self.assertTrue(e.s['halt_pending']);e.process(bar(2*HOUR_MS,o=69,h=70,l=68,c=69))
  self.assertEqual(e.s['qty'],0);self.assertTrue(e.s['halted'])
  self.assertEqual(e.trades[-1]['exit_reason'],'drawdown_halt')
 def test_cash_and_trade_reconcile(self):
  data=[bar(),bar(HOUR_MS,o=110,h=111,l=109,c=110)]
  result=simulate(data,{0:feature()},Config('trend',20),FILTERS,0,2*HOUR_MS,collect=True)
  self.assertAlmostEqual(result['net_pnl'],sum(t['net_pnl'] for t in result['trade_rows']))
  self.assertAlmostEqual(result['ending_equity'],result['curve'][-1])

class FocusedEvidenceTests(unittest.TestCase):
 def test_frozen_inputs_and_trade_accounting(self):
  from pathlib import Path
  from research_weekly import sha256
  root=Path(__file__).resolve().parents[1];folder=root/'research/focused_bot'
  if not (folder/'results.json').exists():self.skipTest('Run evaluation to audit artifacts')
  frozen=json.loads((folder/'frozen_selection.json').read_text())
  self.assertEqual(sha256(root/'focused_engine.py'),frozen['engine_sha256'])
  self.assertEqual(sha256(folder/'TEST_PLAN.md'),frozen['plan_sha256'])
  result=json.loads((folder/'results.json').read_text())
  for run in result['runs']:
   with self.subTest(model=run['model'],period=run['period']):
    self.assertAlmostEqual(sum(t['net_pnl'] for t in run['trades']),run['summary']['net_pnl'],places=7)
    self.assertEqual(len(run['trades']),run['summary']['trades'])
    self.assertAlmostEqual(run['daily'][-1]['equity'],run['summary']['ending_equity'],places=7)
    for trade in run['trades']:
     self.assertLessEqual(trade['planned_stop_loss'],trade['entry_equity']*.005+1e-6)
     self.assertLessEqual(trade['entry_fill']*trade['qty'],trade['entry_equity']*.5+1e-6)
 def test_fills_independently_reconcile_cash(self):
  from pathlib import Path
  import csv
  folder=Path(__file__).resolve().parents[1]/'research/focused_bot'
  if not (folder/'fills.csv').exists():self.skipTest('Run evaluation to audit artifacts')
  cash={};inventory={}
  with (folder/'fills.csv').open() as f:
   for row in csv.DictReader(f):
    k=(row['period'],row['model'],row['asset']);cash.setdefault(k,10000/3);inventory.setdefault(k,0.)
    q=float(row['qty']);gross=q*float(row['fill']);fee=float(row['fee'])
    cash[k]+=(-gross-fee) if row['side']=='buy' else (gross-fee)
    inventory[k]+=q if row['side']=='buy' else -q
    self.assertGreaterEqual(cash[k],-1e-7);self.assertGreaterEqual(inventory[k],-1e-7)
    self.assertAlmostEqual(cash[k],float(row['cash_after']),places=7)
  with (folder/'summary.csv').open() as f:
   for row in csv.DictReader(f):
    k=(row['period'],row['model'],row['asset'])
    self.assertAlmostEqual(cash.get(k,10000/3),float(row['ending_equity']),places=7)
    self.assertAlmostEqual(inventory.get(k,0),0,places=7)
