"""
Backtest of MultiHighRR_Setups.mq5 on Dukascopy XAUUSD 1-minute BID/ASK bars (2010 - Oct 2026).

Chart bars are built from M1 bid on broker server time (New York + 7 h). On each new chart bar,
while flat, the five setups are checked in the EA's order (TrendPullback, BOS_Retest, LiqSweep,
SessionBreakout, OB_FVG); the first one that fires is traded at the bar's open (buy at ASK,
sell at BID). SL/TP exits are simulated minute by minute on BID (longs) / ASK (shorts), SL first
when both fall in the same minute, and gaps through the stop fill at the open.

Two versions:
  as_written: what MT5 would actually do with the posted code
    - sell targets computed as entry - (entry - sl) * DefaultRR land ABOVE the entry, so MT5
      rejects those orders (invalid stops): those sells are skipped
    - ManageTrailing() is never reached while a position is open: no trailing
    - BOS_Retest never fires (close2 > swingHigh is impossible because the swing range includes
      bar 2)
  fixed: sell targets on the correct side, BOS swing range starting at bar 3, and trailing run on
    every new bar (after TrailActivateRR of the INITIAL risk, stop at current price - 1 ATR)

Indicators: ATR(14) as MT5's iATR (simple average of true range) on chart bars; EMA 20/50 on H1
closes, where index 0 is the forming H1 bar evaluated at the current price.

Costs: real Dukascopy spread on every fill plus COMMISSION $/oz round trip.

Usage: python3 multi_rr_backtest.py M1_ROOT
"""
import sys
import numpy as np
import pandas as pd
from numba import njit

import ha_backtest as hb
import m1_backtest as mb
import tick_backtest as tb

COMMISSION = 0.07
SETUPS = ["TrendPullback", "BOS_Retest", "LiqSweep", "SessionBreakout", "OB_FVG"]
PERIODS = {"2011-18": (2011, 2018), "2019-22": (2019, 2022), "2023-26": (2023, 2026)}


def rolling_max(a, n, start):
    """max of a[k-start-n+1 .. k-start] for each k (bars 'start' .. 'start+n-1' back)."""
    s = pd.Series(a).rolling(n).max().shift(start - 1).to_numpy()
    return s


def rolling_min(a, n, start):
    return pd.Series(a).rolling(n).min().shift(start - 1).to_numpy()


def ema(x, n):
    return pd.Series(x).ewm(span=n, adjust=False).mean().to_numpy()


def prepare(root):
    t, c = mb.load_m1(root)
    m = hb.server_minutes(t)
    years = pd.DatetimeIndex(t, tz="UTC").year.to_numpy()
    return dict(t=t, m=m, years=years, bo=c["open_b"], bh=c["high_b"], bl=c["low_b"], bc=c["close_b"],
                ao=c["open_a"], ah=c["high_a"], al=c["low_a"])


