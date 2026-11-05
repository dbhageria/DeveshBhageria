"""Stages 3-4: evaluate given candidate configs on a later period (validation or hold-out).

Usage: python3 evaluate.py m15.pkl candidates.csv START END out.csv [costs]
  For the 2025-2026 hold-out use START 2025-03-01: indicators warm up on Jan-Feb 2025.
  costs: "normal" (default), "stress" (spread x1.5, swaps x1.5) or "swapfree"
"""
import sys
import pandas as pd
import engine as E
import strategies as S

path, cand, start, end, out = sys.argv[1:6]
mode = sys.argv[6] if len(sys.argv) > 6 else "normal"
costs = dict(E.DEFAULT_COSTS)
if mode == "stress":
    costs = {k: v * 1.5 for k, v in costs.items()}
elif mode == "swapfree":
    costs.update(swap_long_bp=0.0, swap_short_bp=0.0)

D = E.load_segment(path, start)
c = pd.read_csv(cand)
rows = []
for _, r in c.iterrows():
    fn, sp, ep = S.FAMILIES[r["family"]]
    sp_args = {k: (type(v[0])(r[k]) if not isinstance(v[0], str) else r[k]) for k, v in sp.items()}
    ep_args = {k: type(v[0])(r[k]) for k, v in ep.items()}
    trades = E.run(D, fn(D, **sp_args), costs=costs, **ep_args)
    m = E.metrics(D, trades, start, end)
    rows.append(dict(family=r["family"], combo=r["combo"], dev_pf=r["pf"], **sp_args, **ep_args, **m))
res = pd.DataFrame(rows)
res.to_csv(out, index=False)
pd.set_option("display.width", 250)
print(res[["family", "combo", "dev_pf", "trades", "pf", "avg_r", "win_rate", "ret_pct", "max_dd_pct",
           "years_pos", "years", "long_avg_r", "short_avg_r"]].to_string())
