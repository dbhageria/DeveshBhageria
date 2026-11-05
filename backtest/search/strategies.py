"""Strategy families: each returns signal arrays from engine.Data, plus its parameter grid.

All signals use information available at the close of bar i (entries happen at bar i+1's open).
"""
import itertools
import numpy as np
import pandas as pd

RULES = {"H1": "1h", "H4": "4h", "D1": "1D"}


def _cross_up(x, lvl):
    prev = np.roll(x, 1); prev[0] = x[0]
    return (prev < lvl) & (x >= lvl)


def _cross_dn(x, lvl):
    prev = np.roll(x, 1); prev[0] = x[0]
    return (prev > lvl) & (x <= lvl)


def _sides(sig, side):
    if side == "long":
        sig["short"] = np.zeros_like(sig["long"])
    elif side == "short":
        sig["long"] = np.zeros_like(sig["short"])
    return sig


# F1: buy pullbacks in a higher-timeframe uptrend (sell rallies in a downtrend)
def trend_pullback(D, htf, ema_n, rsi_lvl, side):
    tr = D.htf_close_ema(RULES[htf], ema_n)
    rsi = D.rsi(14)
    sig = dict(long=(tr > 0) & _cross_up(rsi, rsi_lvl),
               short=(tr < 0) & _cross_dn(rsi, 100 - rsi_lvl))
    sig["exit_long"] = tr < 0
    sig["exit_short"] = tr > 0
    return _sides(sig, side)


# F2: Donchian channel breakout on M15 bars
def donchian_breakout(D, n, filt, side):
    hh, ll = D.donchian(n)
    up = D.c > hh
    dn = D.c < ll
    if filt != "none":
        tr = D.htf_close_ema(RULES[filt], 50)
        up &= tr > 0
        dn &= tr < 0
    return _sides(dict(long=up, short=dn), side)


# F3: Asian-range breakout (range 00:00-07:00 UTC), one trade per day, flat by 20:00 UTC
def asian_breakout(D, range_end, entry_end, filt, side):
    df = pd.DataFrame({"h": D.h, "l": D.l, "c": D.c, "hour": D.hour}, index=D.idx)
    day = D.idx.normalize()
    in_range = df["hour"] < range_end
    rh = df["h"].where(in_range).groupby(day).transform("max")
    rl = df["l"].where(in_range).groupby(day).transform("min")
    window = (df["hour"] >= range_end) & (df["hour"] < entry_end)
    up = window & (df["c"] > rh)
    dn = window & (df["c"] < rl)
    if filt != "none":
        tr = pd.Series(D.htf_close_ema(RULES[filt], 50), index=D.idx)
        up &= tr > 0
        dn &= tr < 0
    first = (up | dn) & ((up | dn).astype(int).groupby(day).cumsum() == 1)
    eod = (df["hour"] == 19) & (D.minute == 45)
    sig = dict(long=(first & up).to_numpy(), short=(first & dn).to_numpy(),
               exit_long=eod.to_numpy(), exit_short=eod.to_numpy())
    return _sides(sig, side)


# F4: Bollinger-band mean reversion, exit at the middle band
def bollinger_revert(D, n, k, filt, side):
    s = pd.Series(D.c)
    mid = s.rolling(n).mean(); sd = s.rolling(n).std()
    lo = (mid - k * sd).to_numpy(); hi = (mid + k * sd).to_numpy(); mid = mid.to_numpy()
    long_ = D.c < lo
    short = D.c > hi
    if filt != "none":
        tr = D.htf_close_ema(RULES[filt], 50)
        long_ &= tr > 0
        short &= tr < 0
    sig = dict(long=long_, short=short, exit_long=D.c >= mid, exit_short=D.c <= mid)
    return _sides(sig, side)


# F5: higher-timeframe moving-average crossover (always in the market unless long/short-only)
def ma_cross(D, htf, fast, slow, side):
    f = D.htf_ma(RULES[htf], fast); s = D.htf_ma(RULES[htf], slow)
    up = f > s
    prev = np.roll(up, 1); prev[0] = up[0]
    long_ = up & ~prev
    short = ~up & prev
    sig = dict(long=long_, short=short, exit_long=~up, exit_short=up)
    return _sides(sig, side)


FAMILIES = {
    "trend_pullback": (trend_pullback,
        dict(htf=["H1", "H4", "D1"], ema_n=[50, 200], rsi_lvl=[30, 40], side=["both", "long"]),
        dict(stop_mult=[1.5, 2.0, 3.0], rr=[1.5, 2.0, 3.0], trail_mult=[0.0], max_bars=[0, 96])),
    "donchian_breakout": (donchian_breakout,
        dict(n=[48, 96, 192, 384], filt=["none", "D1"], side=["both", "long"]),
        dict(stop_mult=[2.0, 3.0, 4.0], rr=[0.0, 2.0, 3.0], trail_mult=[0.0, 3.0, 5.0], max_bars=[0])),
    "asian_breakout": (asian_breakout,
        dict(range_end=[6, 7], entry_end=[12, 16], filt=["none", "D1"], side=["both", "long"]),
        dict(stop_mult=[1.0, 2.0, 3.0], rr=[0.0, 1.0, 2.0, 3.0], trail_mult=[0.0], max_bars=[0])),
    "bollinger_revert": (bollinger_revert,
        dict(n=[20, 48], k=[2.0, 2.5, 3.0], filt=["none", "D1"], side=["both", "long"]),
        dict(stop_mult=[2.0, 3.0, 4.0], rr=[0.0], trail_mult=[0.0], max_bars=[32, 96])),
    "ma_cross": (ma_cross,
        dict(htf=["H1", "H4", "D1"], fast=[10, 20], slow=[50, 100], side=["both", "long"]),
        dict(stop_mult=[4.0, 8.0, 12.0], rr=[0.0], trail_mult=[0.0, 8.0], max_bars=[0])),
}


def grid(family):
    _, sp, ep = FAMILIES[family]
    keys = list(sp) + list(ep)
    vals = list(sp.values()) + list(ep.values())
    for combo in itertools.product(*[range(len(v)) for v in vals]):
        yield {k: vals[j][ci] for j, (k, ci) in enumerate(zip(keys, combo))}, combo