def chart(P, tf):
    m = P["m"]
    b = hb.resample(m, P["bo"], P["bh"], P["bl"], P["bc"], tf)
    day, mod = m // 1440, m % 1440
    bucket = day * (1440 // tf) + mod // tf
    first = np.r_[0, np.flatnonzero(np.diff(bucket)) + 1]          # first M1 index of each chart bar
    b["first"] = first
    o, h, l, cl = b["o"], b["h"], b["l"], b["c"]
    prev_c = np.r_[np.nan, cl[:-1]]
    tr = np.nanmax(np.vstack([h - l, np.abs(h - prev_c), np.abs(l - prev_c)]), axis=0)
    b["atr"] = pd.Series(tr).rolling(14).mean().to_numpy()
    # H1 EMAs: value of the last closed H1 bar, and of the forming H1 bar at a given price
    h1 = hb.resample(m, P["bo"], P["bh"], P["bl"], P["bc"], 60)
    for n, key in ((20, "f"), (50, "s")):
        e = ema(h1["c"], n)
        a = 2.0 / (n + 1)
        b[f"h1end_{key}"] = (h1["end"], e, a)
    return b


def signals(b, P, fixed):
    """For each closed chart bar k: direction, sl, tp, setup id (1..5) of the first setup that fires,
    to be entered at the open of bar k+1."""
    n = len(b["o"])
    o, h, l, c, atr = b["o"], b["h"], b["l"], b["c"], b["atr"]
    nxt = np.r_[b["first"][1:], len(P["m"])]          # first M1 of bar k+1
    valid = nxt < len(P["m"])
    nxt = np.clip(nxt, 0, len(P["m"]) - 1)
    ask0, bid0 = P["ao"][nxt], P["bo"][nxt]           # quotes at the decision time
    T = P["m"][nxt]                                   # decision time (server minutes)
    price = bid0
    # H1 EMAs at decision time: emaX[1] = last closed H1 bar, emaX[0] = forming H1 bar at `price`
    em = {}
    for key in ("f", "s"):
        end, e, a = b[f"h1end_{key}"]
        j = np.searchsorted(end, T, side="right") - 1
        e1 = np.where(j >= 0, e[np.clip(j, 0, None)], np.nan)
        e0 = a * price + (1 - a) * e1
        em[key] = (e0, e1)
    eF0, eF1 = em["f"]
    eS0, eS1 = em["s"]

    d = np.zeros(n, np.int64)
    sl = np.full(n, np.nan)
    tp = np.full(n, np.nan)
    sid = np.zeros(n, np.int64)
    done = np.zeros(n, bool)

    def take(mask, dirv, slv, tpv, k):
        mask = mask & ~done & valid & np.isfinite(slv) & np.isfinite(tpv)
        d[mask], sl[mask], tp[mask], sid[mask] = dirv, slv[mask], tpv[mask], k
        done[mask] = True

    rr = 5.0
    o1, h1_, l1, c1 = o, h, l, c
    o2, c2 = np.r_[np.nan, o[:-1]], np.r_[np.nan, c[:-1]]
    h2, l2 = np.r_[np.nan, h[:-1]], np.r_[np.nan, l[:-1]]

    def short_tp(entry, s, mult):
        # as written: entry - (entry - sl) * mult -> above the entry for a sell (rejected by MT5)
        return entry - (entry - s) * mult if not fixed else entry - (s - entry) * mult

    # 1 Trend pullback
    up = (eF0 > eS0) & (eF1 > eS1)
    dn = (eF0 < eS0) & (eF1 < eS1)
    s_l = l1 - atr * 0.3
    take(up & (l1 <= eF1 + atr * 0.3) & (c1 > eF1) & (c1 > o1), 1, s_l, ask0 + (ask0 - s_l) * rr, 1)
    s_s = h1_ + atr * 0.3
    take(dn & (h1_ >= eF1 - atr * 0.3) & (c1 < eF1) & (c1 < o1), -1, s_s, short_tp(bid0, s_s, rr), 1)

    # 2 BOS + retest (swing over bars 2..21 as written: can never fire; bars 3..22 when fixed)
    start = 3 if fixed else 2
    sw_h = rolling_max(h, 20, start)
    sw_l = rolling_min(l, 20, start)
    s_l = np.minimum(l1, sw_h) - atr * 0.2
    take((c2 > sw_h) & (c1 < sw_h) & (c1 > sw_h - atr * 0.5), 1, s_l, ask0 + (ask0 - s_l) * rr, 2)
    s_s = np.maximum(h1_, sw_l) + atr * 0.2
    take((c2 < sw_l) & (c1 > sw_l) & (c1 < sw_l + atr * 0.5), -1, s_s, short_tp(bid0, s_s, rr), 2)

    # 3 Liquidity sweep
    rh, rl = rolling_max(h, 12, 2), rolling_min(l, 12, 2)
    s_l = l1 - atr * 0.2
    take((l1 < rl - atr * 0.1) & (c1 > o1) & ((c1 - o1) > atr * 0.6), 1, s_l, ask0 + (ask0 - s_l) * rr, 3)
    s_s = h1_ + atr * 0.2
    take((h1_ > rh + atr * 0.1) & (c1 < o1) & ((o1 - c1) > atr * 0.6), -1, s_s, short_tp(bid0, s_s, rr), 3)

    # 4 Session (range) breakout, then the MinRR rule: if RR < 4, TP = 5R with the same formula
    gh, gl = rolling_max(h, 10, 2), rolling_min(l, 10, 2)
    size = gh - gl
    ok = size >= atr * 0.8
    s_l = gh - atr * 0.3
    t_l = ask0 + size * 1.5
    rr_l = np.abs(t_l - ask0) / np.abs(ask0 - s_l)
    t_l = np.where(rr_l < 4, ask0 + (ask0 - s_l) * rr, t_l)
    take(ok & (c1 > gh), 1, s_l, t_l, 4)
    s_s = gl + atr * 0.3
    t_s = bid0 - size * 1.5
    rr_s = np.abs(t_s - bid0) / np.abs(bid0 - s_s)
    t_s = np.where(rr_s < 4, short_tp(bid0, s_s, rr), t_s)
    take(ok & (c1 < gl), -1, s_s, t_s, 4)

    # 5 Order block / FVG
    b2, b1 = np.abs(c2 - o2), np.abs(c1 - o1)
    base = (b2 > atr * 0.9) & (b1 < atr * 0.4)
    s_l = l1 - atr * 0.2
    take(base & (c2 > o2) & (l1 <= c2) & (c1 > o1), 1, s_l, ask0 + (ask0 - s_l) * rr, 5)
    s_s = h1_ + atr * 0.2
    take(base & (c2 < o2) & (h1_ >= c2) & (c1 < o1), -1, s_s, short_tp(bid0, s_s, rr), 5)

    return dict(dir=d, sl=sl, tp=tp, sid=sid, entry_idx=nxt, T=T, spread=ask0 - bid0, atr=atr)


@njit(cache=True)
def simulate(cand, ei, dirs, sls, tps, bo, bh, bl, ao, ah, al, bar_start, atr_m, trail, act_rr, trail_atr,
             commission):
    n = len(bo)
    out_e = np.empty(len(cand), np.int64)
    out_x = np.empty(len(cand), np.int64)
    out_r = np.empty(len(cand), np.float64)
    out_k = np.empty(len(cand), np.int64)
    k = 0
    busy = -1
    for q in range(len(cand)):
        e = ei[q]
        if e <= busy:
            continue
        dr = dirs[q]
        entry = ao[e] if dr > 0 else bo[e]
        sl, tp = sls[q], tps[q]
        risk = (entry - sl) * dr
        # MT5 rejects stops on the wrong side of the market
        if not (risk > 0) or (tp - entry) * dr <= 0:
            continue
        px = 0.0
        x = e
        done = False
        while x < n:
            if trail and x > e and bar_start[x]:
                cur = bo[x] if dr > 0 else ao[x]
                if (cur - entry) * dr >= act_rr * risk and atr_m[x] > 0:
                    ns = cur - dr * atr_m[x] * trail_atr
                    if (ns - sl) * dr > 0 and (cur - ns) * dr > 0:
                        sl = ns
            if dr > 0:
                if x > e and bo[x] <= sl:
                    px = bo[x]; done = True
                elif bl[x] <= sl:
                    px = sl; done = True
                elif bh[x] >= tp:
                    px = tp; done = True
            else:
                if x > e and ao[x] >= sl:
                    px = ao[x]; done = True
                elif ah[x] >= sl:
                    px = sl; done = True
                elif al[x] <= tp:
                    px = tp; done = True
            if done:
                break
            x += 1
        if not done:
            break
        out_e[k], out_x[k], out_k[k] = e, x, q
        out_r[k] = ((px - entry) * dr - commission) / risk
        k += 1
        busy = x
    return out_e[:k], out_x[:k], out_r[:k], out_k[:k]


def run(P, b, S, fixed, setups=None, session=(7, 17), max_spread=0.50, sides="both"):
    sid, d = S["sid"], S["dir"]
    mask = (sid > 0) & np.isfinite(S["atr"])
    if setups is not None:
        mask &= np.isin(sid, setups)
    if sides == "long":
        mask &= d > 0
    elif sides == "short":
        mask &= d < 0
    if session is not None:
        hour = (S["T"] % 1440) // 60
        a, z = session
        mask &= (hour >= a) & (hour < z) if a < z else ((hour >= a) | (hour < z))
    if max_spread is not None:
        mask &= S["spread"] <= max_spread
    cand = np.flatnonzero(mask)
    # per-minute helpers for trailing: chart-bar starts and ATR of the last closed chart bar
    n = len(P["bo"])
    bar_start = np.zeros(n, np.bool_)
    bar_start[b["first"]] = True
    atr_m = np.zeros(n)
    prev_atr = np.r_[np.nan, b["atr"][:-1]]
    atr_m[b["first"]] = np.nan_to_num(prev_atr)
    ei, xi, R, q = simulate(cand, S["entry_idx"][cand], d[cand], S["sl"][cand], S["tp"][cand],
                            P["bo"], P["bh"], P["bl"], P["ao"], P["ah"], P["al"],
                            bar_start, atr_m, fixed, 2.0, 1.0, COMMISSION)
    return pd.DataFrame(dict(entry=ei, exit=xi, R=R, year=P["years"][ei], dir=d[cand][q],
                             setup=sid[cand][q]))


def summary(tr):
    tr = tr[tr.year >= 2011]
    R = tr.R.to_numpy()
    if len(R) == 0:
        return dict(n=0)
    gp, gl = R[R > 0].sum(), -R[R < 0].sum()
    row = dict(n=len(R), win=100 * (R > 0).mean(), avgR=R.mean(), PF=gp / gl if gl else np.nan)
    for name, (a, z) in PERIODS.items():
        r = tr.R[(tr.year >= a) & (tr.year <= z)]
        row[name] = r.mean() if len(r) else np.nan
    eq = np.cumprod(1 + 0.008 * R)
    row["growth"] = eq[-1]
    row["maxDD"] = (1 - eq / np.maximum.accumulate(np.r_[1, eq])[1:]).max()
    lo, hi = tb.bootstrap_ci(R)
    row["ci_lo"], row["ci_hi"] = lo, hi
    return row


def fmt(df):
    return df.to_string(index=False, float_format=lambda x: f"{x:+.3f}" if abs(x) < 10 else f"{x:.0f}")


def main(root):
    P = prepare(root)
    rows, setup_rows, cache = [], [], {}
    for tf in (5, 15, 30, 60):
        b = chart(P, tf)
        for fixed in (False, True):
            S = signals(b, P, fixed)
            cache[(tf, fixed)] = (b, S)
            name = "fixed" if fixed else "as_written"
            for sess_name, sess, spread in (("EA defaults (7-17 server, spread<=0.50)", (7, 17), 0.50),
                                            ("all hours, spread<=1.00", None, 1.00)):
                tr = run(P, b, S, fixed, session=sess, max_spread=spread)
                rows.append(dict(tf=f"M{tf}" if tf < 60 else "H1", version=name, filters=sess_name,
                                 longs=int((tr[tr.year >= 2011].dir > 0).sum()),
                                 shorts=int((tr[tr.year >= 2011].dir < 0).sum()), **summary(tr)))
            for k, setup in enumerate(SETUPS, 1):
                tr = run(P, b, S, fixed, setups=[k])
                setup_rows.append(dict(tf=f"M{tf}" if tf < 60 else "H1", version=name, setup=setup,
                                       **summary(tr)))
    df = pd.DataFrame(rows)
    print("=========== 1. Whole EA: as written vs bugs fixed (risk 0.8% per trade for growth / DD) ===========")
    print(fmt(df))
    sd = pd.DataFrame(setup_rows)
    print("\n=========== 2. Each setup alone (EA default filters) ===========")
    print(fmt(sd[["tf", "version", "setup", "n", "win", "PF", "avgR", "2011-18", "2019-22", "2023-26", "ci_lo", "ci_hi"]]))
    best = df[df.n >= 200].sort_values("avgR", ascending=False).iloc[0]
    print(f"\nBest whole-EA row: {best.tf} {best.version} [{best.filters}] avg R {best.avgR:+.3f}")
    tfm = {"M5": 5, "M15": 15, "M30": 30, "H1": 60}[best.tf]
    b, S = cache[(tfm, best.version == "fixed")]
    sess = (7, 17) if best.filters.startswith("EA") else None
    spread = 0.50 if best.filters.startswith("EA") else 1.00
    tr = run(P, b, S, best.version == "fixed", session=sess, max_spread=spread)
    tr = tr[tr.year >= 2011]
    print("\n=========== 3. Year by year for that row ===========")
    print(tr.groupby("year").R.agg(trades="size", avgR="mean", totR="sum").to_string(float_format=lambda x: f"{x:+.2f}"))
    df.to_csv("results/multi_rr_summary.csv", index=False)
    sd.to_csv("results/multi_rr_by_setup.csv", index=False)


if __name__ == "__main__":
    main(sys.argv[1])
