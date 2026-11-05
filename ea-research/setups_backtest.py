"""
Mechanical versions of four discretionary high-R:R gold setups, backtested on Dukascopy XAUUSD
1-minute BID/ASK bars (2010 - Oct 2026). Entry-timeframe bars (M15 or M5) are built from M1 bid.
Fills and exits are simulated minute by minute: longs fill at the ASK and exit on the BID, shorts
the reverse; SL is assumed first when SL and TP fall in the same minute; gaps through the stop
fill at the open. Cost = real spread + COMMISSION $/oz round trip. One trade at a time per setup.

Swings are fractal pivots (high/low of the K bars either side), usable only once confirmed K bars
later, so there is no look-ahead. ATR is the 14-bar simple average true range of the entry bars.
"London/NY" = 07:00-20:00 UTC.

1 PULLBACK  Trend pullback into value
  HTF (H1 or H4) last closed bar: close > EMA50 > EMA200 and EMA50 rising (vs 5 HTF bars earlier).
  Impulse = last confirmed swing low -> following confirmed swing high, size >= 2 ATR.
  Setup lives until price breaks the impulse low or a new swing high forms.
  Trigger: the bar's low reaches the 50-61.8% retracement zone (low <= 50% level, pullback low still
  above the 100% level), and the bar is a bullish engulfing or a bullish pin bar (lower wick >= 2x
  body, close in the top third). Entry at the next bar's open.
  Stop: pullback low - buffer (0.1 ATR or 1 ATR). Target: impulse high ("swing") or pullback low +
  impulse size ("measured"). Trades below MIN_RR are skipped. Shorts mirror everything.
2 BOS       Break of structure + retest
  BOS: a close beyond the last confirmed swing high (low) during London/NY. Within 24 bars: a bar
  whose low touches the level (low <= level + 0.2 ATR), closes above it and is bullish = rejection;
  a close more than 0.3 ATR back through the level cancels. Entry next bar open. Stop: retest-bar
  low - 0.1 ATR. Target: highest high of the prior 200 bars if above the entry ("liquidity", else the
  measured move of the broken range), or a fixed 5R.
3 SWEEP     Liquidity sweep + displacement + FVG pullback
  Levels: Asian range (00-07 UTC) and the previous UTC day's high/low. During London/NY, the first
  trade through a level is a sweep; within the next 3 bars a displacement candle must close back
  beyond the level with a body >= 1 ATR and >= 60% of its range, leaving a fair value gap
  (bar before displacement vs bar after). Entry: limit at the FVG's near edge within 12 bars.
  Stop: sweep extreme + 0.1 ATR. Target: the opposite Asian level / previous-day level ("liquidity",
  first one beyond the entry) or a fixed 5R.
4 BREAKOUT  Session breakout with measured move
  Range: Asian range 00-07 UTC, or London's first hour 07-08 UTC. First entry-bar close outside the
  range before 12:00 UTC; entry next bar open. Stop: range midpoint or the opposite side.
  Target: breakout side + 1x or 2x the range height. One trade per day.

Usage: python3 setups_backtest.py M1_ROOT
"""
import sys
import numpy as np
import pandas as pd
from numba import njit

import ha_backtest as hb
import multi_rr_backtest as mrb
import tick_backtest as tb

COMMISSION = 0.07
K = 3
PERIODS = {"2011-18": (2011, 2018), "2019-22": (2019, 2022), "2023-26": (2023, 2026)}
HOUR_NS = 3_600_000_000_000
DAY_NS = 24 * HOUR_NS


# ---------------------------------------------------------------- bars and helpers

