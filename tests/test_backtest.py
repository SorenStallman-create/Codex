import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import Bar, StrategySpec, backtest, make_signals, run_batch, strategy_catalog


def rising_bars(count=420):
    bars = []
    for i in range(count):
        base = 100 + i * 0.12 + math.sin(i / 7) * 3
        bars.append(Bar(1_700_000_000_000 + i * 86_400_000, base, base + 2, base - 2, base + math.sin(i / 3), 1000 + i))
    return bars


class TradingLabTests(unittest.TestCase):
    def test_catalog_has_large_batch_and_featured_strategy(self):
        catalog = strategy_catalog()
        self.assertGreaterEqual(len(catalog), 60)
        self.assertTrue(any(spec.family == "andean_laguerre" for spec in catalog))

    def test_all_signal_families_match_bar_count(self):
        bars = rising_bars()
        representative = {}
        for spec in strategy_catalog():
            representative.setdefault(spec.family, spec)
        for family, spec in representative.items():
            with self.subTest(family=family):
                signals = make_signals(bars, spec)
                self.assertEqual(len(signals), len(bars))
                self.assertTrue(set(signals) <= {-1, 0, 1})

    def test_backtest_applies_signal_on_next_bar(self):
        bars = [Bar(i, 100, 101, 99, 100, 1) for i in range(6)]
        bars[1] = Bar(1, 100, 201, 99, 200, 1)
        bars[2] = Bar(2, 200, 201, 199, 200, 1)
        bars[3] = Bar(3, 200, 201, 199, 200, 1)
        bars[4] = Bar(4, 200, 201, 199, 200, 1)
        bars[5] = Bar(5, 200, 201, 199, 200, 1)
        signals = [0, 1, 1, 1, 1, 1]
        result = backtest(bars, signals, commission=0)
        self.assertAlmostEqual(result["return"], 0.0, places=9)

    def test_batch_is_sorted_and_has_oos_metrics(self):
        result = run_batch(rising_bars())
        scores = [row["score"] for row in result["results"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertIn("max_drawdown", result["results"][0]["oos"])
        self.assertEqual(result["results"][0]["rank"], 1)

    def test_unknown_strategy_rejected(self):
        with self.assertRaises(ValueError):
            make_signals(rising_bars(), StrategySpec("x", "unknown", "Unknown", {}))


if __name__ == "__main__":
    unittest.main()
