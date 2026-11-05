"""
Settings search for the London -> NY strategy on Dukascopy 1-minute bid/ask (2010 - Oct 2026).

Grid: direction (both / long only / short only) x London threshold x SL x TP, under both price and
volatility scaling. A setting counts as "consistently profitable" only if its average R is positive
in EACH of three separate periods: 2011-2018, 2019-2022, 2023-2026.

Null check: the same search is repeated with every trade reversed (fading London instead of
following it). Neither version has a reason to work, so the number of fade settings that pass
shows how many passes luck alone produces in a grid this size.

Usage: python3 settings_search.py M1_ROOT
"""
import sys
import numpy as np
import pandas as pd

import m1_backtest as mb
import tick_backtest as tb

PERIODS = {"2011-18": range(2011, 2019), "2019-22": range(2019, 2023), "2023-26": range(2023, 2027)}
NO = tb.NO_LEVEL
GRID = [(side, thr, sl, tp)
        for side in ("both", "long", "short")
        for thr in (20, 30, 40, 50, 70)
        for sl in (20, 30, 40, 60, NO)
        for tp in (30, 45, 60, 80, 120, NO)]
MIN_TRADES = 15  # per period


def build_both(root):
    """Days with a path that follows London and one that fades it."""
    t, c = mb.load_m1(root)
    adr20 = tb.prior_adr(t, c["high_b"], c["low_b"])
    days = tb.build_days(t, c["open_b"], adr20, t, c["open_b"], c["open_a"], "m1", min_rows=400)
    for r in days:
        r["dir"] = 1 if r["l_move"] > 0 else -1
        s = slice(r["i"], r["j"])
        b = [c[k][s] for k in ("open_b", "high_b", "low_b", "close_b")]
        a = [c[k][s] for k in ("open_a", "high_a", "low_a", "close_a")]
        for key, d in (("follow", r["dir"]), ("fade", -r["dir"])):
            if d > 0:
                bid, ask = mb.interleave(b[0], b[2], b[1], b[3]), mb.interleave(a[0], a[2], a[1], a[3])
            else:
                bid, ask = mb.interleave(b[0], b[1], b[2], b[3]), mb.interleave(a[0], a[1], a[2], a[3])
            r[key] = tb.DayPath(bid, ask, d)
    return days


def trade_R(days, side, thr, sl, tp, key="follow"):
    """R per trade by year for one setting. side refers to the direction actually traded."""
    out = []
    for r in days:
        if abs(r["l_move"]) <= thr * r["scale"]:
            continue
        d = r["dir"] if key == "follow" else -r["dir"]
        if (side == "long" and d < 0) or (side == "short" and d > 0):
            continue
        s = r["scale"]
        pnl, _ = r[key].run(sl * s if sl < NO else NO, tp * s if tp < NO else NO)
        out.append((r["year"], pnl / s / (sl if sl < NO else 40.0)))
    return pd.DataFrame(out, columns=["year", "R"])


def summarise(t):
    row = {}
    for name, yrs in PERIODS.items():
        R = t.loc[t.year.isin(yrs), "R"].to_numpy()
        row[f"n {name}"] = len(R)
        row[f"R {name}"] = R.mean() if len(R) else np.nan
    allR = t.loc[t.year.isin(range(2011, 2027)), "R"].to_numpy()
    gp, gl = allR[allR > 0].sum(), -allR[allR < 0].sum()
    row.update(n=len(allR), avgR=allR.mean() if len(allR) else np.nan, PF=gp / gl if gl else np.nan,
               totR=allR.sum())
    lo, hi = tb.bootstrap_ci(allR)
    row.update(ci_lo=lo, ci_hi=hi)
    return row


def search(days, key):
    rows = []
    for side, thr, sl, tp in GRID:
        row = dict(side=side, thr=thr, sl=sl, tp=tp, **summarise(trade_R(days, side, thr, sl, tp, key)))
        enough = all(row[f"n {p}"] >= MIN_TRADES for p in PERIODS)
        row["consistent"] = enough and all(row[f"R {p}"] > 0 for p in PERIODS)
        rows.append(row)
    return pd.DataFrame(rows)


def lab(v):
    return "none" if v >= NO else f"{v:g}"


def show(df, title, k=12):
    print(title)
    if df.empty:
        print("  (none)")
        return
    cols = ["side", "thr", "sl", "tp", "n", "PF", "avgR", "ci_lo", "ci_hi"] + [f"R {p}" for p in PERIODS] \
        + [f"n {p}" for p in PERIODS]
    d = df[cols].copy()
    d["sl"], d["tp"] = d.sl.map(lab), d.tp.map(lab)
    print(d.head(k).to_string(index=False, float_format=lambda x: f"{x:+.3f}" if abs(x) < 10 else f"{x:.0f}"))


def main(root):
    days = build_both(root)
    print(f"{len(days)} NY sessions, {days[0]['date']} -> {days[-1]['date']}; grid of {len(GRID)} settings, "
          f">= {MIN_TRADES} trades per period required\n")
    results = {}
    for mode in ("price", "vol"):
        tb.set_scale(days, mode, mb.REF_ADR)
        follow, fade = search(days, "follow"), search(days, "fade")
        results[mode] = follow
        print(f"################ SCALING BY {mode.upper()} ################")
        print(f"Settings positive in all three periods: follow London {follow.consistent.sum()}, "
              f"fade London (luck baseline) {fade.consistent.sum()}")
        for side in ("both", "long", "short"):
            f = follow[follow.side == side]
            print(f"  {side:<5}: {f.consistent.sum():>2} of {len(f)} consistent; "
                  f"positive over 2011-2026: {(f.avgR > 0).sum()} of {len(f)}")
        show(follow[follow.consistent].sort_values("avgR", ascending=False),
             "\nConsistent settings, following London (best average R first):")
        show(fade[fade.consistent].sort_values("avgR", ascending=False),
             "\nConsistent settings, fading London (luck baseline):", k=5)
        print()

    both = results["price"].merge(results["vol"], on=["side", "thr", "sl", "tp"], suffixes=("_p", "_v"))
    robust = both[both.consistent_p & both.consistent_v].copy()
    robust["minR"] = robust[[f"R {p}_p" for p in PERIODS] + [f"R {p}_v" for p in PERIODS]].min(axis=1)
    robust = robust.sort_values("minR", ascending=False)
    print("################ CONSISTENT UNDER BOTH SCALINGS ################")
    if robust.empty:
        print("(none)")
    else:
        cols = ["side", "thr", "sl", "tp", "n_p", "PF_p", "avgR_p", "ci_lo_p", "n_v", "PF_v", "avgR_v",
                "ci_lo_v", "minR"]
        d = robust[cols].copy()
        d["sl"], d["tp"] = d.sl.map(lab), d.tp.map(lab)
        print(d.to_string(index=False, float_format=lambda x: f"{x:+.3f}" if abs(x) < 10 else f"{x:.0f}"))


if __name__ == "__main__":
    main(sys.argv[1])
