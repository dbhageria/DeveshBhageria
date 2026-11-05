"""
Trade-by-trade list of what LondonNYContinuation.mq5 should do, for comparison with the MT5
Strategy Tester. It follows the EA's rules on Dukascopy 1-minute bid/ask bars:

  * London move = bid close of the last minute before NY 08:00 - bid open at London 08:00
  * Entry at the first minute at/after NY 08:00: buy at the ASK open, sell at the BID open
  * SL/TP are fixed distances from the entry price; longs exit on BID, shorts on ASK. If SL and TP
    fall in the same minute, SL is assumed first. A stop jumped by a gap fills at the gap price.
  * Forced close at NY 16:55 (the EA's default 23:55 server time on a GMT+2/+3 broker)
  * With --scale-price, every $ level is multiplied by London open / 4450, like the EA's
    InpScaleByPrice option

Server times assume the usual GMT+2 / GMT+3 gold broker, which runs at New York time + 7 hours.

Usage: python3 trade_list.py M1_ROOT OUT.csv --from 2026-01-01 --side short --london 40 --sl 40 --tp 0
                             [--scale-price] [--to 2026-12-31]
"""
import argparse
import numpy as np
import pandas as pd

import m1_backtest as mb

REF_PRICE = 4450.0


def ny_ns(day, hhmm, tz="America/New_York"):
    return pd.Timestamp(f"{day} {hhmm}", tz=tz).value


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("out")
    ap.add_argument("--from", dest="start", default="2026-01-01")
    ap.add_argument("--to", dest="end", default="2026-12-31")
    ap.add_argument("--side", choices=("both", "long", "short"), default="short")
    ap.add_argument("--london", type=float, default=40.0)
    ap.add_argument("--sl", type=float, default=40.0, help="0 = no stop loss")
    ap.add_argument("--tp", type=float, default=0.0, help="0 = no take profit")
    ap.add_argument("--scale-price", action="store_true")
    a = ap.parse_args()

    t, c = mb.load_m1(a.root)
    bo, bh, bl, bc = (c[k] for k in ("open_b", "high_b", "low_b", "close_b"))
    ao, ah, al, ac = (c[k] for k in ("open_a", "high_a", "low_a", "close_a"))
    rows = []
    for day in pd.bdate_range(a.start, a.end).date:
        lon0 = ny_ns(day, "08:00", "Europe/London")
        ny0, ny_end = ny_ns(day, "08:00"), ny_ns(day, "16:55")
        i_lon, i_ny, i_end = np.searchsorted(t, [lon0, ny0, ny_end])
        if i_ny - i_lon < 0.8 * (ny0 - lon0) / 60e9 or i_ny >= len(t) or t[i_ny] - ny0 > 30 * 60e9:
            continue  # holiday, missing data, or no price inside the EA's 30-minute entry window
        l_open, move = bo[i_lon], bc[i_ny - 1] - bo[i_lon]
        scale = l_open / REF_PRICE if a.scale_price else 1.0
        if abs(move) <= a.london * scale:
            continue
        d = 1 if move > 0 else -1
        if (a.side == "long" and d < 0) or (a.side == "short" and d > 0):
            continue
        entry = ao[i_ny] if d > 0 else bo[i_ny]
        sl_px = entry - d * a.sl * scale if a.sl > 0 else np.nan
        tp_px = entry + d * a.tp * scale if a.tp > 0 else np.nan
        exit_px, why, k = None, "close 16:55", i_end
        for k in range(i_ny, i_end):
            if d > 0:
                lo, hi, op = bl[k], bh[k], bo[k]
                if a.sl > 0 and lo <= sl_px:
                    exit_px, why = min(sl_px, op), "SL"
                elif a.tp > 0 and hi >= tp_px:
                    exit_px, why = tp_px, "TP"
            else:
                lo, hi, op = al[k], ah[k], ao[k]
                if a.sl > 0 and hi >= sl_px:
                    exit_px, why = max(sl_px, op), "SL"
                elif a.tp > 0 and lo <= tp_px:
                    exit_px, why = tp_px, "TP"
            if exit_px is not None:
                break
        if exit_px is None:
            k = min(i_end, len(t) - 1)
            exit_px = bo[k] if d > 0 else ao[k]
        pnl = (exit_px - entry) * d
        to_ny = lambda ns: pd.Timestamp(ns, tz="UTC").tz_convert("America/New_York")
        e_ny, x_ny = to_ny(t[i_ny]), to_ny(t[k])
        rows.append({
            "date": day, "weekday": pd.Timestamp(day).day_name()[:3],
            "side": "BUY" if d > 0 else "SELL",
            "london_move": round(move, 2), "london_open": round(l_open, 2),
            "entry_time_ny": e_ny.strftime("%H:%M"), "entry_time_server": (e_ny + pd.Timedelta(hours=7)).strftime("%H:%M"),
            "entry": round(entry, 2), "sl": round(sl_px, 2) if a.sl > 0 else "", "tp": round(tp_px, 2) if a.tp > 0 else "",
            "exit_reason": why,
            "exit_time_ny": x_ny.strftime("%H:%M"), "exit_time_server": (x_ny + pd.Timedelta(hours=7)).strftime("%H:%M"),
            "exit": round(exit_px, 2), "pnl_usd_per_oz": round(pnl, 2),
            "pnl_R": round(pnl / (a.sl * scale), 2) if a.sl > 0 else "",
        })
    df = pd.DataFrame(rows)
    df.to_csv(a.out, index=False)

    p = df.pnl_usd_per_oz if len(df) else pd.Series(dtype=float)
    gp, gl = p[p > 0].sum(), -p[p < 0].sum()
    eq = p.cumsum()
    print(f"{a.start}..{a.end} side={a.side} London>{a.london:g} SL={a.sl or 'none'} TP={a.tp or 'none'}"
          f"{' (scaled by price)' if a.scale_price else ''}")
    print(f"  trades {len(df)}, wins {int((p > 0).sum())} ({100*(p > 0).mean():.0f}%), "
          f"profit factor {gp/gl if gl else float('inf'):.2f}, total ${p.sum():+.2f}/oz "
          f"(= $ per 0.01 lot), worst drawdown ${(eq.cummax().clip(lower=0) - eq).max():.2f}/oz")
    if len(df):
        print("  exits: " + ", ".join(f"{k} {v}" for k, v in df.exit_reason.value_counts().items()))


if __name__ == "__main__":
    main()
