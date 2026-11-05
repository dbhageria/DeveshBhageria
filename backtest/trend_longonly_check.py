"""Long-only HTF filter (buy when the HTF trend is up, otherwise stay out), full period and
each half separately, with and without swaps.  Usage: python3 -I trend_longonly_check.py XAUUSD_m1.csv"""
import sys, json
import numpy as np
import grid_basket_backtest as g
import trend_backtest as tb

df_all = g.load(sys.argv[1])
periods = {"full": (None, None), "2025-01..2025-08": (None, "2025-08-31"),
           "2025-09..2026-05": ("2025-09-01", None)}
for pname, (a, b) in periods.items():
    df = df_all.loc[a:b]
    for mode in ("htf_H1", "htf_H4", "buy"):
        tr = g.htf_trend(df, *g.HTF[mode]) if mode != "buy" else np.ones(len(df))
        long_only = np.where(tr > 0, 1.0, 0.0)
        for sl in (10.0, 20.0):
            for rr in (2.0, 3.0):
                for swaps in (False, True):
                    res = tb.run(df, long_only, "htf", sl, rr, swaps=swaps)
                    res.update(period=pname, mode=("long_only_" + mode) if mode != "buy" else "buy",
                               sl=sl, rr=rr, swaps=swaps,
                               gold_move_pct=round(100 * (df["close"].iloc[-1] / df["open"].iloc[0] - 1), 1))
                    print(json.dumps(res), flush=True)
