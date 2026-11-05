"""
Backtest of the London -> NY continuation strategy on Dukascopy XAUUSD 1-minute BID and ASK bars,
2010 to Oct 2026 (mirror: github.com/kevingtlin/dukascopy_XAUUSD_1m_Data, files
xauusd/{bid,ask}/m1/xauusd_{bid,ask}_m1_YYYY_MM.csv, UTC epoch-ms timestamps).

Same rules and engine as tick_backtest.py, with each minute replayed as
open -> adverse extreme -> favourable extreme -> close:
  * long: enters at the ASK open of the first NY minute, exits are checked on BID bars
  * short: enters at the BID open, exits are checked on ASK bars
so Dukascopy's real per-minute spread is paid. The only approximation left is the order of high
and low inside one minute, which is resolved against the trade.

Section 0 checks this approximation against the tick-exact engine on the 2011-2018 overlap.

Usage: python3 m1_backtest.py M1_ROOT [TICKS_DIR]
       M1_ROOT is the folder holding bid/ and ask/ (the repo's xauusd/ folder)
"""
import glob
import os
import sys
import numpy as np
import pandas as pd

import tick_backtest as tb

REF_ADR = 96.8   # average daily range of the 2026 sample used in tick_backtest.py
RECENT = range(2019, 2027)


def read_side(root, side):
    files = sorted(glob.glob(os.path.join(root, side, "m1", f"xauusd_{side}_m1_*.csv")))
    df = pd.concat((pd.read_csv(f) for f in files), ignore_index=True)
    return df.drop_duplicates("timestamp").set_index("timestamp").sort_index()


def load_m1(root):
    bid, ask = read_side(root, "bid"), read_side(root, "ask")
    df = bid.join(ask, lsuffix="_b", rsuffix="_a", how="inner")
    # Older months fill closed-market minutes with flat copies of the last price: drop them
    flat = ((df.open_b == df.close_b) & (df.high_b == df.low_b) & (df.open_b == df.high_b) &
            (df.close_b == df.close_b.shift()) & (df.close_a == df.close_a.shift()))
    df = df[~flat]
    t = df.index.to_numpy(np.int64) * 1_000_000
    cols = {k: df[k].to_numpy(np.float64) for k in df.columns}
    return t, cols


def interleave(*arrays):
    return np.column_stack(arrays).ravel()


def build(root):
    t, c = load_m1(root)
    adr20 = tb.prior_adr(t, c["high_b"], c["low_b"])
    days = tb.build_days(t, c["open_b"], adr20, t, c["open_b"], c["open_a"], "m1", min_rows=400)
    for r in days:
        r["dir"] = 1 if r["l_move"] > 0 else -1
        s = slice(r["i"], r["j"])
        bo, bh, bl, bc = (c[k][s] for k in ("open_b", "high_b", "low_b", "close_b"))
        ao, ah, al, ac = (c[k][s] for k in ("open_a", "high_a", "low_a", "close_a"))
        if r["dir"] > 0:   # adverse first: bid low before bid high
            bid, ask = interleave(bo, bl, bh, bc), interleave(ao, al, ah, ac)
        else:              # adverse first: ask high before ask low
            bid, ask = interleave(bo, bh, bl, bc), interleave(ao, ah, al, ac)
        r["path"] = tb.DayPath(bid, ask, r["dir"])
        r["spread"] = ao[0] - bo[0]
    return days


def section(title):
    print(f"\n=========== {title} ===========")


def by_year_table(days, years, thr=30, sl=40, tp=60):
    t = tb.trades(days, thr, sl, tp)
    print(f"{'period':<12} {tb.HDR}")
    for y in years:
        print(f"{y:<12} {tb.fmt(tb.stats(t[t.year == y]))}")
    print(f"{f'{years[0]}-{years[-1]}':<12} {tb.fmt(tb.stats(t[t.year.isin(years)]))}")
    return t[t.year.isin(years)]


def equity(t, risk=0.005):
    eq, peak, mdd = 1.0, 1.0, 0.0
    for R in t["R"]:
        eq *= 1 + risk * R
        peak = max(peak, eq)
        mdd = max(mdd, 1 - eq / peak)
    return eq, mdd


