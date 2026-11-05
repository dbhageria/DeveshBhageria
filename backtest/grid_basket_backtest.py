"""
Backtest of the gold "grid basket" bot seen in the video, plus a higher-timeframe (HTF)
trend-filtered buy/sell version.

Model (rebuilt from the video):
  * Open a basket (level 1) in the chosen direction.
  * Every `step` dollars price moves AGAINST the basket, add another level (averaging),
    up to `levels` levels, while free margin allows it.
  * Close the WHOLE basket when floating profit >= tp * balance (basket take-profit).
  * Optional basket stop-loss at -sl * balance (the video bot had none).
  * Stop-out when equity <= 0 (Exness-style 0% stop-out, negative-balance protection).
  * Full compounding: level size is a fixed fraction of current balance.
  * When an account blows up (or hits the $1M cap), a fresh account starts with
    `start` dollars, so we can count how many accounts a trader burns and how often
    one gets lucky (the kind that ends up in a video).

Execution assumptions:
  * M1 mid-price bars; intra-bar path O->L->H->C for up bars, O->H->L->C for down bars.
  * Spread paid on entry and exit (half each side of mid).
  * Leverage tiers like Exness: unlimited < $5k equity, 1:2000 < $30k, 1:1000 above.
  * Weekend/session gaps fill at the gapped price.
  * No swaps, commissions or slippage (all would make results worse).

Usage:  python3 -I grid_basket_backtest.py /path/to/XAUUSD_m1.csv
"""
import sys
import json
import numpy as np
import pandas as pd

CONTRACT = 100.0  # oz per lot


def leverage_for(equity):
    if equity < 5_000:
        return 1e12  # "unlimited"
    if equity < 30_000:
        return 2000.0
    return 1000.0


def load(path):
    df = pd.read_csv(path, parse_dates=["datetime"]).set_index("datetime").sort_index()
    return df


def htf_trend(df, rule, ema_len):
    htf = df["close"].resample(rule, label="left", closed="left").last().dropna()
    ema = htf.ewm(span=ema_len, adjust=False).mean()
    tr = np.sign(htf - ema).shift(1)  # only use the last COMPLETED HTF bar
    tr = tr.reindex(df.index.floor(rule)).to_numpy()
    return np.nan_to_num(tr, nan=0.0)


