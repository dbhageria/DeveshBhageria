"""Stage 2: apply the pre-registered development-period rules and the neighbour-robustness check.

Rules (2010-2016): trades >= 200, profit factor >= 1.15, max drawdown <= 30%,
at least 4 of 7 years profitable. Robustness: among configs that differ by one step in one
parameter, the median profit factor must be >= 1.05 and >= 60% must be profitable.

Usage: python3 select_dev.py dev_results.csv candidates.csv
"""
import sys
import numpy as np
import pandas as pd

d = pd.read_csv(sys.argv[1])
d["key"] = d["family"] + ":" + d["combo"]
pf = dict(zip(d["key"], d["pf"]))


def neighbours(row):
    c = list(map(int, row["combo"].split("-")))
    out = []
    for j in range(len(c)):
        for step in (-1, 1):
            n = c.copy(); n[j] += step
            k = row["family"] + ":" + "-".join(map(str, n))
            if k in pf:
                out.append(pf[k])
    return out


rules = (d.trades >= 200) & (d.pf >= 1.15) & (d.max_dd_pct <= 30) & (d.years_pos >= 4)
c = d[rules].copy()
nb = c.apply(neighbours, axis=1)
c["nb_count"] = nb.apply(len)
c["nb_median_pf"] = nb.apply(lambda x: float(np.median(x)) if x else 0.0)
c["nb_share_profitable"] = nb.apply(lambda x: float(np.mean(np.array(x) > 1.0)) if x else 0.0)
c["robust"] = (c.nb_median_pf >= 1.05) & (c.nb_share_profitable >= 0.6)
print(f"{len(d)} configs tested; {int(rules.sum())} pass the dev rules; {int(c.robust.sum())} also robust")
c.sort_values("pf", ascending=False).to_csv(sys.argv[2], index=False)
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30)
cols = ["family", "combo", "trades", "pf", "avg_r", "win_rate", "ret_pct", "max_dd_pct", "years_pos",
        "long_avg_r", "short_avg_r", "nb_median_pf", "nb_share_profitable", "robust"]
print(c.sort_values("pf", ascending=False)[cols].head(40).to_string())
