"""
Tick-exact backtest of the London -> NY continuation strategy on Dukascopy XAUUSD ticks.

  * London move = NY-open price - London 08:00 open (1-minute bid bars, London local time).
  * Entry at the first tick at/after NY 08:00 (New York local time): buy at ASK, sell at BID.
  * Long exits are checked on BID, short exits on ASK, tick by tick, so there is no intrabar guessing
    and the real spread is paid. Stops fill at the tick that crosses them (gap slippage included),
    take-profits fill at the limit price. Anything open is closed at the last tick before NY 17:00.
  * $ levels are quoted in 2026 terms and scaled to each day, two ways:
      price: by gold price (REF $4,450), so $30 / $40 / $60 = 0.67% / 0.90% / 1.35% of price
      vol:   by volatility, i.e. the prior 20-day average daily range relative to the 2026
             sample's average daily range (no look-ahead)
  * Results are in R (multiples of the stop distance) and in 2026-equivalent $ per ounce.

Optional: the 2026 1-minute bar sample is run through the same engine with each bar replayed as
open -> adverse extreme -> favourable extreme -> close, and a fixed $0.35 spread.

Usage: python3 tick_backtest.py TICKS_DIR [XAUUSD_1m_2026.csv]
"""
import glob
import os
import sys
import numpy as np
import pandas as pd

REF = 4450.0
IS_YEARS = range(2011, 2015)
OOS_YEARS = range(2015, 2019)
BAR_SPREAD = 0.35
NO_LEVEL = 1e9


# ---------------------------------------------------------------- data

def local_ns(day, hhmm, tz):
    return pd.Timestamp(f"{day} {hhmm}", tz=tz).value


def prior_adr(m1_t, m1_h, m1_l, n=20):
    """Average daily (NY calendar day) range over the previous n weekdays, indexed by NY date."""
    ny_day = pd.to_datetime(m1_t, utc=True).tz_convert("America/New_York").normalize()
    rng = pd.DataFrame({"d": ny_day, "h": m1_h, "l": m1_l}).groupby("d").agg(h=("h", "max"), l=("l", "min"))
    rng = rng[rng.index.weekday < 5]
    return (rng.h - rng.l).rolling(n).mean().shift(1)


def build_days(m1_t, m1_o, adr20, tick_t, bid, ask, label, min_rows=500):
    """One record per NY weekday with London move and the NY tick slice."""
    days = []
    for d in adr20.index:
        day = d.date()
        if np.isnan(adr20[d]) or not (tick_t[0] <= d.value + 86400e9 and d.value <= tick_t[-1]):
            continue
        lon0 = local_ns(day, "08:00", "Europe/London")
        ny0 = local_ns(day, "08:00", "America/New_York")
        ny1 = local_ns(day, "17:00", "America/New_York")
        a, b = np.searchsorted(m1_t, [lon0, ny0])
        if b - a < 0.8 * (ny0 - lon0) / 60e9:  # need >= 80% of London minutes
            continue
        i, j = np.searchsorted(tick_t, [ny0, ny1])
        if j - i < min_rows or tick_t[i] - ny0 > 5 * 60e9:  # holiday / missing data
            continue
        l_open = m1_o[a]
        entry_mid = (bid[i] + ask[i]) / 2
        days.append(dict(date=day, year=day.year, src=label, l_open=l_open,
                         l_move=entry_mid - l_open, pscale=l_open / REF, adr=adr20[d], i=i, j=j))
    return days


def load_ticks(ticks_dir):
    """Yields (days, bid, ask) per year; the volatility average runs across year boundaries."""
    m1s = [np.load(f) for f in sorted(glob.glob(os.path.join(ticks_dir, "*_m1.npz")))]
    m1_t, m1_o, m1_h, m1_l = (np.concatenate([m[k] for m in m1s]) for k in "tohl")
    adr20 = prior_adr(m1_t, m1_h, m1_l)
    for f in sorted(glob.glob(os.path.join(ticks_dir, "*_ticks.npz"))):
        tk = np.load(f)
        t, bid, ask = tk["t"], tk["bid"], tk["ask"]
        yield build_days(m1_t, m1_o, adr20, t, bid, ask, "tick"), bid, ask


def load_bars_2026(path):
    df = pd.read_csv(path)
    t = pd.to_datetime(df["datetime"], utc=True).to_numpy().astype("datetime64[ns]").astype(np.int64)
    o, h, l, c = (df[k].to_numpy(np.float64) for k in ("open", "high", "low", "close"))
    days = []
    for rec in build_days(t, o, prior_adr(t, h, l), t, o, o + BAR_SPREAD, "bars", min_rows=400):
        sl = slice(rec["i"], rec["j"])
        rec["bars"] = (o[sl], h[sl], l[sl], c[sl])
        days.append(rec)
    return days


def bar_path(bars, direction):
    """Replay 1m bars as a tick path, adverse extreme first (conservative)."""
    o, h, l, c = bars
    first, second = (l, h) if direction > 0 else (h, l)
    mid = np.column_stack([o, first, second, c]).ravel()
    return mid, mid + BAR_SPREAD


