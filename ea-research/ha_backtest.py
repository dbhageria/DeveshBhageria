"""
Backtest of the Heiken Ashi Conviction strategy on Dukascopy XAUUSD 1-minute BID/ASK bars
(2010 - Oct 2026), following the EA specification:

  * Eight frames vote: M1, M5, M15, M30, M45, H1, H4, D1 on broker-server time (New York + 7 h,
    the usual GMT+2/+3 gold broker). M45 is built from M1 in buckets anchored to server midnight.
    Each frame uses only its last candle that had CLOSED at the M1 decision time; a frame whose
    latest expected candle is missing or incomplete makes that minute unusable (no entry).
  * Heikin Ashi from BID bars. A candle is a no-wick BUY if its body is >= 2 ticks and its lower
    wick <= 1 tick; a no-wick SELL mirrors that. "color" vote mode counts any HA candle with a
    body >= 2 ticks as a vote for its colour instead.
  * Entry: a FRESH M1 no-wick signal (previous M1 candle was not the same signal) with at least
    MIN_VOTES of 8 frames agreeing (M1 counts). Enter at the next minute's open: buy at ASK,
    sell at BID. One basket at a time; no adding, reversing or re-entry until a new fresh
    signal appears after the exit.
  * Initial stop: beyond the extreme of the last 5 completed M5 bid candles, plus a 0.10 buffer.
    R = distance from entry to that stop.
  * Exit "trail a/d": once the best executable quote is a*R beyond entry, the stop trails the best
    quote at d*R (moving only in the profitable direction, minimum step 5 ticks). No TP.
    Exit "tp x": fixed take-profit at x*R. Longs exit on BID, shorts on ASK. Within a minute the
    adverse extreme is assumed first (conservative); gaps through the stop fill at the open.
  * Cost: real Dukascopy spread on every fill, plus COMMISSION $/oz round trip.
  * Risk tiers for the equity curve: 5-6 votes 0.5%, 7 votes 1.0%, 8 votes 2.0% of equity.

Usage: python3 ha_backtest.py M1_ROOT [--quick]
"""
import sys
import time
import numpy as np
import pandas as pd
from numba import njit

import m1_backtest as mb

TICK = 0.01
WICK_TOL = 1 * TICK
MIN_BODY = 2 * TICK
STOP_BUFFER = 0.10
TRAIL_STEP = 5 * TICK
COMMISSION = 0.07          # $/oz round trip (about $7 per lot)
MAX_SPREAD = 1.00          # $ at entry
FRAMES = [1, 5, 15, 30, 45, 60, 240, 1440]
LOOKBACKS = (5, 12, 24)    # M5 candles for the initial stop
PERIODS = {"2011-18": (2011, 2018), "2019-22": (2019, 2022), "2023-26": (2023, 2026)}
MIN = 60_000_000_000


# ---------------------------------------------------------------- data

