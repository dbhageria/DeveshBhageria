"""
No-averaging HTF trend strategy on gold, risking a fixed % of balance per trade.

Rules:
  * One position at a time, in the HTF trend direction (or buy / sell / random for comparison).
  * Fixed stop-loss distance `sl` ($ of gold price) and take-profit at `rr` x that distance.
  * Lot size so that hitting the stop loses `risk` x balance (rounded down to 0.01 lots).
  * Optional: close early when the HTF trend flips against the trade.
  * Re-enter on the next bar after a trade closes.

Execution assumptions match grid_basket_backtest.py: M1 mid-price bars, intra-bar path
O->L->H->C for up bars and O->H->L->C for down bars, spread paid half on each side, gaps
fill at the gapped price. Swaps are optional (`swaps=True` uses the broker's XAUUSD swaps,
with triple swap on Wednesdays). No commission or slippage.

Usage: python3 -I trend_backtest.py /path/to/XAUUSD_m1.csv > r_trend.jsonl
"""
import sys
import json
import numpy as np
import grid_basket_backtest as g

CONTRACT = 100.0
SWAP_POINTS = {1: -95.03, -1: -5.40}  # broker XAUUSD swap per lot per night, in points ($1 each)


def run(df, trend, mode, sl, rr, risk=0.01, spread=0.30, start=10_000.0,
        flip_close=False, swaps=False, seed=0):
    o = df["open"].to_numpy(); h = df["high"].to_numpy()
    l = df["low"].to_numpy(); c = df["close"].to_numpy()
    days = df.index.normalize().to_numpy()
    weekday = df.index.weekday.to_numpy()
    half = spread / 2.0
    rng = np.random.default_rng(seed)

    bal = start
    peak = start
    max_dd = 0.0
    d = 0; lots = 0.0; entry = 0.0; slp = 0.0; tpp = 0.0; risk_usd = 0.0
    r_mults = []
    dirs = []
    gross_win = 0.0; gross_loss = 0.0

    def close(exitp):
        nonlocal bal, d, peak, max_dd, gross_win, gross_loss
        pnl = d * (exitp - entry) * lots * CONTRACT
        bal += pnl
        r_mults.append(pnl / risk_usd)
        dirs.append(d)
        if pnl > 0:
            gross_win += pnl
        else:
            gross_loss -= pnl
        peak = max(peak, bal)
        max_dd = max(max_dd, 1 - bal / peak)
        d = 0

    for i in range(len(o)):
        if d != 0:
            if swaps and days[i] != days[i - 1]:
                nights = 3 if weekday[i - 1] == 2 else 1
                bal += SWAP_POINTS[d] * lots * nights
            if flip_close and trend[i] == -d:
                close(o[i] - d * half)
            else:  # gap through stop or target: fill at the open
                ex = o[i] - d * half
                if d * (ex - slp) <= 0 or d * (ex - tpp) >= 0:
                    close(ex)
        if d == 0:
            if mode == "buy":
                nd = 1
            elif mode == "sell":
                nd = -1
            elif mode == "random":
                nd = 1 if rng.random() < 0.5 else -1
            else:
                nd = int(trend[i])
            if nd != 0:
                lt = np.floor(bal * risk / (sl * CONTRACT) / 0.01 + 1e-9) * 0.01
                if lt >= 0.01:
                    d = nd; lots = lt
                    entry = o[i] + d * half
                    slp = entry - d * sl
                    tpp = entry + d * sl * rr
                    risk_usd = sl * lots * CONTRACT
        if d != 0:
            path = (l[i], h[i], c[i]) if c[i] >= o[i] else (h[i], l[i], c[i])
            for p in path:
                ex = p - d * half
                if d * (ex - slp) <= 0:
                    close(slp)
                    break
                if d * (ex - tpp) >= 0:
                    close(tpp)
                    break

    if d != 0:
        close(c[-1] - d * half)
    r = np.array(r_mults)
    dr = np.array(dirs)
    side = lambda x: round(float(r[dr == x].mean()), 4) if (dr == x).any() else None
    return dict(
        trades=int(len(r)),
        win_rate=round(100 * float((r > 0).mean()), 2) if len(r) else None,
        breakeven_win_rate=round(100 / (1 + rr), 2),
        avg_R=round(float(r.mean()), 4) if len(r) else None,
        profit_factor=round(gross_win / gross_loss, 3) if gross_loss else None,
        final_balance=round(bal, 2),
        return_pct=round(100 * (bal / start - 1), 2),
        max_drawdown_pct=round(100 * max_dd, 2),
        long_trades=int((dr == 1).sum()), avg_R_long=side(1),
        short_trades=int((dr == -1).sum()), avg_R_short=side(-1),
    )


if __name__ == "__main__":
    df = g.load(sys.argv[1])
    trends = {m: g.htf_trend(df, *g.HTF[m]) for m in g.HTF}
    zero = np.zeros(len(df))
    jobs = sys.argv[2].split(",") if len(sys.argv) > 2 else ["htf_H1", "htf_H4", "htf_D1", "buy", "sell", "random"]
    if jobs == ["swaps"]:  # re-check the $20-stop settings with overnight swaps charged
        for mode in ("htf_H1", "htf_H4", "htf_D1", "buy"):
            for rr in (1.0, 2.0, 3.0):
                res = run(df, trends.get(mode, zero), mode, 20.0, rr, swaps=True)
                res.update(mode=mode, sl=20.0, rr=rr, flip_close=False, seed=0, swaps=True)
                print(json.dumps(res), flush=True)
        sys.exit()
    for mode in jobs:
        for sl in (5.0, 10.0, 20.0):
            for rr in (1.0, 2.0, 3.0):
                for flip in ((False, True) if mode.startswith("htf") else (False,)):
                    seeds = range(5) if mode == "random" else [0]
                    for seed in seeds:
                        res = run(df, trends.get(mode, zero), mode, sl, rr, flip_close=flip, seed=seed)
                        res.update(mode=mode, sl=sl, rr=rr, flip_close=flip, seed=seed)
                        print(json.dumps(res), flush=True)