# ---------------------------------------------------------------- simulation

class DayPath:
    """Precomputed running excursions for one day and one direction, so many SL/TP pairs are cheap."""

    def __init__(self, bid, ask, direction):
        bid, ask = np.asarray(bid, np.float64), np.asarray(ask, np.float64)
        entry, exit_px = (ask[0], bid) if direction > 0 else (bid[0], ask)
        move = (exit_px - entry) * direction
        self.adv = np.maximum.accumulate(-move).astype(np.float32)  # running worst adverse excursion
        self.fav = np.maximum.accumulate(move).astype(np.float32)   # running best favourable excursion
        self.move = move.astype(np.float32)

    def run(self, sl, tp):
        n = len(self.move)
        i_sl = np.searchsorted(self.adv, sl, side="left") if sl < NO_LEVEL else n
        i_tp = np.searchsorted(self.fav, tp, side="left") if tp < NO_LEVEL else n
        if i_sl < n and i_sl <= i_tp:
            return float(self.move[i_sl]), "sl"         # stop fills at the crossing tick
        if i_tp < n:
            return tp, "tp"                      # limit fills at the level
        return float(self.move[-1]), "close"


def paths_for(days, tick_sets):
    """Attach a DayPath in London's direction (and the opposite one, for the baseline) to each day."""
    for days_y, bid, ask in tick_sets:
        for r in days_y:
            r["dir"] = 1 if r["l_move"] > 0 else -1
            b, a = bid[r["i"]:r["j"]], ask[r["i"]:r["j"]]
            r["path"] = DayPath(b, a, r["dir"])
            days.append(r)
    return days


def attach_bar_paths(days):
    for r in days:
        r["dir"] = 1 if r["l_move"] > 0 else -1
        b, a = bar_path(r["bars"], r["dir"])
        r["path"] = DayPath(b, a, r["dir"])
    return days


def trades(days, thr, sl, tp):
    """Run the strategy; returns DataFrame with pnl in 2026-$ and R."""
    rows = []
    for r in days:
        if abs(r["l_move"]) <= thr * r["scale"]:
            continue
        s = r["scale"]
        pnl, how = r["path"].run(sl * s if sl < NO_LEVEL else NO_LEVEL,
                                 tp * s if tp < NO_LEVEL else NO_LEVEL)
        risk = sl if sl < NO_LEVEL else 40.0
        rows.append(dict(date=r["date"], year=r["year"], pnl=pnl / s, R=pnl / s / risk, exit=how))
    return pd.DataFrame(rows)


def stats(t):
    if len(t) == 0:
        return dict(n=0, win=np.nan, avgR=np.nan, PF=np.nan, totR=0.0, ddR=0.0, usd=0.0)
    R = t["R"].to_numpy()
    eq = np.r_[0, R.cumsum()]
    gp, gl = R[R > 0].sum(), -R[R < 0].sum()
    return dict(n=len(R), win=100 * (R > 0).mean(), avgR=R.mean(), PF=gp / gl if gl else np.inf,
                totR=R.sum(), ddR=(np.maximum.accumulate(eq) - eq).max(), usd=t["pnl"].sum())


def fmt(s):
    return (f"{s['n']:>5} {s['win']:>5.1f}% {s['avgR']:>+6.3f} {s['PF']:>5.2f} {s['totR']:>+7.1f} "
            f"{s['ddR']:>6.1f} {s['usd']:>+8.0f}")


HDR = f"{'trades':>5} {'win':>6} {'avgR':>6} {'PF':>5} {'totR':>7} {'maxDD':>6} {'2026$':>8}"


def bootstrap_ci(R, n=10000, seed=1):
    if len(R) < 2:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    means = rng.choice(R, size=(n, len(R)), replace=True).mean(axis=1)
    return np.percentile(means, [2.5, 97.5])


# ---------------------------------------------------------------- report

def claim_table(days, label):
    sig = [r for r in days if abs(r["l_move"]) > 30 * r["scale"]]
    def rate(rs, f):
        return f"{sum(f(r) for r in rs):>4}/{len(rs):<4} {100*np.mean([f(r) for r in rs]) if rs else float('nan'):5.1f}%"
    with60 = lambda r: r["path"].fav[-1] >= 60 * r["scale"]
    against60 = lambda r: r["path"].adv[-1] >= 60 * r["scale"]
    close_with = lambda r: r["path"].move[-1] > 0
    tp_first = lambda r: r["path"].run(30 * r["scale"], 60 * r["scale"])[1] == "tp"
    print(f"\n[{label}] days={len(days)}  signal days (London > $30-equiv)={len(sig)}")
    print(f"  A  NY +$60 in London dir      signal {rate(sig, with60)}   all days {rate(days, with60)}")
    print(f"  B  NY -$60 against London     signal {rate(sig, against60)}   all days {rate(days, against60)}")
    print(f"  D  NY closes in London dir    signal {rate(sig, close_with)}   all days {rate(days, close_with)}")
    print(f"  F  +$60 before -$30           signal {rate(sig, tp_first)}")


