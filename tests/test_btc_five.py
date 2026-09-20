import unittest

from btc_five_research import SPECS, build_features, build_signals, load_bars


class BTCFiveStudyTests(unittest.TestCase):
    def test_five_distinct_bots_are_registered(self):
        self.assertEqual(len(SPECS), 5)
        self.assertEqual(len({bot.id for bot in SPECS}), 5)

    def test_signals_do_not_change_before_future_bar(self):
        bars = load_bars()[:12000]
        before = build_features(bars)
        changed = list(bars)
        last = changed[-1]
        changed[-1] = type(last)(last.time, last.open * 1.4, last.high * 1.4, last.low * 1.4, last.close * 1.4, last.volume * 2.0)
        after = build_features(changed)
        cutoff = len(bars) - 2
        for key in before:
            self.assertEqual(before[key][:cutoff], after[key][:cutoff], key)
        for bot in SPECS:
            self.assertEqual(build_signals(bars, before, bot)[:cutoff], build_signals(changed, after, bot)[:cutoff], bot.id)

    def test_data_has_no_duplicate_open_times(self):
        bars = load_bars()
        times = [bar.time for bar in bars]
        self.assertEqual(len(times), len(set(times)))
        self.assertTrue(all(b - a == 15 * 60 * 1000 for a, b in zip(times, times[1:])))


if __name__ == "__main__":
    unittest.main()
