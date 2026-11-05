"""
Robustness checks for the Heiken Ashi Conviction settings search (ha_backtest.py):

  1. Walk-forward: rank all settings on 2011-2018 only, then show how the top ones did on 2019-2026.
  2. Bootstrap 95% CI of average R, and cost sensitivity (commission $0 and $0.14/oz).

Usage: python3 ha_validate.py M1_ROOT GRID_CSV
"""
import sys
import numpy as np
import pandas as pd

import ha_backtest as hb
import tick_backtest as tb


def parse_exit(s):
    kind, val = s.split(" ")
    if kind == "trail":
        a, d = val.split("/")
        return ("trail", float(a), float(d))
    return ("tp", float(val.rstrip("R")))


def run_row(P, r, **kw):
    return hb.run(P, r["votes"], int(r["min"]), r["side"], parse_exit(r["exit"]), r["session"],
                  lookback=int(r["stop_m5"]), **kw)


def describe(r):
    return f"{r['votes']} {int(r['min'])}/8 {r['side']} {r['exit']} {r['session']} stop {int(r['stop_m5'])}xM5"


def main(root, grid_csv):
    P = hb.prepare(root)
    df = pd.read_csv(grid_csv)
    print(f"{len(df)} settings in the grid\n")

    print("=========== 1. Walk-forward: ranked on 2011-2018 only (>= 100 trades), tested on 2019-2026 ===========")
    rows = []
    for _, r in df.iterrows():
        if r["n"] < 150:
            continue
        rows.append(r)
    ranked = pd.DataFrame(rows).sort_values("2011-18", ascending=False).head(20)
    out = []
    for _, r in ranked.iterrows():
        tr = run_row(P, r)
        a = tr[(tr.year >= 2011) & (tr.year <= 2018)].R
        b = tr[tr.year >= 2019].R
        if len(a) < 100:
            continue
        out.append(dict(setting=describe(r), n_in=len(a), R_in=a.mean(), n_out=len(b), R_out=b.mean(),
                        PF_out=b[b > 0].sum() / -b[b < 0].sum() if (b < 0).any() else np.nan))
    wf = pd.DataFrame(out).head(10)
    print(wf.to_string(index=False, float_format=lambda x: f"{x:+.3f}"))
    print(f"Top-10-in-sample settings positive out of sample: {(wf.R_out > 0).sum()} / {len(wf)}; "
          f"median out-of-sample avg R {wf.R_out.median():+.3f}\n")

    print("=========== 2. The consistent settings: bootstrap CI and cost sensitivity ===========")
    cons = df[df.consistent].sort_values("avgR", ascending=False)
    res = []
    for _, r in cons.iterrows():
        tr = run_row(P, r)
        R = tr[tr.year >= 2011].R.to_numpy()
        lo, hi = tb.bootstrap_ci(R)
        r0 = run_row(P, r, commission=0.0)
        r2 = run_row(P, r, commission=0.14)
        res.append(dict(setting=describe(r), n=len(R), avgR=R.mean(), ci_lo=lo, ci_hi=hi,
                        avgR_comm0=r0[r0.year >= 2011].R.mean(), avgR_comm014=r2[r2.year >= 2011].R.mean()))
    res = pd.DataFrame(res)
    print(res.to_string(index=False, float_format=lambda x: f"{x:+.3f}"))
    print("\nCosts: real Dukascopy spread plus commission per oz round trip ($0.07 default; $0 and $0.14 shown)."
          f"\nCaution: these were selected from {len(df)} settings, so their averages are biased upward.")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