def set_scale(days, mode, ref_adr):
    for r in days:
        r["scale"] = r["pscale"] if mode == "price" else r["adr"] / ref_adr


def main(ticks_dir, bars_csv=None):
    tick_days = paths_for([], load_ticks(ticks_dir))
    years = sorted({r["year"] for r in tick_days})
    print(f"Tick data: {len(tick_days)} usable NY days, years {years[0]}-{years[-1]}")
    bar_days = attach_bar_paths(load_bars_2026(bars_csv)) if bars_csv else []
    ref_adr = np.mean([r["adr"] for r in bar_days]) if bar_days else 75.0
    print(f"Reference 2026 average daily range: ${ref_adr:.1f}")
    for mode in ("price", "vol"):
        set_scale(tick_days, mode, ref_adr)
        set_scale(bar_days, mode, ref_adr)
        print(f"\n\n################ SCALING BY {mode.upper()} ################")
        report(tick_days, bar_days, years)


def report(tick_days, bar_days, years):

    print("\n=========== 1. The forum claim ===========")
    claim_table(tick_days, "ticks 2011-2018")
    if bar_days:
        claim_table(bar_days, "1m bars 2026")

    print("\n=========== 2. Default EA settings: London > $30, SL $40, TP $60 (2026-$) ===========")
    print(f"{'period':<12} {HDR}")
    by_year = trades(tick_days, 30, 40, 60)
    for y in years:
        print(f"{y:<12} {fmt(stats(by_year[by_year.year == y]))}")
    print(f"{'2011-2018':<12} {fmt(stats(by_year))}")
    if bar_days:
        print(f"{'2026 (1m)':<12} {fmt(stats(trades(bar_days, 30, 40, 60)))}")

    print("\n=========== 3. Does the London filter add anything? (SL 40 / TP 60, ticks) ===========")
    print(f"{'London >':<12} {HDR}")
    for thr in [0, 10, 20, 30, 40, 50, 70]:
        print(f"{'$'+str(thr):<12} {fmt(stats(trades(tick_days, thr, 40, 60)))}")

    print("\n=========== 4. Walk-forward: choose on 2011-2014, test on 2015-2018 (+2026) ===========")
    is_days = [r for r in tick_days if r["year"] in IS_YEARS]
    oos_days = [r for r in tick_days if r["year"] in OOS_YEARS]
    grid = [(thr, sl, tp) for thr in (20, 30, 40, 50)
            for sl in (20, 30, 40, 60, NO_LEVEL) for tp in (30, 45, 60, 80, 120, NO_LEVEL)]
    res = []
    for thr, sl, tp in grid:
        s = stats(trades(is_days, thr, sl, tp))
        if s["n"] >= 40:
            res.append((s["avgR"] * np.sqrt(s["n"]), thr, sl, tp, s))
    res.sort(key=lambda x: -x[0])
    lab = lambda v: "none" if v >= NO_LEVEL else f"{v:g}"
    print("Top 5 in-sample (ranked by avgR * sqrt(n)):")
    print(f"{'thr/SL/TP':<14} {HDR}")
    for _, thr, sl, tp, s in res[:5]:
        print(f"{f'{thr}/{lab(sl)}/{lab(tp)}':<14} {fmt(s)}")
    pos = sum(1 for x in res if x[4]["avgR"] > 0)
    print(f"In-sample combos with positive expectancy: {pos}/{len(res)}")

    if not res:
        print("Not enough in-sample trades for the walk-forward.")
        return
    _, thr, sl, tp, _ = res[0]
    print(f"\nOut-of-sample, IS-best {thr}/{lab(sl)}/{lab(tp)} vs default 30/40/60:")
    print(f"{'set / period':<22} {HDR}")
    for name, p in (("IS-best", (thr, sl, tp)), ("default", (30, 40, 60))):
        t_oos = trades(oos_days, *p)
        print(f"{name+' 2015-2018':<22} {fmt(stats(t_oos))}")
        lo, hi = bootstrap_ci(t_oos["R"].to_numpy())
        print(f"{'':<22} 95% bootstrap CI of avgR: {lo:+.3f} .. {hi:+.3f}")
        if bar_days:
            print(f"{name+' 2026 (1m)':<22} {fmt(stats(trades(bar_days, *p)))}")

    print("\n=========== 5. Exit breakdown, default settings 2011-2018 ===========")
    print(by_year["exit"].value_counts().to_string())

    print("\n=========== 6. Equity at 0.5% risk per trade, default settings 2011-2018 ===========")
    eq = 1.0
    peak, mdd = 1.0, 0.0
    for R in by_year["R"]:
        eq *= 1 + 0.005 * R
        peak = max(peak, eq)
        mdd = max(mdd, 1 - eq / peak)
    print(f"Growth x{eq:.3f} ({100*(eq-1):+.1f}%), max drawdown {100*mdd:.1f}%, "
          f"{len(by_year)} trades over {len(years)} calendar years")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
