import json
import tempfile
import unittest
from pathlib import Path

import benchmark
import holdout_registry
import power
import preregister


class Phase1MeasurementTests(unittest.TestCase):
    def test_minimum_detectable_sharpe_matches_brief(self):
        self.assertAlmostEqual(power.min_detectable_sharpe(90), 4.685296148590823, places=9)

    def test_sharpe_ci_is_inconclusive_for_short_noisy_sample(self):
        result = power.metrics([0.01, -0.01, 0.005, -0.003])
        self.assertLessEqual(result["sharpe_ci_low"], 0.0)
        self.assertEqual(result["power_verdict"], "INCONCLUSIVE")

    def test_matched_spy_uses_cash_below_one_and_borrowing_above_one(self):
        low, low_meta = benchmark.matched_spy_returns([0.01, -0.01] * 20, [0.001, -0.001] * 20)
        high, high_meta = benchmark.matched_spy_returns([0.001, -0.001] * 20, [0.01, -0.01] * 20)
        self.assertLessEqual(low_meta["matched_leverage"], 1.0)
        self.assertGreater(high_meta["matched_leverage"], 1.0)
        self.assertEqual(len(low), len(high))

    def test_registry_blocks_reopening_test_period_by_default(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "holdout_registry.json"
            periods = {"holdout": {"start": "2020-01-01", "end_exclusive": "2021-01-01"}, "development": {"start": "2019-01-01", "end_exclusive": "2020-01-01"}}
            holdout_registry.register(path, universe=["SPY", "AAA"], periods=periods, run_id="one")
            with self.assertRaises(RuntimeError):
                holdout_registry.register(path, universe=["SPY", "AAA"], periods=periods, run_id="two")
            result = holdout_registry.register(path, universe=["SPY", "AAA"], periods=periods, run_id="two", allow_reopen=True, reason="audit")
            self.assertEqual(len(result["warnings"]), 1)

    def test_preregistration_hash_ignores_run_metadata(self):
        spec = {"strategy": "x", "hypothesis": "h", "run_id": "a", "created_utc": "one"}
        same = {**spec, "run_id": "b", "created_utc": "two"}
        self.assertEqual(preregister.spec_hash(spec), preregister.spec_hash(same))

    def test_missing_matching_preregistration_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(RuntimeError):
                preregister.require_matching({"strategy": "x"}, Path(temp))


if __name__ == "__main__":
    unittest.main()
