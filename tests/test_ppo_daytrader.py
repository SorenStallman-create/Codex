import sys, unittest
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ppo_daytrader'))
from data import features  # noqa: E402
from env import SPOT, PERP, BARS, run_policy  # noqa: E402


def fake_bars(n=96 * 12, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range('2024-01-01', periods=n, freq='15min', tz='UTC')
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.003, n)))
    o = np.concatenate([[100], c[:-1]])
    b = pd.DataFrame({'open': o, 'high': np.maximum(o, c) * 1.001, 'low': np.minimum(o, c) * 0.999,
                      'close': c, 'volume': rng.uniform(1, 5, n)}, index=idx)
    b['day'] = b.index.floor('D')
    return b


def sessions(days=3, seed=1):
    rng = np.random.default_rng(seed)
    o = 100 * np.exp(np.cumsum(rng.normal(0, 0.002, (days, BARS)), 1))
    c = o * np.exp(rng.normal(0, 0.002, (days, BARS)))
    return {'open': o, 'close': c, 'obs': np.zeros((days, BARS, 17), np.float32)}


class Causality(unittest.TestCase):
    def test_features_ignore_future_bars(self):
        b = fake_bars()
        full = features(b)
        cut = 96 * 9
        trunc = features(b.iloc[:cut])
        # rows before the cut must not change when later bars are removed (no look-ahead)
        np.testing.assert_allclose(full.iloc[96 * 7:cut].values, trunc.iloc[96 * 7:cut].values, atol=1e-5)


class Accounting(unittest.TestCase):
    def test_intraday_buy_hold_pays_two_sides(self):
        s = sessions()
        r = run_policy(SPOT, s, np.arange(3), lambda obs, j: np.ones(len(obs), int))
        exp = s['close'][:, -1] / s['open'][:, 0] * (1 - SPOT.cost) ** 2 - 1
        np.testing.assert_allclose(r['daily_ret'], exp, rtol=1e-10)
        np.testing.assert_array_equal(r['turnover'], [2, 2, 2])

    def test_flat_earns_nothing(self):
        r = run_policy(PERP, sessions(), np.arange(3), lambda obs, j: np.full(len(obs), PERP.flat_action))
        np.testing.assert_array_equal(r['daily_ret'], 0)

    def test_perp_short_pays_funding_and_costs(self):
        s = sessions(); s['open'][:] = 100.0; s['close'][:] = 100.0
        r = run_policy(PERP, s, np.arange(3), lambda obs, j: np.zeros(len(obs), int))  # always short
        exp = (1 - PERP.cost) ** 2 * (1 - PERP.funding) ** 2 - 1
        np.testing.assert_allclose(r['daily_ret'], exp, rtol=1e-10)

    def test_decision_sees_only_previous_bar(self):
        s = sessions(1)
        s['obs'][0, :, 0] = np.arange(BARS)  # tag rows; row j must be presented at decision j
        seen = []
        run_policy(SPOT, s, np.arange(1), lambda obs, j: (seen.append(obs[0, 0]), np.zeros(1, int))[1])
        self.assertEqual(seen, list(range(BARS)))


if __name__ == '__main__':
    unittest.main()
