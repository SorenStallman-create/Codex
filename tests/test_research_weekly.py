import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import Bar
from research_weekly import simulate, windows_for


def bars(prices):
    result = []
    for i, (op, close) in enumerate(prices):
        result.append(Bar(i * 3_600_000, op, max(op, close) + 1, min(op, close) - 1, close, 10))
    return result


class WeeklyResearchTests(unittest.TestCase):
    def test_signal_executes_on_next_open_and_uses_spot_cash(self):
        data = bars([(100, 100), (100, 110), (120, 120), (120, 120)])
        result = simulate(data, [0, 1, 1, 0], [{}] * len(data), 2, 4,
                          fee=0, slip=0, allocation=0.10, collect=True)
        self.assertEqual(result["decision_rows"][0]["order_action"], "open_long")
        self.assertEqual(result["trades_rows"][0]["entry_time_utc"], "1970-01-01T02:00:00Z")
        self.assertAlmostEqual(result["trades_rows"][0]["entry_fill"], 120)
        self.assertAlmostEqual(result["net_return_pct"], 0.0)

    def test_short_signal_never_creates_spot_short(self):
        data = bars([(100, 100), (100, 100), (100, 100)])
        result = simulate(data, [-1, -1, -1], [{}] * len(data), 1, 3,
                          fee=0, slip=0, allocation=0.1, collect=True)
        self.assertEqual(result["closed_trades"], 0)
        self.assertTrue(all(row["spot_quantity_after_fill"] in (0, "0") for row in result["decision_rows"]))
        self.assertTrue(any("no_spot_shorting" in row["order_action"] for row in result["decision_rows"]))

    def test_costs_reduce_flat_round_trip_return(self):
        data = bars([(100, 100), (100, 100), (100, 100), (100, 100)])
        signals = [0, 1, 1, 0]
        free = simulate(data, signals, [{}] * len(data), 2, 4, fee=0, slip=0, collect=False)
        charged = simulate(data, signals, [{}] * len(data), 2, 4, fee=0.0015, slip=0.0005, collect=False)
        self.assertGreater(free["net_return_pct"], charged["net_return_pct"])

    def test_windows_are_exact_seven_day_aligned_ranges(self):
        now = __import__("datetime").datetime(2026, 9, 17, 2, 20, tzinfo=__import__("datetime").timezone.utc)
        result = windows_for(now)
        for start, end in result.values():
            self.assertEqual(end - start, 7 * 24 * 3_600_000)
            self.assertEqual(start % 3_600_000, 0)
            self.assertEqual(end % 3_600_000, 0)
        self.assertGreater(result["recent_7d"][0], result["random_week_~3mo_ago"][1])


if __name__ == "__main__":
    unittest.main()
