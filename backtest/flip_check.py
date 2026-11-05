"""Compare HTF-filtered baskets with and without closing the basket when the HTF trend flips.

Usage: python3 -I flip_check.py /path/to/XAUUSD_m1.csv > r_flip.jsonl
"""
import sys
import json
import grid_basket_backtest as g

RUNS = [  # (profile name, settings, start balance)
    ("video_like", g.PROFILES["video_like"], 1000.0),
    ("moderate", g.PROFILES["moderate"], 1000.0),
    ("conservative", g.PROFILES["conservative"], 1000.0),
    ("swing", dict(r=0.005, levels=5, step=10.0, tp=0.10, sl=0.30), 10000.0),
]

if __name__ == "__main__":
    df = g.load(sys.argv[1])
    trends = {m: g.htf_trend(df, *g.HTF[m]) for m in g.HTF}
    for pname, p, start in RUNS:
        for mode in g.HTF:
            for flip in (False, True):
                res = g.run(df, trends[mode], mode, spread=0.30, start=start,
                            flip_close=flip, **p)
                res.update(profile=pname, mode=mode, flip_close=flip, start=start)
                print(json.dumps(res), flush=True)
