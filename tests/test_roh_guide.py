import math
import unittest

from roh_guide_research import PairConfig, causal_signals, ols_beta


class GuideContracts(unittest.TestCase):
    def test_beta_is_causal_helper_and_bounded(self):
        self.assertAlmostEqual(ols_beta([1, 2, 3], [1, 2, 3]), 1.0)
        self.assertGreaterEqual(ols_beta([1, 2], [1, 100]), 0.1)
        self.assertLessEqual(ols_beta([1, 2], [1, 100]), 5.0)

    def test_config_is_canonical(self):
        c = PairConfig("canonical")
        self.assertEqual(c.z_entry, 2.0)
        self.assertEqual(c.z_exit, 0.5)
        self.assertEqual(c.max_hold_bars, 18)

    def test_causal_signals_do_not_use_current_bar_in_history(self):
        # 11 short synthetic sessions; adding a huge value to the current
        # bar must affect its z-score, but cannot change the previous bar's
        # estimation inputs.  This test exercises the calculation contract
        # without relying on market performance.
        days = [f"2026-01-{i:02d}" for i in range(1, 12)]
        data = {"SPY": {}, "QQQ": {}}
        for d in days:
            data["SPY"][d] = [[j * 300000, 100 + j * .01, 100 + j * .02,
                                99 + j * .01, 100 + j * .01, 1000] for j in range(3)]
            data["QQQ"][d] = [[j * 300000, 100 + j * .015, 100 + j * .025,
                                99 + j * .005, 100 + j * .015, 1000] for j in range(3)]
        c = PairConfig("test", lookback_bars=6)
        first = causal_signals(data, days, c)
        changed = {s: {d: [list(x) for x in bars] for d, bars in data[s].items()} for s in data}
        changed["SPY"][days[-1]][0][4] = 1000
        second = causal_signals(changed, days, c)
        prior_day = days[-2]
        self.assertEqual(first[prior_day], second[prior_day])
        self.assertTrue(first[days[-1]])
        self.assertNotEqual(first[days[-1]], second[days[-1]])


if __name__ == "__main__":
    unittest.main()
