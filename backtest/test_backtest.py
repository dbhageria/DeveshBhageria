"""Small synthetic-bar tests for grid_basket_backtest.run().  Run: python3 -m unittest -v"""
import unittest
import numpy as np
import pandas as pd
import grid_basket_backtest as g

SPREAD = 0.30


def bars(rows, minutes=None):
    """rows: list of (open, high, low, close); minutes: offsets from start (default 0,1,2...)."""
    minutes = minutes or list(range(len(rows)))
    idx = pd.Timestamp("2025-01-06 10:00") + pd.to_timedelta(minutes, unit="m")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


def run(df, **kw):
    p = dict(r=0.1, levels=1, step=1.0, tp=0.1, sl=None, spread=SPREAD, start=1000.0)
    p.update(kw)
    return g.run(df, np.zeros(len(df)), "buy", **p)


class BacktestTests(unittest.TestCase):
    def test_take_profit_closes_basket_at_target(self):
        # 1 lot buy at 2000; TP of 10% ($100) is reached on the rise to 2002
        res = run(bars([(2000, 2000, 2000, 2000), (2000, 2002, 2000, 2002)]))
        self.assertEqual(res["baskets"], 1)
        self.assertEqual(res["basket_win_rate"], 100.0)
        self.assertAlmostEqual(res["live_account"]["balance"], 1100.0, places=2)

    def test_stop_out_blows_account(self):
        # 10 lots on $1,000: a ~$1 drop wipes the account
        res = run(bars([(2000, 2000, 2000, 2000), (2000, 2000, 1998, 1998)]), r=1.0)
        self.assertEqual(res["blown"], 1)

    def test_sl_already_breached_books_actual_loss(self):
        # spread cost alone ($30 on 1 lot) exceeds a 0.1% SL ($1): loss must be ~$30, not $1
        res = run(bars([(2000, 2000, 2000, 2000), (2000, 2000, 1999.99, 1999.99)]), sl=0.001)
        self.assertEqual(res["baskets"], 1)
        self.assertAlmostEqual(res["live_account"]["balance"], 970.0, places=2)

    def test_gap_fills_every_crossed_level(self):
        # 0.1-lot levels, $1 step; a $4.50 weekend-style gap crosses 4 levels
        df = bars([(2000, 2000, 2000, 2000), (1995.5, 1995.5, 1995.5, 1995.5)], minutes=[0, 60])
        res = run(df, r=0.01, levels=6, tp=10.0)
        half = SPREAD / 2
        lots = [0.1] + [0.1] * 4
        entries = [2000 + half] + [1995.5 + half] * 4
        avg = sum(l * e for l, e in zip(lots, entries)) / sum(lots)
        expected = 1000 + (1995.5 - half - avg) * sum(lots) * 100
        self.assertAlmostEqual(res["live_account"]["balance"], round(expected, 2), places=2)

    def test_htf_flip_closes_basket_and_reverses(self):
        # buy basket opens at 2000; HTF turns down on bar 2 -> close buy at 1999.5, open sell
        df = bars([(2000, 2000, 2000, 2000), (2000, 2000, 1999.5, 1999.5),
                   (1999.5, 1999.5, 1999.5, 1999.5)])
        trend = np.array([1.0, 1.0, -1.0])
        p = dict(r=0.1, levels=1, step=1.0, tp=0.1, spread=SPREAD, start=1000.0)
        res = g.run(df, trend, "htf_H1", flip_close=True, **p)
        self.assertEqual(res["flip_closes"], 1)
        # buy: 1 lot 2000.15 -> 1999.35 = -$80 -> $920; new sell is 0.92 lots, pays $27.60 spread
        self.assertAlmostEqual(res["live_account"]["balance"], 892.4, places=2)
        res_off = g.run(df, trend, "htf_H1", flip_close=False, **p)
        self.assertEqual(res_off["flip_closes"], 0)


if __name__ == "__main__":
    unittest.main()
