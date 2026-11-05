"""Synthetic-bar tests for the search engine.  Run: python3 -m unittest -v test_engine"""
import unittest
import numpy as np
import pandas as pd
import engine as E

NO_COSTS = dict(min_spread=0.0, spread_bp=0.0, swap_long_bp=0.0, swap_short_bp=0.0)


def data(rows, start="2015-01-05 10:00"):
    idx = pd.date_range(start, periods=len(rows), freq="15min")
    return E.Data(pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx))


def sig_at(n, i, key="long"):
    a = np.zeros(n, bool); a[i] = True
    return {key: a}


class EngineTests(unittest.TestCase):
    def setUp(self):
        # 20 flat bars (ATR -> 1.0), then the test bars
        self.flat = [(100, 100.5, 99.5, 100)] * 20

    def test_target_hit_gives_rr(self):
        D = data(self.flat + [(100, 103.5, 99.9, 103), (103, 103, 103, 103)])
        te, tx, td, tr = E.run(D, sig_at(len(D.o), 19), stop_mult=1.0, rr=2.0, costs=NO_COSTS)
        self.assertEqual(len(tr), 1)
        self.assertAlmostEqual(tr[0], 2.0, places=6)

    def test_stop_first_when_both_hit_in_one_bar(self):
        D = data(self.flat + [(100, 103.5, 98.0, 103), (103, 103, 103, 103)])
        te, tx, td, tr = E.run(D, sig_at(len(D.o), 19), stop_mult=1.0, rr=2.0, costs=NO_COSTS)
        self.assertAlmostEqual(tr[0], -1.0, places=6)

    def test_gap_through_stop_fills_at_open(self):
        D = data(self.flat + [(100, 100.2, 99.8, 100), (97, 97, 96, 96)])
        te, tx, td, tr = E.run(D, sig_at(len(D.o), 19), stop_mult=1.0, costs=NO_COSTS)
        self.assertAlmostEqual(tr[0], -3.0, places=6)

    def test_spread_cost_in_r(self):
        D = data(self.flat + [(100, 103.5, 99.9, 103), (103, 103, 103, 103)])
        te, tx, td, tr = E.run(D, sig_at(len(D.o), 19), stop_mult=1.0, rr=2.0,
                               costs=dict(NO_COSTS, min_spread=0.5))
        self.assertAlmostEqual(tr[0], 1.5, places=6)

    def test_exit_signal_and_reversal(self):
        rows = self.flat + [(100, 100.2, 99.8, 100.1), (100.1, 100.3, 99.9, 100.2),
                            (100.2, 100.3, 100.0, 100.2)]
        D = data(rows)
        n = len(rows)
        sig = {"long": np.zeros(n, bool), "short": np.zeros(n, bool), "exit_long": np.zeros(n, bool)}
        sig["long"][19] = True
        sig["exit_long"][20] = True
        sig["short"][20] = True
        te, tx, td, tr = E.run(D, sig, stop_mult=1.0, costs=NO_COSTS)
        self.assertEqual(list(td), [1, -1])
        self.assertAlmostEqual(tr[0], 0.1, places=6)  # long 100 -> exit at 100.1 open

    def test_long_swap_charged_per_night(self):
        idx = list(pd.date_range("2015-01-05 22:30", periods=22, freq="15min"))
        rows = [(100, 100.5, 99.5, 100)] * 22
        D = E.Data(pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=pd.DatetimeIndex(idx)))
        te, tx, td, tr = E.run(D, sig_at(22, 1), stop_mult=1.0,
                               costs=dict(NO_COSTS, swap_long_bp=0.001))
        # one rollover (Mon -> Tue) on a $100 price: $0.10 swap over a $1 stop -> -0.1R
        self.assertAlmostEqual(tr[0], -0.1, places=6)


if __name__ == "__main__":
    unittest.main()