def walk_forward(days, is_years, oos_years, label):
    is_days = [r for r in days if r["year"] in is_years]
    oos_days = [r for r in days if r["year"] in oos_years]
    grid = [(thr, sl, tp) for thr in (20, 30, 40, 50)
            for sl in (20, 30, 40, 60, tb.NO_LEVEL) for tp in (30, 45, 60, 80, 120, tb.NO_LEVEL)]
    res = []
    for p in grid:
        s = tb.stats(tb.trades(is_days, *p))
        if s["n"] >= 40:
            res.append((s["avgR"] * np.sqrt(s["n"]), p, s))
    res.sort(key=lambda x: -x[0])
    lab = lambda v: "none" if v >= tb.NO_LEVEL else f"{v:g}"
    name = lambda p: f"{p[0]}/{lab(p[1])}/{lab(p[2])}"
    print(f"[{label}] choose on {is_years[0]}-{is_years[-1]}, test on {oos_years[0]}-{oos_years[-1]}")
    print(f"  in-sample combos with positive expectancy: {sum(x[2]['avgR'] > 0 for x in res)}/{len(res)}")
    print(f"  {'set':<26} {tb.HDR}")
    for rank, (_, p, s) in enumerate(res[:3], 1):
        print(f"  {f'IS #{rank} {name(p)} (in)':<26} {tb.fmt(s)}")
        t = tb.trades(oos_days, *p)
        lo, hi = tb.bootstrap_ci(t["R"].to_numpy())
        print(f"  {'   -> out of sample':<26} {tb.fmt(tb.stats(t))}   CI {lo:+.3f}..{hi:+.3f}")
    t = tb.trades(oos_days, 30, 40, 60)
    lo, hi = tb.bootstrap_ci(t["R"].to_numpy())
    print(f"  {'default 30/40/60 (out)':<26} {tb.fmt(tb.stats(t))}   CI {lo:+.3f}..{hi:+.3f}")


def validate(m1_days, ticks_dir):
    tick_days = tb.paths_for([], tb.load_ticks(ticks_dir))
    by_date = {r["date"]: r for r in m1_days}
    pairs = [(r, by_date[r["date"]]) for r in tick_days if r["date"] in by_date]
    print(f"Overlapping days: {len(pairs)} (tick engine {len(tick_days)}, 1m engine "
          f"{sum(1 for r in m1_days if r['year'] <= 2018)} in 2011-2018)")
    for mode in ("price", "vol"):
        tb.set_scale([p[0] for p in pairs], mode, REF_ADR)
        tb.set_scale([p[1] for p in pairs], mode, REF_ADR)
        tk = tb.trades([p[0] for p in pairs], 30, 40, 60).set_index("date")
        m1 = tb.trades([p[1] for p in pairs], 30, 40, 60).set_index("date")
        both = tk.join(m1, lsuffix="_tick", rsuffix="_m1", how="inner")
        print(f"  [{mode}] trades tick {len(tk)}, 1m {len(m1)}, same day {len(both)}; "
              f"same exit {100*(both.exit_tick == both.exit_m1).mean():.0f}%; "
              f"R correlation {np.corrcoef(both.R_tick, both.R_m1)[0, 1]:.3f}")
        print(f"     tick  {tb.fmt(tb.stats(tk.reset_index()))}")
        print(f"     1m    {tb.fmt(tb.stats(m1.reset_index()))}")


def main(root, ticks_dir=None):
    days = build(root)
    years = sorted({r["year"] for r in days})
    print(f"1m bid/ask data: {len(days)} usable NY days, {days[0]['date']} -> {days[-1]['date']}")
    sp = pd.DataFrame([(r["year"], r["spread"]) for r in days], columns=["y", "s"]).groupby("y").s.median()
    print("Median spread at NY open by year: " + ", ".join(f"{y} ${v:.2f}" for y, v in sp.items()))

    if ticks_dir:
        section("0. Check: 1-minute engine vs tick-exact engine on the same days (2011-2018)")
        validate(days, ticks_dir)

    recent = [y for y in years if y in RECENT]
    for mode in ("price", "vol"):
        tb.set_scale(days, mode, REF_ADR)
        rec_days = [r for r in days if r["year"] in RECENT]
        print(f"\n\n################ SCALING BY {mode.upper()} ################")
        section("1. The forum claim, 2019-2026")
        tb.claim_table(rec_days, "1m bid/ask 2019-2026")
        section("2. Default EA settings (London > $30, SL $40, TP $60, 2026-$), by year")
        t = by_year_table(days, recent)
        eq, mdd = equity(t)
        print(f"Equity at 0.5% risk, 2019-2026: x{eq:.3f} ({100*(eq-1):+.1f}%), max drawdown {100*mdd:.1f}%")
        print("Exits: " + ", ".join(f"{k} {v}" for k, v in t["exit"].value_counts().items()))
        section("3. Does the London filter add anything? (SL 40 / TP 60, 2019-2026)")
        print(f"{'London >':<12} {tb.HDR}")
        for thr in [0, 10, 20, 30, 40, 50, 70]:
            print(f"{'$'+str(thr):<12} {tb.fmt(tb.stats(tb.trades(rec_days, thr, 40, 60)))}")
        section("4. Walk-forward")
        walk_forward(days, list(range(2011, 2019)), recent, "old -> recent")
        walk_forward(days, list(range(2019, 2023)), [y for y in recent if y >= 2023], "recent split")

    section("5. Literal fixed-dollar settings ($30 / $40 / $60, no scaling)")
    for r in days:
        r["scale"] = 1.0
    by_year_table(days, [y for y in years if y >= 2020])


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
