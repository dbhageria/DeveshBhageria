"""
Test the forum claim on XAUUSD:
  "Anytime London moves more than $30 in a direction, NY has an 87-98% chance
   of continuing over a $60 move in that same direction."

Sessions are defined in local exchange time so DST is handled correctly:
  London : 08:00-13:00 Europe/London  (up to the NY open)
  NY     : 08:00-17:00 America/New_York

Usage: python3 london_ny_study.py path/to/XAUUSD_5m.csv
"""
import sys
import math
import numpy as np
import pandas as pd

LONDON_MOVE = 30.0
NY_TARGET = 60.0
SPREAD = 0.35  # round-trip cost assumption in $ per oz


def wilson(k, n, z=1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def pct(k, n):
    lo, hi = wilson(k, n)
    return f"{k}/{n} = {100*k/n:5.1f}%  (95% CI {100*lo:4.1f}-{100*hi:4.1f}%)" if n else "n/a"


def load(path):
    df = pd.read_csv(path)
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    return df.set_index("datetime").sort_index()


def session_slice(df, day, tz, start, end):
    t = df.index.tz_convert(tz)
    mask = (t.date == day) & (t.time >= start) & (t.time < end)
    return df[mask]


def build_days(df):
    rows = []
    ny_dates = sorted(set(df.index.tz_convert("America/New_York").date))
    lon_t = df.index.tz_convert("Europe/London")
    ny_t = df.index.tz_convert("America/New_York")
    for d in ny_dates:
        if pd.Timestamp(d).weekday() >= 5:
            continue
        lon = df[(lon_t.date == d) & (lon_t.hour >= 8) & (lon_t.hour < 13)]
        ny = df[(ny_t.date == d) & (ny_t.hour >= 8) & (ny_t.hour < 17)]
        if len(lon) < 50 or len(ny) < 90:  # skip holidays / partial sessions
            continue
        l_open, l_close = lon["open"].iloc[0], lon["close"].iloc[-1]
        n_open, n_close = ny["open"].iloc[0], ny["close"].iloc[-1]
        rows.append(dict(
            date=d,
            l_open=l_open, l_close=l_close, l_high=lon["high"].max(), l_low=lon["low"].min(),
            n_open=n_open, n_close=n_close, n_high=ny["high"].max(), n_low=ny["low"].min(),
            ny_bars=ny[["open", "high", "low", "close"]].to_numpy(),
        ))
    out = pd.DataFrame(rows)
    out["l_move"] = out.l_close - out.l_open
    out["dir"] = np.sign(out.l_move)
    # NY excursions measured from NY open, signed to the London direction
    out["ny_with"] = np.where(out.dir > 0, out.n_high - out.n_open, out.n_open - out.n_low)
    out["ny_against"] = np.where(out.dir > 0, out.n_open - out.n_low, out.n_high - out.n_open)
    out["ny_net"] = (out.n_close - out.n_open) * out.dir
    # Excursion measured from the London open (whole-day continuation)
    out["day_with"] = np.where(out.dir > 0, out.n_high - out.l_open, out.l_open - out.n_low)
    return out


def simulate(day, sl, tp):
    """Enter at NY open in London's direction. SL checked before TP inside a bar (conservative)."""
    bars, d, entry = day.ny_bars, day.dir, day.n_open
    for o, h, l, c in bars:
        adverse = (entry - l) if d > 0 else (h - entry)
        favour = (h - entry) if d > 0 else (entry - l)
        if adverse >= sl:
            return -sl - SPREAD
        if favour >= tp:
            return tp - SPREAD
    return (bars[-1][3] - entry) * d - SPREAD


def main(path):
    df = load(path)
    days = build_days(df)
    sig = days[days.l_move.abs() > LONDON_MOVE]
    n_all, n_sig = len(days), len(sig)

    print(f"Data: {df.index[0]} -> {df.index[-1]}   trading days: {n_all}")
    print(f"Median London range ${(days.l_high-days.l_low).median():.0f}, "
          f"median NY range ${(days.n_high-days.n_low).median():.0f}")
    print(f"Days with |London net move| > ${LONDON_MOVE:.0f}: {pct(n_sig, n_all)}\n")

    print("== Claim test (signal days) ==")
    print("A. NY touches +$60 in London direction (from NY open):  ", pct((sig.ny_with >= NY_TARGET).sum(), n_sig))
    print("B. NY touches -$60 AGAINST London direction:             ", pct((sig.ny_against >= NY_TARGET).sum(), n_sig))
    print("C. NY CLOSES >= +$60 in London direction:                ", pct((sig.ny_net >= NY_TARGET).sum(), n_sig))
    print("D. NY closes in London direction at all (> $0):          ", pct((sig.ny_net > 0).sum(), n_sig))
    print("E. Price reaches $60 beyond London OPEN during NY:       ", pct((sig.day_with >= NY_TARGET).sum(), n_sig))
    print("F. NY hits +$60 with London dir BEFORE -$30 against:     ",
          pct(sum(simulate(r, 30, 60) > 0 for r in sig.itertuples()), n_sig))

    print("\n== Baseline (ALL days, direction = London's sign, no $30 filter) ==")
    print("A. NY touches +$60 in London direction:                  ", pct((days.ny_with >= NY_TARGET).sum(), n_all))
    print("B. NY touches -$60 against London direction:             ", pct((days.ny_against >= NY_TARGET).sum(), n_all))
    print("D. NY closes in London direction:                        ", pct((days.ny_net > 0).sum(), n_all))
    either = ((days.n_high - days.n_open) >= NY_TARGET) | ((days.n_open - days.n_low) >= NY_TARGET)
    print("   NY moves $60 from open in EITHER direction:           ", pct(either.sum(), n_all))

    print("\n== Correlation: London net move vs NY net move ==")
    raw_ny = days.n_close - days.n_open
    print(f"Pearson r (all days) = {np.corrcoef(days.l_move, raw_ny)[0,1]:+.3f}")
    print(f"Pearson r (signal days) = {np.corrcoef(sig.l_move, sig.n_close - sig.n_open)[0,1]:+.3f}")

    print("\n== Tradeable version: buy/sell at NY open in London direction ==")
    print(f"(cost ${SPREAD} per trade, SL checked first when both hit in one 5m bar, flat at NY 17:00)")
    print(f"{'SL':>4} {'TP':>4} {'trades':>6} {'win%':>6} {'avg $':>7} {'total $':>8} {'PF':>5} {'maxDD $':>8}")
    for sl, tp in [(20, 60), (30, 60), (40, 60), (60, 60), (30, 30), (30, 45), (40, 80), (1e9, 60), (1e9, 1e9)]:
        pnl = np.array([simulate(r, sl, tp) for r in sig.itertuples()])
        eq = pnl.cumsum()
        dd = (np.maximum.accumulate(np.r_[0, eq]) - np.r_[0, eq]).max()
        gp, gl = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
        lab = lambda x: "none" if x > 1e8 else f"{x:.0f}"
        print(f"{lab(sl):>4} {lab(tp):>4} {len(pnl):>6} {100*(pnl>0).mean():>5.1f}% {pnl.mean():>7.2f} "
              f"{pnl.sum():>8.1f} {gp/gl if gl else float('inf'):>5.2f} {dd:>8.1f}")

    print("\n== Sensitivity to the London threshold (outcome A / B) ==")
    for th in [10, 20, 30, 40, 50]:
        s = days[days.l_move.abs() > th]
        if len(s):
            print(f"London > ${th:>2}: n={len(s):>3}  with>=60: {100*(s.ny_with>=60).mean():5.1f}%   "
                  f"against>=60: {100*(s.ny_against>=60).mean():5.1f}%   NY close with: {100*(s.ny_net>0).mean():5.1f}%")

    print("\n== Signal days detail ==")
    print(sig[["date", "l_move", "ny_with", "ny_against", "ny_net"]].round(1).to_string(index=False))


if __name__ == "__main__":
    main(sys.argv[1])
