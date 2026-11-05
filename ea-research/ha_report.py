"""
Year-by-year results and 2026 trade lists for the Heiken Ashi Conviction settings of interest:
the specification defaults and the two candidates that held up best in ha_validate.py.

Usage: python3 ha_report.py M1_ROOT OUT_DIR
"""
import os
import sys
import numpy as np
import pandas as pd

import ha_backtest as hb

SETTINGS = {
    "spec_default": dict(vote_mode="strict", min_votes=5, side="both", exit_=("trail", 1, 1), session="all", lookback=5),
    "A_8of8_tp1.5_ldnny_stop24": dict(vote_mode="strict", min_votes=8, side="both", exit_=("tp", 1.5), session="ldn-ny", lookback=24),
    "B_6of8_tp3_ldnny_stop24": dict(vote_mode="strict", min_votes=6, side="both", exit_=("tp", 3), session="ldn-ny", lookback=24),
}


def run(P, s):
    return hb.run(P, s["vote_mode"], s["min_votes"], s["side"], s["exit_"], s["session"], lookback=s["lookback"])


def server_time(P, i):
    m = P["m"][i]
    return pd.Timestamp(int(m) * hb.MIN).strftime("%Y-%m-%d %H:%M")


def main(root, out_dir):
    P = hb.prepare(root)
    os.makedirs(out_dir, exist_ok=True)
    for name, s in SETTINGS.items():
        tr = run(P, s)
        tr = tr[tr.year >= 2011]
        print(f"\n=========== {name} ===========")
        g = tr.groupby("year").R.agg(trades="size", win=lambda r: f"{100*(r > 0).mean():.0f}%",
                                     avgR="mean", totR="sum",
                                     PF=lambda r: r[r > 0].sum() / -r[r < 0].sum() if (r < 0).any() else np.nan)
        print(g.to_string(float_format=lambda x: f"{x:+.3f}"))
        growth, dd = hb.equity(tr)
        print(f"2011-2026: {len(tr)} trades, avg R {tr.R.mean():+.3f}; equity with 0.5/1/2% risk tiers "
              f"x{growth:.2f}, max drawdown {100*dd:.1f}%")
        if name == "spec_default":
            continue
        t26 = tr[tr.year == 2026]
        rows = []
        for _, r in t26.iterrows():
            e, x, d = int(r.entry), int(r.exit), int(r["dir"])
            entry = P["ao"][e] if d > 0 else P["bo"][e]
            sl_long, sl_short = P["stops"][s["lookback"]]
            stop = sl_long[e - 1] if d > 0 else sl_short[e - 1]
            risk = (entry - stop) * d
            tp = entry + d * s["exit_"][1] * risk
            rows.append(dict(entry_server=server_time(P, e), side="BUY" if d > 0 else "SELL",
                             votes=int(r.votes), entry=round(entry, 2), stop=round(stop, 2), tp=round(tp, 2),
                             risk_usd_per_oz=round(risk, 2), exit_server=server_time(P, x),
                             R=round(r.R, 2), pnl_usd_per_oz=round(r.R * risk, 2)))
        df = pd.DataFrame(rows)
        path = os.path.join(out_dir, f"ha_trades_2026_{name}.csv")
        df.to_csv(path, index=False)
        print(f"2026 trade list: {path} ({len(df)} trades, total {df.R.sum():+.1f}R, "
              f"${df.pnl_usd_per_oz.sum():+.0f}/oz = $ per 0.01 lot)")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
