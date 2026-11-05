"""Stage 5: deeper checks on the selected setup.

1. Year-by-year results and losing streaks (normal costs).
2. Luck test: random entries in the D1-trend direction, same trade count, same stop/trailing
   exits, 50 seeds. If the breakout entry is real, it should beat almost all of them.

Usage: python3 check_selected.py m15.pkl
"""
import sys
import numpy as np
import pandas as pd
import engine as E
import strategies as S

P = dict(n=192, filt="D1", side="both")
X = dict(stop_mult=3.0, rr=0.0, trail_mult=5.0, max_bars=0)
# run each continuous data block on its own (2025 trades counted from March, after warm-up)
parts = []
for seg_start, count_from in (("2010-01-01", "2010-01-01"), ("2025-01-01", "2025-03-01")):
    Dseg = E.load_segment(sys.argv[1], seg_start)
    t = E.run(Dseg, S.donchian_breakout(Dseg, **P), **X)
    keep = Dseg.idx[t[0]] >= pd.Timestamp(count_from)
    parts.append((Dseg.idx[t[0][keep]], t[1][keep] - t[0][keep], t[2][keep], t[3][keep]))
when = parts[0][0].append(parts[1][0])
held = np.concatenate([parts[0][1], parts[1][1]])
td = np.concatenate([parts[0][2], parts[1][2]])
tr = np.concatenate([parts[0][3], parts[1][3]])
D = E.load_segment(sys.argv[1], "2010-01-01")
sig = S.donchian_breakout(D, **P)
te, tx, td0, tr0 = E.run(D, sig, **X)

print("== Year by year (R per year, trades, profit factor)")
df = pd.DataFrame({"year": when.year, "r": tr, "dir": td, "bars": held})
yr = df.groupby("year").agg(trades=("r", "size"), total_R=("r", "sum"),
                            pf=("r", lambda r: r[r > 0].sum() / -r[r < 0].sum()),
                            long_R=("r", lambda r: r[df.loc[r.index, "dir"] == 1].sum()),
                            short_R=("r", lambda r: r[df.loc[r.index, "dir"] == -1].sum()))
print(yr.round(2).to_string())
streak = mx = 0
for r in tr:
    streak = streak + 1 if r < 0 else 0
    mx = max(mx, streak)
print(f"\nAll periods: {len(tr)} trades, avg {tr.mean():.3f}R, win rate {100*(tr>0).mean():.1f}%, "
      f"longest losing streak {mx}, median hold {np.median(df.bars)*15/60:.1f} hours, "
      f"avg win {tr[tr>0].mean():.2f}R, avg loss {tr[tr<0].mean():.2f}R")

print("\n== Luck test: random entries in D1-trend direction, same exits")
trend = D.htf_close_ema("1D", 50)
dev_val = (D.idx < pd.Timestamp("2020-01-01"))
real = E.metrics(D, (te, tx, td0, tr0), "2010-01-01", "2020-01-01")
n_sig = int((sig["long"] | sig["short"])[dev_val].sum())
p_entry = n_sig / dev_val.sum()
rand_avg = []
for seed in range(50):
    rng = np.random.default_rng(seed)
    pick = rng.random(len(D.o)) < p_entry
    rs = dict(long=pick & (trend > 0), short=pick & (trend < 0))
    m = E.metrics(D, E.run(D, rs, **X), "2010-01-01", "2020-01-01")
    rand_avg.append(m["avg_r"])
rand_avg = np.array(rand_avg)
print(f"Breakout entries 2010-2019: avg {real['avg_r']:.3f}R over {real['trades']} trades")
print(f"Random entries (50 seeds):  avg {rand_avg.mean():.3f}R, best {rand_avg.max():.3f}R, "
      f"worst {rand_avg.min():.3f}R; breakout beats {100*(rand_avg < real['avg_r']).mean():.0f}% of them")
