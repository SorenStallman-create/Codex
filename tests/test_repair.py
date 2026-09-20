import unittest
import json
import csv
from pathlib import Path
from app import Bar, make_signals, strategy_catalog
from research_weekly import simulate
from repair_replay import select_balanced, read, QUOTAS

class ReplayTests(unittest.TestCase):
    def test_family_comparison_has_25_unique_models_and_required_families(self):
        selected=select_balanced(read('selection_scores.csv'))
        self.assertEqual(len({r['id'] for r in selected}),25)
        self.assertEqual({k:sum(r['family']==k for r in selected) for k in QUOTAS},QUOTAS)

    def test_entry_slippage_matches_quantity_times_price_difference(self):
        bars=[Bar(i*3600000,100,111,99,110,10) for i in range(3)]
        r=simulate(bars,[1,1,1],[{}]*3,1,3,slip=.01,collect=True)
        d=r['decision_rows'][0]
        self.assertAlmostEqual(d['estimated_slippage_cost'],d['spot_quantity_after_fill']*(d['fill_price']-100),places=4)
        self.assertAlmostEqual(sum(t['net_cash_pnl'] for t in r['trades_rows']),r['net_pnl'])
        self.assertIsNone(r['profit_factor'])
        self.assertEqual(r['profit_factor_status'],'no_losses')

    def test_every_strategy_is_causal(self):
        bars=[Bar(i*3600000,100+i*.03,104+i*.03,96+i*.03,100+i*.03+(i%7-3)*.4,10) for i in range(260)]
        for spec in strategy_catalog():
            with self.subTest(model=spec.id):
                self.assertEqual(make_signals(bars,spec)[:240],make_signals(bars[:240],spec))

    def test_replay_trade_and_decision_accounts_reconcile(self):
        payload=json.loads((Path(__file__).resolve().parents[1]/'research/repaired_replay/data.json').read_text())
        for run in payload['runs']:
            with self.subTest(model=run['summary']['model'],window=run['summary']['window']):
                self.assertAlmostEqual(sum(t['net_cash_pnl'] for t in run['trades']),run['summary']['net_pnl'],places=7)
                self.assertAlmostEqual(run['decisions'][-1]['equity_marked_at_close'],run['summary']['ending_equity'],places=4)
                self.assertEqual(len(run['equity']),170)