def server_minutes(t_utc_ns):
    """Broker server wall-clock minutes since epoch (New York + 7 h)."""
    ny = pd.DatetimeIndex(t_utc_ns, tz="UTC").tz_convert("America/New_York").tz_localize(None)
    return (ny.asi8 // MIN + 7 * 60).astype(np.int64)


def resample(m, o, h, l, c, tf):
    """Bars of tf minutes anchored to server midnight. Returns start/end minutes, OHLC, count."""
    day, mod = m // 1440, m % 1440
    bucket = day * (1440 // tf) + mod // tf
    edges = np.r_[0, np.flatnonzero(np.diff(bucket)) + 1]
    start = day[edges] * 1440 + (mod[edges] // tf) * tf
    return dict(start=start, end=start + tf, o=o[edges], h=np.maximum.reduceat(h, edges),
                l=np.minimum.reduceat(l, edges), c=c[np.r_[edges[1:] - 1, len(c) - 1]],
                n=np.diff(np.r_[edges, len(m)]))


@njit(cache=True)
def ha_states(o, h, l, c, tol, min_body):
    """Heikin Ashi recurrence; returns strict (no-wick) and colour states: +1 / -1 / 0."""
    n = len(o)
    strict = np.zeros(n, np.int8)
    colour = np.zeros(n, np.int8)
    ha_o = (o[0] + c[0]) / 2.0
    ha_c = (o[0] + h[0] + l[0] + c[0]) / 4.0
    for i in range(n):
        if i > 0:
            ha_o = (ha_o + ha_c) / 2.0
            ha_c = (o[i] + h[i] + l[i] + c[i]) / 4.0
        ha_h = max(h[i], ha_o, ha_c)
        ha_l = min(l[i], ha_o, ha_c)
        body = ha_c - ha_o
        if body >= min_body:
            colour[i] = 1
            if min(ha_o, ha_c) - ha_l <= tol:
                strict[i] = 1
        elif -body >= min_body:
            colour[i] = -1
            if ha_h - max(ha_o, ha_c) <= tol:
                strict[i] = -1
    return strict, colour


def prepare(root):
    t, cols = mb.load_m1(root)
    m = server_minutes(t)
    bo, bh, bl, bc = (cols[k] for k in ("open_b", "high_b", "low_b", "close_b"))
    ao, ah, al = (cols[k] for k in ("open_a", "high_a", "low_a"))
    n = len(m)
    T = m + 1                               # decision time: close of each M1 bar (server minutes)
    votes = {"strict": [], "color": []}
    valid = np.ones(n, bool)
    m5 = None
    for tf in FRAMES:
        if tf == 1:
            bars = dict(start=m, end=m + 1, o=bo, h=bh, l=bl, c=bc, n=np.ones(n, np.int64))
        else:
            bars = resample(m, bo, bh, bl, bc, tf)
        s, col = ha_states(bars["o"], bars["h"], bars["l"], bars["c"], WICK_TOL, MIN_BODY)
        j = np.searchsorted(bars["end"], T, side="right") - 1
        ok = j >= 0
        jj = np.clip(j, 0, None)
        complete = bars["n"][jj] >= (0.9 * tf if tf < 1440 else 1)
        stale_limit = tf if tf <= 60 else tf + 3 * 1440   # H4/D1 may span a weekend
        fresh_enough = (T - bars["end"][jj]) < stale_limit
        if tf <= 60:
            valid &= ok & complete & fresh_enough
        else:
            valid &= ok & complete & fresh_enough
        votes["strict"].append(np.where(ok, s[jj], 0))
        votes["color"].append(np.where(ok, col[jj], 0))
        if tf == 5:
            m5 = (bars, j)
    m1_strict = votes["strict"][0]
    prev = np.r_[0, m1_strict[:-1]]
    contiguous = np.r_[False, np.diff(m) == 1]
    fresh = (m1_strict != 0) & (prev != m1_strict) & contiguous
    # next bar must exist and start within 5 minutes of the decision time
    nxt_ok = np.r_[(m[1:] - T[:-1]) <= 5, False]
    # initial stop from the last N completed M5 bars (bid); the specification uses N = 5
    bars5, j5 = m5
    jj5 = np.clip(j5, 0, None)
    stops = {}
    for lb in LOOKBACKS:
        lo = pd.Series(bars5["l"]).rolling(lb).min().to_numpy()
        hi = pd.Series(bars5["h"]).rolling(lb).max().to_numpy()
        stops[lb] = (lo[jj5] - STOP_BUFFER, hi[jj5] + STOP_BUFFER)
    years = pd.DatetimeIndex(t, tz="UTC").year.to_numpy()
    utc_hour = ((t // (60 * MIN)) % 24).astype(np.int8)
    return dict(t=t, m=m, years=years, utc_hour=utc_hour, bo=bo, bh=bh, bl=bl, ao=ao, ah=ah, al=al,
                votes={k: np.vstack(v) for k, v in votes.items()}, valid=valid & nxt_ok,
                fresh=fresh, dir=m1_strict, stops=stops, stop_long=stops[5][0], stop_short=stops[5][1])


# ---------------------------------------------------------------- simulation

@njit(cache=True)
def simulate(cand, direction, stop0, bo, bh, bl, ao, ah, al, mode, a, d, tp_r, commission, max_spread):
    """Sequential baskets. mode 0 = trailing (activate a*R, distance d*R), 1 = fixed TP tp_r*R.
    Returns per trade: entry index, exit index, R multiple."""
    n = len(bo)
    out_i = np.empty(len(cand), np.int64)
    out_x = np.empty(len(cand), np.int64)
    out_r = np.empty(len(cand), np.float64)
    k = 0
    busy_until = -1
    for q in range(len(cand)):
        i = cand[q]
        e = i + 1
        if e <= busy_until or e >= n:
            continue
        if ao[e] - bo[e] > max_spread:
            continue
        dr = direction[q]
        entry = ao[e] if dr > 0 else bo[e]
        stop = stop0[q]
        risk = (entry - stop) * dr
        if not (risk > 0):
            continue
        tp = entry + dr * tp_r * risk
        best = entry
        px = 0.0
        x = e
        done = False
        while x < n:
            if dr > 0:
                op, hi, lo = bo[x], bh[x], bl[x]
                if x > e and op <= stop:
                    px = op; done = True
                elif lo <= stop:
                    px = stop; done = True
                elif mode == 1 and hi >= tp:
                    px = tp; done = True
                elif mode == 0:
                    if hi > best:
                        best = hi
                    if best - entry >= a * risk:
                        ns = best - d * risk
                        if ns >= stop + 0.05:
                            stop = ns
                            if lo <= stop:
                                px = stop; done = True
            else:
                op, hi, lo = ao[x], ah[x], al[x]
                if x > e and op >= stop:
                    px = op; done = True
                elif hi >= stop:
                    px = stop; done = True
                elif mode == 1 and lo <= tp:
                    px = tp; done = True
                elif mode == 0:
                    if lo < best:
                        best = lo
                    if entry - best >= a * risk:
                        ns = best + d * risk
                        if ns <= stop - 0.05:
                            stop = ns
                            if hi >= stop:
                                px = stop; done = True
            if done:
                break
            x += 1
        if not done:
            break
        out_i[k] = e
        out_x[k] = x
        out_r[k] = ((px - entry) * dr - commission) / risk
        k += 1
        busy_until = x
    return out_i[:k], out_x[:k], out_r[:k]


def run(P, vote_mode, min_votes, side, exit_, session, commission=COMMISSION, max_spread=MAX_SPREAD,
        lookback=5):
    V = P["votes"][vote_mode]
    dr = P["dir"]
    agree = (V == dr[None, :]).sum(axis=0)
    mask = P["fresh"] & P["valid"] & (agree >= min_votes) & (P["years"] >= 2010)
    if side == "long":
        mask &= dr > 0
    elif side == "short":
        mask &= dr < 0
    if session == "ldn-ny":
        h = P["utc_hour"]
        mask &= (h >= 7) & (h < 20)
    cand = np.flatnonzero(mask)
    dirs = dr[cand].astype(np.int64)
    sl_long, sl_short = P["stops"][lookback]
    stops = np.where(dirs > 0, sl_long[cand], sl_short[cand])
    if exit_[0] == "trail":
        mode, a, d, tp = 0, exit_[1], exit_[2], 0.0
    else:
        mode, a, d, tp = 1, 0.0, 0.0, exit_[1]
    ei, xi, R = simulate(cand, dirs, stops, P["bo"], P["bh"], P["bl"], P["ao"], P["ah"], P["al"],
                         mode, a, d, tp, commission, max_spread)
    return pd.DataFrame(dict(entry=ei, exit=xi, R=R, votes=agree[ei - 1], year=P["years"][ei],
                             dir=dr[ei - 1]))


def equity(trades):
    risk = np.where(trades.votes >= 8, 0.02, np.where(trades.votes == 7, 0.01, 0.005))
    eq = np.cumprod(1 + risk * trades.R.to_numpy())
    peak = np.maximum.accumulate(np.r_[1.0, eq])[1:]
    return (eq[-1] if len(eq) else 1.0), (1 - eq / peak).max() if len(eq) else 0.0


def summary(tr):
    tr = tr[tr.year >= 2011]
    R = tr.R.to_numpy()
    gp, gl = R[R > 0].sum(), -R[R < 0].sum()
    row = dict(n=len(R), win=100 * (R > 0).mean() if len(R) else np.nan,
               avgR=R.mean() if len(R) else np.nan, PF=gp / gl if gl else np.nan)
    for name, (a, b) in PERIODS.items():
        r = tr.R[(tr.year >= a) & (tr.year <= b)]
        row[name] = r.mean() if len(r) else np.nan
    row["consistent"] = all(row[p] > 0 for p in PERIODS)
    g, dd = equity(tr)
    row["growth"], row["maxDD"] = g, dd
    return row


def label(exit_):
    return f"trail {exit_[1]:g}/{exit_[2]:g}" if exit_[0] == "trail" else f"tp {exit_[1]:g}R"


def main(root, quick=False):
    t0 = time.time()
    P = prepare(root)
    print(f"Prepared {len(P['m']):,} M1 bars in {time.time()-t0:.0f}s; "
          f"usable decision minutes {P['valid'].mean():.1%}; fresh M1 no-wick signals {P['fresh'].sum():,}")

    exits = [("trail", 1, 1), ("trail", 1, 0.5), ("trail", 2, 1), ("tp", 1.5), ("tp", 2), ("tp", 3)]
    grid = [(vm, mv, sd, ex, ss, lb) for vm in ("strict", "color") for mv in (5, 6, 7, 8)
            for sd in ("both", "long", "short") for ex in exits for ss in ("all", "ldn-ny")
            for lb in LOOKBACKS]
    if quick:
        grid = grid[:6]
    rows = []
    for vm, mv, sd, ex, ss, lb in grid:
        s = summary(run(P, vm, mv, sd, ex, ss, lookback=lb))
        rows.append(dict(votes=vm, min=mv, side=sd, exit=label(ex), session=ss, stop_m5=lb, **s))
    df = pd.DataFrame(rows)
    pd.set_option("display.width", 200)
    fmt = lambda x: f"{x:+.3f}" if isinstance(x, float) and abs(x) < 10 else (f"{x:.2f}" if isinstance(x, float) else x)

    print("\n=========== Specification defaults (strict votes, min 5, both sides, trail 1/1, all hours) ===========")
    print(df[(df.votes == "strict") & (df["min"] == 5) & (df.side == "both") & (df.exit == "trail 1/1")
             & (df.session == "all") & (df.stop_m5 == 5)].to_string(index=False, float_format=lambda x: f"{x:.3f}"))

    print(f"\n=========== All {len(df)} settings ===========")
    print(f"Positive average R over 2011-2026: {(df.avgR > 0).sum()} / {len(df)}")
    print(f"Positive in each of 2011-18, 2019-22, 2023-26: {df.consistent.sum()} / {len(df)}")
    print("\nBy dimension (share of settings with positive average R, median avg R):")
    for col in ("votes", "min", "side", "exit", "session", "stop_m5"):
        g = df.groupby(col).agg(pos=("avgR", lambda x: f"{(x > 0).mean():.0%}"), median_avgR=("avgR", "median"),
                                median_trades=("n", "median"))
        print(g.to_string(float_format=lambda x: f"{x:+.3f}"), "\n")

    show = ["votes", "min", "side", "exit", "session", "stop_m5", "n", "win", "PF", "avgR", "2011-18", "2019-22", "2023-26",
            "growth", "maxDD"]
    print("Top 15 by average R (at least 300 trades):")
    print(df[df.n >= 300].sort_values("avgR", ascending=False)[show].head(15).to_string(
        index=False, float_format=lambda x: f"{x:.3f}"))
    print("\nConsistent settings (positive in all three periods), by average R:")
    c = df[df.consistent].sort_values("avgR", ascending=False)
    print(c[show].head(25).to_string(index=False, float_format=lambda x: f"{x:.3f}") if len(c) else "(none)")
    df.to_csv(sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else "ha_grid.csv", index=False)
    print(f"\nDone in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main(sys.argv[1], quick="--quick" in sys.argv)