def bars(P, tf):
    b = mrb.chart(P, tf)
    t0 = P["t"][b["first"]]
    b["utc_hour"] = ((t0 // HOUR_NS) % 24).astype(np.int64)
    b["utc_day"] = (t0 // DAY_NS).astype(np.int64)
    n = len(b["o"])
    # confirmed pivots: pivot at i is known at i + K
    h, l = b["h"], b["l"]
    win = 2 * K + 1
    hmax = pd.Series(h).rolling(win, center=True).max().to_numpy()
    lmin = pd.Series(l).rolling(win, center=True).min().to_numpy()
    b["piv_h"] = (h == hmax)
    b["piv_l"] = (l == lmin)
    return b


def htf_trend(P, b, tf_htf):
    """+1 / -1 / 0 trend from the last closed HTF bar at each entry bar's close."""
    m = P["m"]
    H = hb.resample(m, P["bo"], P["bh"], P["bl"], P["bc"], tf_htf)
    c = H["c"]
    e50 = pd.Series(c).ewm(span=50, adjust=False).mean().to_numpy()
    e200 = pd.Series(c).ewm(span=200, adjust=False).mean().to_numpy()
    rising = e50 > np.r_[np.full(5, np.nan), e50[:-5]]
    falling = e50 < np.r_[np.full(5, np.nan), e50[:-5]]
    up = (c > e50) & (e50 > e200) & rising
    dn = (c < e50) & (e50 < e200) & falling
    tr = np.where(up, 1, np.where(dn, -1, 0))
    close_time = b["end"]                       # entry bar close (server minutes)
    j = np.searchsorted(H["end"], close_time, side="right") - 1
    return np.where(j >= 200, tr[np.clip(j, 0, None)], 0)


def is_engulf_or_pin(o, h, l, c, k, d):
    body = abs(c[k] - o[k])
    rng = h[k] - l[k]
    if rng <= 0:
        return False
    if d > 0:
        engulf = c[k] > o[k] and o[k - 1] > c[k - 1] and c[k] >= o[k - 1] and o[k] <= c[k - 1]
        lower = min(o[k], c[k]) - l[k]
        pin = lower >= 2 * body and c[k] >= l[k] + rng * 2 / 3
    else:
        engulf = c[k] < o[k] and o[k - 1] < c[k - 1] and c[k] <= o[k - 1] and o[k] >= c[k - 1]
        upper = h[k] - max(o[k], c[k])
        pin = upper >= 2 * body and c[k] <= h[k] - rng * 2 / 3
    return engulf or pin


# ---------------------------------------------------------------- setups -> orders
# Each order: (entry M1 index, limit price or nan for market, direction, stop, target)

def setup_pullback(P, b, trend, buffer_atr, target, min_rr):
    o, h, l, c, atr = b["o"], b["h"], b["l"], b["c"], b["atr"]
    n = len(o)
    orders = []
    for d in (1, -1):
        last_piv_ext = None          # last confirmed swing low (for longs) / high (for shorts)
        imp = None                   # (start price, end price, end index)
        pb_ext = None
        for j in range(K, n - 1):
            i = j - K                # pivot confirmed now
            if d > 0:
                if b["piv_l"][i]:
                    last_piv_ext = (l[i], i)
                if b["piv_h"][i] and last_piv_ext is not None and last_piv_ext[1] < i:
                    size = h[i] - last_piv_ext[0]
                    if np.isfinite(atr[i]) and size >= 2 * atr[i]:
                        imp = (last_piv_ext[0], h[i], i)
                        pb_ext = l[i + 1:j + 1].min() if j > i else h[i]
                    else:
                        imp = None
            else:
                if b["piv_h"][i]:
                    last_piv_ext = (h[i], i)
                if b["piv_l"][i] and last_piv_ext is not None and last_piv_ext[1] < i:
                    size = last_piv_ext[0] - l[i]
                    if np.isfinite(atr[i]) and size >= 2 * atr[i]:
                        imp = (last_piv_ext[0], l[i], i)
                        pb_ext = h[i + 1:j + 1].max() if j > i else l[i]
                    else:
                        imp = None
            if imp is None:
                continue
            start, end, ie = imp
            size = abs(end - start)
            if d > 0:
                pb_ext = min(pb_ext, l[j])
                if pb_ext <= start:                       # impulse origin broken
                    imp = None
                    continue
                in_zone = l[j] <= end - 0.5 * size and pb_ext >= end - size
            else:
                pb_ext = max(pb_ext, h[j])
                if pb_ext >= start:
                    imp = None
                    continue
                in_zone = h[j] >= end + 0.5 * size and pb_ext <= end + size
            if not in_zone or trend[j] != d or not is_engulf_or_pin(o, h, l, c, j, d):
                continue
            stop = pb_ext - d * buffer_atr * atr[j]
            tgt = end if target == "swing" else pb_ext + d * size
            orders.append((b["first"][j + 1], np.nan, d, stop, tgt, min_rr))
            imp = None                                    # one entry per impulse
    return orders


def setup_bos(P, b, target):
    o, h, l, c, atr = b["o"], b["h"], b["l"], b["c"], b["atr"]
    n = len(o)
    hh200 = pd.Series(h).rolling(200).max().to_numpy()
    ll200 = pd.Series(l).rolling(200).min().to_numpy()
    orders = []
    for d in (1, -1):
        level = None
        watch = None
        for j in range(K, n - 1):
            i = j - K
            if d > 0 and b["piv_h"][i]:
                level = (h[i], i)
            if d < 0 and b["piv_l"][i]:
                level = (l[i], i)
            if watch is not None:
                lv, jb, rng_ext = watch
                if j - jb > 24 or (d > 0 and c[j] < lv - 0.3 * atr[j]) or (d < 0 and c[j] > lv + 0.3 * atr[j]):
                    watch = None
                elif j > jb:
                    touch = (l[j] <= lv + 0.2 * atr[j] and c[j] > lv and c[j] > o[j]) if d > 0 else \
                            (h[j] >= lv - 0.2 * atr[j] and c[j] < lv and c[j] < o[j])
                    if touch and 7 <= b["utc_hour"][j] < 20:
                        stop = l[j] - 0.1 * atr[j] if d > 0 else h[j] + 0.1 * atr[j]
                        if target == "liquidity":
                            liq = hh200[jb] if d > 0 else ll200[jb]
                            mm = lv + d * abs(lv - rng_ext)
                            tgt = liq if (liq - c[j]) * d > 0 else mm
                        else:
                            tgt = np.nan                    # fixed R, set at fill
                        orders.append((b["first"][j + 1], np.nan, d, stop, tgt, 0.0))
                        watch = None
                continue
            if level is None or not np.isfinite(atr[j]) or not (7 <= b["utc_hour"][j] < 20):
                continue
            lv, il = level
            if (d > 0 and c[j] > lv and c[j - 1] <= lv) or (d < 0 and c[j] < lv and c[j - 1] >= lv):
                rng_ext = l[il:j + 1].min() if d > 0 else h[il:j + 1].max()
                watch = (lv, j, rng_ext)
                level = None
    return orders


def day_levels(b):
    """Per entry bar: Asian high/low of the same UTC day (once 07:00 is reached) and previous-day H/L."""
    df = pd.DataFrame(dict(day=b["utc_day"], hour=b["utc_hour"], h=b["h"], l=b["l"]))
    asia = df[df.hour < 7].groupby("day").agg(ah=("h", "max"), al=("l", "min"))
    full = df.groupby("day").agg(dh=("h", "max"), dl=("l", "min"))
    prev = full.shift(1)
    prev.index = full.index
    out = df[["day"]].join(asia, on="day").join(prev, on="day")
    return out.ah.to_numpy(), out.al.to_numpy(), out.dh.to_numpy(), out.dl.to_numpy()


def setup_sweep(P, b, target):
    o, h, l, c, atr = b["o"], b["h"], b["l"], b["c"], b["atr"]
    n = len(o)
    ah, al, dh, dl = day_levels(b)
    orders = []
    swept = set()
    for j in range(1, n - 5):
        if not (7 <= b["utc_hour"][j] < 20) or not np.isfinite(atr[j]):
            continue
        day = b["utc_day"][j]
        for name, lv, d in (("ah", ah[j], -1), ("dh", dh[j], -1), ("al", al[j], 1), ("dl", dl[j], 1)):
            if not np.isfinite(lv) or (day, name) in swept:
                continue
            hit = h[j] > lv if d < 0 else l[j] < lv
            if not hit:
                continue
            swept.add((day, name))
            for k in range(j, min(j + 4, n - 2)):
                body, rng = abs(c[k] - o[k]), h[k] - l[k]
                disp = rng > 0 and body >= atr[j] and body >= 0.6 * rng and \
                       ((d < 0 and c[k] < o[k] and c[k] < lv) or (d > 0 and c[k] > o[k] and c[k] > lv))
                if not disp:
                    continue
                if d < 0 and l[k - 1] > h[k + 1]:
                    entry = h[k + 1]                            # FVG lower edge, sell limit
                elif d > 0 and h[k - 1] < l[k + 1]:
                    entry = l[k + 1]                            # FVG upper edge, buy limit
                else:
                    break
                ext = h[j:k + 1].max() if d < 0 else l[j:k + 1].min()
                stop = ext + 0.1 * atr[j] if d < 0 else ext - 0.1 * atr[j]
                if target == "liquidity":
                    cands = [al[j], dl[j]] if d < 0 else [ah[j], dh[j]]
                    cands = [x for x in cands if np.isfinite(x) and (x - entry) * d > 0]
                    tgt = (max(cands) if d < 0 else min(cands)) if cands else np.nan
                    if not np.isfinite(tgt):
                        break
                else:
                    tgt = np.nan
                # limit order valid from bar k+2 for 12 bars
                a = b["first"][k + 2] if k + 2 < n else None
                z = b["first"][min(k + 14, n - 1)]
                if a is not None:
                    orders.append((a, entry, d, stop, tgt, 0.0, z))
                break
    return orders


def setup_breakout(P, b, rng_kind, stop_kind, mult):
    o, h, l, c = b["o"], b["h"], b["l"], b["c"]
    df = pd.DataFrame(dict(day=b["utc_day"], hour=b["utc_hour"], h=h, l=l))
    a0, a1 = (0, 7) if rng_kind == "asia" else (7, 8)
    rr = df[(df.hour >= a0) & (df.hour < a1)].groupby("day").agg(rh=("h", "max"), rl=("l", "min"))
    lv = df[["day"]].join(rr, on="day")
    rh, rl = lv.rh.to_numpy(), lv.rl.to_numpy()
    orders = []
    done = set()
    for j in range(len(o) - 1):
        hr, day = b["utc_hour"][j], b["utc_day"][j]
        if hr < a1 or hr >= 12 or day in done or not np.isfinite(rh[j]):
            continue
        height = rh[j] - rl[j]
        if height <= 0:
            continue
        d = 1 if c[j] > rh[j] else (-1 if c[j] < rl[j] else 0)
        if d == 0:
            continue
        done.add(day)
        edge = rh[j] if d > 0 else rl[j]
        mid = (rh[j] + rl[j]) / 2
        stop = mid if stop_kind == "mid" else (rl[j] if d > 0 else rh[j])
        orders.append((b["first"][j + 1], np.nan, d, stop, edge + d * mult * height, 0.0))
    return orders


# ---------------------------------------------------------------- simulation

@njit(cache=True)
def simulate(ei, lim, expiry, dirs, sls, tps, fixed_r, min_rr, bo, bh, bl, ao, ah, al, commission):
    n = len(bo)
    m = len(ei)
    out_e = np.empty(m, np.int64)
    out_r = np.empty(m, np.float64)
    out_rr = np.empty(m, np.float64)
    k = 0
    busy = -1
    for q in range(m):
        e = ei[q]
        if e <= busy or e >= n:
            continue
        dr = dirs[q]
        sl = sls[q]
        # entry: market at the open, or limit touched before expiry
        if np.isnan(lim[q]):
            entry = ao[e] if dr > 0 else bo[e]
        else:
            entry = lim[q]
            f = -1
            for x in range(e, min(expiry[q], n)):
                if dr < 0 and bh[x] >= entry:      # sell limit fills on the bid
                    f = x
                    break
                if dr > 0 and al[x] <= entry:      # buy limit fills on the ask
                    f = x
                    break
                # cancel if the stop level trades first
                if (dr < 0 and ah[x] >= sl) or (dr > 0 and bl[x] <= sl):
                    break
            if f < 0:
                continue
            e = f
        risk = (entry - sl) * dr
        if not (risk > 0):
            continue
        tp = tps[q] if not np.isnan(tps[q]) else entry + dr * fixed_r * risk
        reward = (tp - entry) * dr
        if reward <= 0 or reward / risk < min_rr[q]:
            continue
        px = 0.0
        x = e
        done = False
        while x < n:
            if dr > 0:
                if x > e and bo[x] <= sl:
                    px = bo[x]; done = True
                elif bl[x] <= sl:
                    px = sl; done = True
                elif bh[x] >= tp and (x > e or np.isnan(lim[q])):
                    px = tp; done = True
            else:
                if x > e and ao[x] >= sl:
                    px = ao[x]; done = True
                elif ah[x] >= sl:
                    px = sl; done = True
                elif al[x] <= tp and (x > e or np.isnan(lim[q])):
                    px = tp; done = True
            if done:
                break
            x += 1
        if not done:
            break
        out_e[k] = e
        out_r[k] = ((px - entry) * dr - commission) / risk
        out_rr[k] = reward / risk
        k += 1
        busy = x
    return out_e[:k], out_r[:k], out_rr[:k]


def run(P, orders, fixed_r=5.0):
    if not orders:
        return pd.DataFrame(columns=["entry", "R", "rr", "year"])
    orders = sorted(orders, key=lambda x: x[0])
    ei = np.array([x[0] for x in orders], np.int64)
    lim = np.array([x[1] for x in orders], np.float64)
    dirs = np.array([x[2] for x in orders], np.int64)
    sls = np.array([x[3] for x in orders], np.float64)
    tps = np.array([x[4] for x in orders], np.float64)
    mrr = np.array([x[5] for x in orders], np.float64)
    exp = np.array([x[6] if len(x) > 6 else x[0] + 1 for x in orders], np.int64)
    e, R, rr = simulate(ei, lim, exp, dirs, sls, tps, fixed_r, mrr, P["bo"], P["bh"], P["bl"],
                        P["ao"], P["ah"], P["al"], COMMISSION)
    return pd.DataFrame(dict(entry=e, R=R, rr=rr, year=P["years"][e]))


def summary(tr):
    tr = tr[tr.year >= 2011]
    R = tr.R.to_numpy()
    if len(R) < 2:
        return dict(n=len(R))
    gp, gl = R[R > 0].sum(), -R[R < 0].sum()
    row = dict(n=len(R), per_yr=len(R) / 15.8, win=100 * (R > 0).mean(), med_rr=np.median(tr.rr),
               avgR=R.mean(), PF=gp / gl if gl else np.nan)
    for name, (a, z) in PERIODS.items():
        r = tr.R[(tr.year >= a) & (tr.year <= z)]
        row[name] = r.mean() if len(r) else np.nan
    row["consistent"] = all(row[p] > 0 for p in PERIODS)
    lo, hi = tb.bootstrap_ci(R)
    row["ci_lo"], row["ci_hi"] = lo, hi
    return row


def main(root):
    P = mrb.prepare(root)
    rows = []
    for tf in (15, 5):
        b = bars(P, tf)
        tfn = f"M{tf}"
        for htf in (60, 240):
            tr_ = htf_trend(P, b, htf)
            for buf in (0.1, 1.0):
                for tgt in ("swing", "measured"):
                    for mr in (0.0, 3.0, 4.0):
                        s = summary(run(P, setup_pullback(P, b, tr_, buf, tgt, mr)))
                        rows.append(dict(tf=tfn, setup="1 pullback",
                                         variant=f"HTF {'H1' if htf == 60 else 'H4'}, stop +{buf:g}ATR, {tgt}, minRR {mr:g}", **s))
        for tgt in ("liquidity", "5R"):
            s = summary(run(P, setup_bos(P, b, tgt)))
            rows.append(dict(tf=tfn, setup="2 BOS+retest", variant=f"target {tgt}", **s))
        for tgt in ("liquidity", "5R"):
            s = summary(run(P, setup_sweep(P, b, tgt)))
            rows.append(dict(tf=tfn, setup="3 sweep+FVG", variant=f"target {tgt}", **s))
        for rk in ("asia", "london1h"):
            for sk in ("mid", "opposite"):
                for mult in (1.0, 2.0):
                    s = summary(run(P, setup_breakout(P, b, rk, sk, mult)))
                    rows.append(dict(tf=tfn, setup="4 breakout", variant=f"{rk} range, stop {sk}, target {mult:g}x", **s))
        print(f"{tfn} done", flush=True)
    df = pd.DataFrame(rows)
    df.to_csv("results/setups_summary.csv", index=False)
    pd.set_option("display.width", 250)
    cols = ["tf", "setup", "variant", "n", "per_yr", "win", "med_rr", "PF", "avgR", "2011-18", "2019-22", "2023-26",
            "ci_lo", "ci_hi"]
    f = lambda x: f"{x:+.3f}" if isinstance(x, float) and abs(x) < 10 else (f"{x:.0f}" if isinstance(x, float) else x)
    for s in df.setup.unique():
        print(f"\n=========== {s} ===========")
        print(df[df.setup == s][cols].to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    print(f"\nSettings positive overall: {(df.avgR > 0).sum()} / {len(df)}; "
          f"positive in all three periods: {df.consistent.sum()} / {len(df)}")


if __name__ == "__main__":
    main(sys.argv[1])
