import datetime as dt
import focused_overnight as fo
import unittest

import overnight_equities as oe


class OvernightEquityTests(unittest.TestCase):
    def test_model_ids_are_unique_and_benchmark_is_not_a_candidate(self):
        ids = [m.id for m in oe.MODELS]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertNotIn(oe.BENCHMARK, ids)

    def test_net_return_includes_both_sides_of_slippage(self):
        gross = 110.0 / 100.0 - 1.0
        net = oe.net_return(100.0, 110.0, fee=0.0, slip=0.001)
        self.assertAlmostEqual(net, (109.89 / 100.1) - 1.0, places=12)
        self.assertLess(net, gross)

    def test_rank_and_selection_are_causal_to_current_rows(self):
        spec = oe.MODELS[0]
        rows = []
        for i, symbol in enumerate(("AAA", "BBB", "CCC")):
            rows.append({
                "symbol": symbol,
                "adj_close": 100.0,
                "dollar_volume": 100_000_000.0,
                "market_regime": True,
                "overnight_mean60": float(i),
                "mom20": float(i),
                "resid_mom20": float(i),
                "volume_ratio20": 1.0,
                "vol20": 0.01,
            })
        self.assertEqual(oe.score_candidates(rows, spec)[0]["symbol"], "CCC")
        original = oe.score_candidates(rows, spec)[0]["symbol"]
        for key in ("overnight_mean60", "mom20", "resid_mom20"):
            rows[0][key] = 100.0
        self.assertNotEqual(oe.score_candidates(rows, spec)[0]["symbol"], original)

    def test_periods_are_chronological(self):
        ends = [oe.PERIODS[name][1] for name in ("development", "validation", "holdout", "recent")]
        self.assertEqual(ends, sorted(ends))

    def test_focused_selection_is_top_thirty_percent_of_five(self):
        rows = []
        for index, model in enumerate(oe.MODELS):
            rows.append({
                "period": "validation",
                "model": model.id,
                "name": model.name,
                "quality": float(5 - index),
                "return_pct": float(index),
                "max_drawdown_pct": float(index),
            })
        selected, ranked = fo.select_models({"summary": rows}, 0.30)
        self.assertEqual([row["model"] for row in selected], [oe.MODELS[0].id, oe.MODELS[1].id])
        self.assertEqual(len(ranked), 5)

    def test_focused_portfolio_caps_total_allocation(self):
        payload = {
            "m1": ({}, [{"date": "2026-01-01", "next_date": "2026-01-02", "symbol": "AAA", "net_return_pct": 10.0}], [], []),
            "m2": ({}, [{"date": "2026-01-01", "next_date": "2026-01-02", "symbol": "BBB", "net_return_pct": -10.0}], [], []),
        }
        summary, rows = fo.portfolio_replay(payload, 0.50, "test", 0.0, 0.0)
        self.assertAlmostEqual(summary["allocation"], 0.50)
        self.assertAlmostEqual(summary["return_pct"], 0.0)
        self.assertEqual(rows[0]["allocation_per_model"], 0.25)


if __name__ == "__main__":
    unittest.main()
