"""Extra check: does the HTF filter help when baskets last longer (wide grid, small size)?"""
import sys, json, numpy as np
import grid_basket_backtest as g
df = g.load(sys.argv[1])
trends = {m: g.htf_trend(df, *g.HTF[m]) for m in g.HTF}
zero = np.zeros(len(df))
P = dict(r=0.005, levels=5, step=10.0, tp=0.10, sl=0.30)
for mode in g.MODES:
    res = g.run(df, trends.get(mode, zero), mode, spread=0.30, start=10000, **P)
    res.update(profile="swing", mode=mode)
    print(json.dumps(res), flush=True)