def run(df, trend, mode, r, levels, step, tp, sl=None, spread=0.30,
        start=10.0, cap=1_000_000.0, flip_close=False):
    """flip_close: in HTF modes, close the open basket at the bar open as soon as the
    HTF trend turns against it, then start a basket in the new direction."""
    o = df["open"].to_numpy(); h = df["high"].to_numpy()
    l = df["low"].to_numpy(); c = df["close"].to_numpy()
    t = df.index.to_numpy()
    gap_ns = np.diff(t).astype("timedelta64[m]").astype(int)
    half = spread / 2.0

    st = dict(bal=start, d=0, T=0.0, cost=0.0, n=0, last=0.0, acct_start=t[0],
              peak=start, peak_t=t[0], baskets=0, i=0)
    accounts = []          # finished accounts
    basket_results = []    # +1 win / -1 sl / -2 stopout / +3,-3 closed on HTF flip

    def end_account(i, reason):
        accounts.append(dict(reason=reason, peak=st["peak"], baskets=st["baskets"],
                             final=st["bal"] if reason == "cap" else 0.0,
                             days=(t[i] - st["acct_start"]) / np.timedelta64(1, "D"),
                             start=str(st["acct_start"])[:16], peak_at=str(st["peak_t"])[:16],
                             end=str(t[i])[:16]))
        st.update(bal=start, d=0, T=0.0, cost=0.0, n=0, acct_start=t[i],
                  peak=start, peak_t=t[i], baskets=0)

    def avg():
        return st["cost"] / st["T"]

    def equity(p):
        if st["d"] == 0 or st["T"] == 0:
            return st["bal"]
        exitp = p - st["d"] * half
        return st["bal"] + st["d"] * (exitp - avg()) * st["T"] * CONTRACT

    def add_level(p):
        lots = max(0.01, round(r * st["bal"] / CONTRACT, 2))
        entry = p + st["d"] * half
        newT = st["T"] + lots
        eq_after = equity(p) - lots * spread * CONTRACT
        margin = newT * CONTRACT * p / leverage_for(eq_after)
        st["last"] = p
        if eq_after <= 0 or margin > eq_after:
            return False
        st["T"] = newT; st["cost"] += lots * entry; st["n"] += 1
        return True

    def close_at_pnl(pnl):
        st["bal"] += pnl
        st["d"] = 0; st["T"] = 0.0; st["cost"] = 0.0; st["n"] = 0
        st["baskets"] += 1
        if st["bal"] > st["peak"]:
            st["peak"] = st["bal"]; st["peak_t"] = t[st["i"]]

    def thresh_price(X):
        # price at which basket pnl == X
        d = st["d"]
        return avg() + d * X / (st["T"] * CONTRACT) + d * half

    def stop_out(i):
        basket_results.append(-2)
        st["peak"] = max(st["peak"], st["bal"])
        end_account(i, "blown")

    def sl_now(i, p):
        # close at the ACTUAL pnl if the basket is already past its stop-loss at price p
        # (e.g. after a grid add's spread cost or a gap)
        if sl is None or st["d"] == 0:
            return False
        pnl = equity(p) - st["bal"]
        if pnl > -sl * st["bal"]:
            return False
        close_at_pnl(pnl)
        basket_results.append(-1)
        if st["bal"] < 1.0:
            end_account(i, "blown")
        return True

    def segment(i, a, b, gap):
        d = st["d"]
        if d == 0 or a == b:
            return
        if (b - a) * d < 0:  # adverse move
            if gap:  # everything happens at the gapped price
                if equity(b) <= 0:
                    stop_out(i)
                    return
                if sl_now(i, b):
                    return
                crossed = int(d * (st["last"] - b) // step)
                for _ in range(max(0, min(crossed, levels - st["n"]))):
                    if not add_level(b):
                        break
                sl_now(i, b)
                return
            cur = a
            while st["d"] != 0:
                if sl_now(i, cur):
                    return
                ev = []
                if st["n"] < levels:
                    g = st["last"] - d * step
                    if d * b <= d * g <= d * cur:
                        ev.append((d * g, "grid", g))
                ps = thresh_price(-st["bal"])
                if d * b <= d * ps <= d * cur:
                    ev.append((d * ps, "stop", ps))
                if sl is not None:
                    pl = thresh_price(-sl * st["bal"])
                    if d * b <= d * pl < d * cur:
                        ev.append((d * pl, "sl", pl))
                if not ev:
                    return
                _, kind, p = max(ev, key=lambda e: (e[0], e[1] != "grid"))
                if kind == "stop":
                    stop_out(i)
                    return
                if kind == "sl":
                    close_at_pnl(-sl * st["bal"])
                    basket_results.append(-1)
                    if st["bal"] < 1.0:
                        end_account(i, "blown")
                    return
                add_level(p)
                cur = p
        else:  # favourable move
            X = tp * st["bal"]
            p = thresh_price(X)
            if d * b >= d * p:
                pnl = (equity(b) - st["bal"]) if gap else X
                close_at_pnl(pnl)
                basket_results.append(1)
                if st["bal"] >= cap:
                    end_account(i, "cap")

    def want_dir(i):
        if mode == "buy":
            return 1
        if mode == "sell":
            return -1
        return int(trend[i])

    for i in range(len(o)):
        st["i"] = i
        if i > 0 and gap_ns[i - 1] > 5 and st["d"] != 0:
            segment(i, c[i - 1], o[i], True)
        if flip_close and st["d"] != 0 and trend[i] == -st["d"]:
            pnl = equity(o[i]) - st["bal"]
            close_at_pnl(pnl)
            basket_results.append(3 if pnl > 0 else -3)
            if st["bal"] < 1.0:
                end_account(i, "blown")
        if st["d"] == 0:
            dd = want_dir(i)
            if dd != 0:
                st["d"] = dd
                if not add_level(o[i]):
                    st["d"] = 0
                    if st["bal"] < 1.0:
                        end_account(i, "blown")
        path = (o[i], l[i], h[i], c[i]) if c[i] >= o[i] else (o[i], h[i], l[i], c[i])
        for a, b in zip(path[:-1], path[1:]):
            segment(i, a, b, False)

    # still-running account
    live = dict(balance=round(equity(c[-1]), 2), peak=round(st["peak"], 2),
                start=str(st["acct_start"])[:16], peak_at=str(st["peak_t"])[:16],
                days=round((t[-1] - st["acct_start"]) / np.timedelta64(1, "D"), 1),
                baskets=st["baskets"])
    acc = pd.DataFrame(accounts) if accounts else pd.DataFrame(
        columns=["reason", "peak", "baskets", "days", "final", "start", "peak_at", "end"])
    blown = acc[acc.reason == "blown"]
    br = np.array(basket_results)
    total_accounts = len(acc) + 1
    peaks = np.append(acc["peak"].to_numpy(), st["peak"])
    lives = np.append(acc["days"].to_numpy(dtype=float), live["days"])
    survived = lambda x: round(100 * float((lives >= x).mean()), 2)
    deposited = total_accounts * start
    cashed_out = float(acc["final"].sum()) + live["balance"]
    return dict(
        accounts_used=total_accounts,
        pct_alive_1d=survived(1), pct_alive_7d=survived(7), pct_alive_30d=survived(30),
        deposited=round(deposited, 2), ending_money=round(cashed_out, 2),
        net_result=round(cashed_out - deposited, 2),
        blown=int(len(blown)),
        hit_1M=int((acc.reason == "cap").sum()),
        median_life_days=round(float(blown["days"].median()), 2) if len(blown) else None,
        median_baskets_before_blowup=float(blown["baskets"].median()) if len(blown) else None,
        pct_reach_1k=round(100 * float((peaks >= 1_000).mean()), 2),
        pct_reach_10k=round(100 * float((peaks >= 10_000).mean()), 2),
        pct_reach_100k=round(100 * float((peaks >= 100_000).mean()), 2),
        best_peak=round(float(peaks.max()), 2),
        basket_win_rate=round(100 * float((br > 0).mean()), 2) if len(br) else None,
        flip_closes=int((np.abs(br) == 3).sum()),
        flip_closes_in_profit_pct=round(100 * float((br == 3).sum() / max(1, (np.abs(br) == 3).sum())), 2),
        baskets=int(len(br)),
        live_account=live,
        top_accounts=[dict(start=a["start"], peak_at=a["peak_at"], end=a["end"],
                           peak=round(a["peak"], 2), baskets=int(a["baskets"]),
                           days=round(a["days"], 2), how_it_ended=a["reason"])
                      for a in acc.sort_values("peak", ascending=False).head(3).to_dict("records")],
    )


PROFILES = {
    # like the video: big size, averaging, aim to ~double the balance per basket, no SL
    "video_like":   dict(r=0.25, levels=6, step=1.0, tp=1.00, sl=None),
    # same idea, smaller targets
    "moderate":     dict(r=0.10, levels=5, step=2.0, tp=0.30, sl=None),
    # small size, wide grid, small target, basket stop-loss
    "conservative": dict(r=0.02, levels=5, step=3.0, tp=0.05, sl=0.25),
}

MODES = ["buy", "sell", "htf_H1", "htf_H4", "htf_D1"]
HTF = {"htf_H1": ("1h", 50), "htf_H4": ("4h", 50), "htf_D1": ("1D", 20)}

if __name__ == "__main__":
    path = sys.argv[1]
    spread = float(sys.argv[2]) if len(sys.argv) > 2 else 0.30
    start = float(sys.argv[3]) if len(sys.argv) > 3 else 10.0
    df = load(path)
    trends = {m: htf_trend(df, *HTF[m]) for m in HTF}
    zero = np.zeros(len(df))
    out = []
    for pname, p in PROFILES.items():
        for mode in MODES:
            res = run(df, trends.get(mode, zero), mode, spread=spread, start=start, **p)
            res.update(profile=pname, mode=mode)
            out.append(res)
            print(json.dumps(res), flush=True)
