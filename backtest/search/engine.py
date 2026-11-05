"""Fast single-position backtest engine (numba) on M15 gold bars, plus indicators.

Execution rules
  * Signals are computed on bar i's close; entries/exits fill at bar i+1's open.
  * Stop = entry -/+ stop_mult * ATR(at signal). Optional target at rr * stop distance,
    optional ATR trailing stop (chandelier, from the highest high / lowest low since entry),
    optional time exit after max_bars bars.
  * If stop and target are both inside one bar, the stop is assumed hit first (conservative).
    Gaps through a stop fill at the open.
  * Costs per trade: spread = max(min_spread, spread_bp * price), paid once per round trip.
    Swaps charged per unit per calendar-day rollover held (triple on Wednesday night):
    long swap_long_bp * price, short swap_short_bp * price.
  * Results are in R (net pnl / initial stop distance). Equity compounds at `risk` per trade.
"""
import numpy as np
import pandas as pd
from numba import njit

DEFAULT_COSTS = dict(min_spread=0.30, spread_bp=1.1e-4, swap_long_bp=2.4e-4, swap_short_bp=0.15e-4)


@njit(cache=True)
def _run(o, h, l, c, atr, long_sig, short_sig, exit_long, exit_short, day, wday,
         stop_mult, rr, trail_mult, max_bars,
         min_spread, spread_bp, swap_long_bp, swap_short_bp):
    n = len(o)
    max_trades = n // 2 + 1
    t_entry = np.empty(max_trades, np.int64)
    t_exit = np.empty(max_trades, np.int64)
    t_dir = np.empty(max_trades, np.int64)
    t_r = np.empty(max_trades, np.float64)
    k = 0
    d = 0
    entry = 0.0; stop = 0.0; target = 0.0; dist = 0.0; ext = 0.0; swap_acc = 0.0
    ent_i = 0; atr_e = 0.0
    pend_dir = 0; pend_exit = False

    for i in range(1, n):
        # 1) pending exit at the open
        if d != 0 and pend_exit:
            px = o[i]
            spread = max(min_spread, spread_bp * entry)
            t_entry[k] = ent_i; t_exit[k] = i; t_dir[k] = d
            t_r[k] = (d * (px - entry) - spread - swap_acc) / dist
            k += 1
            d = 0
        pend_exit = False
        # 2) swaps on day change
        if d != 0 and day[i] != day[i - 1]:
            nights = 3.0 if wday[i - 1] == 2 else 1.0
            swap_acc += nights * (swap_long_bp if d == 1 else swap_short_bp) * c[i - 1]
        # 3) pending entry at the open
        if d == 0 and pend_dir != 0 and atr[i - 1] > 0:
            d = pend_dir
            entry = o[i]
            atr_e = atr[i - 1]
            dist = stop_mult * atr_e
            stop = entry - d * dist
            target = entry + d * rr * dist if rr > 0 else 0.0
            ext = entry
            swap_acc = 0.0
            ent_i = i
        pend_dir = 0
        # 4) stop / target inside the bar
        if d != 0:
            hit = False
            px = 0.0
            if d == 1:
                if o[i] <= stop:
                    hit = True; px = o[i]
                elif l[i] <= stop:
                    hit = True; px = stop
                elif rr > 0 and h[i] >= target:
                    hit = True; px = max(target, o[i])
            else:
                if o[i] >= stop:
                    hit = True; px = o[i]
                elif h[i] >= stop:
                    hit = True; px = stop
                elif rr > 0 and l[i] <= target:
                    hit = True; px = min(target, o[i])
            if hit:
                spread = max(min_spread, spread_bp * entry)
                t_entry[k] = ent_i; t_exit[k] = i; t_dir[k] = d
                t_r[k] = (d * (px - entry) - spread - swap_acc) / dist
                k += 1
                d = 0
        # 5) end-of-bar updates and signals (act at next open)
        if d != 0:
            if trail_mult > 0:
                if d == 1:
                    ext = max(ext, h[i])
                    stop = max(stop, ext - trail_mult * atr_e)
                else:
                    ext = min(ext, l[i])
                    stop = min(stop, ext + trail_mult * atr_e)
            if (d == 1 and exit_long[i]) or (d == -1 and exit_short[i]):
                pend_exit = True
            if max_bars > 0 and i - ent_i + 1 >= max_bars:
                pend_exit = True
            if pend_exit:
                # allow a reversal on the same open
                if d == 1 and short_sig[i]:
                    pend_dir = -1
                elif d == -1 and long_sig[i]:
                    pend_dir = 1
        else:
            if long_sig[i]:
                pend_dir = 1
            elif short_sig[i]:
                pend_dir = -1
        # a reversal needs the exit to happen first; handled at the next open
        if pend_exit and pend_dir != 0:
            pass
    # close any open trade at the last close
    if d != 0:
        spread = max(min_spread, spread_bp * entry)
        t_entry[k] = ent_i; t_exit[k] = n - 1; t_dir[k] = d
        t_r[k] = (d * (c[n - 1] - entry) - spread - swap_acc) / dist
        k += 1
    return t_entry[:k], t_exit[:k], t_dir[:k], t_r[:k]


class Data:
    def __init__(self, df):
        self.df = df
        self.idx = df.index
        self.o = df["open"].to_numpy(np.float64)
        self.h = df["high"].to_numpy(np.float64)
        self.l = df["low"].to_numpy(np.float64)
        self.c = df["close"].to_numpy(np.float64)
        self.day = df.index.to_numpy().astype("datetime64[D]").astype(np.int64)
        self.wday = df.index.weekday.to_numpy().astype(np.int64)
        self.hour = df.index.hour.to_numpy()
        self.minute = df.index.minute.to_numpy()
        self.year = df.index.year.to_numpy()
        self._cache = {}

    # ---- indicators (value at i uses bars <= i only) ----
    def atr(self, n=14):
        key = ("atr", n)
        if key not in self._cache:
            pc = np.roll(self.c, 1); pc[0] = self.c[0]
            tr = np.maximum(self.h - self.l, np.maximum(np.abs(self.h - pc), np.abs(self.l - pc)))
            self._cache[key] = pd.Series(tr).ewm(alpha=1 / n, adjust=False).mean().to_numpy()
        return self._cache[key]

    def ema(self, n, src=None):
        key = ("ema", n, id(src))
        if key not in self._cache:
            s = self.c if src is None else src
            self._cache[key] = pd.Series(s).ewm(span=n, adjust=False).mean().to_numpy()
        return self._cache[key]

    def rsi(self, n=14):
        key = ("rsi", n)
        if key not in self._cache:
            dlt = np.diff(self.c, prepend=self.c[0])
            up = pd.Series(np.maximum(dlt, 0)).ewm(alpha=1 / n, adjust=False).mean()
            dn = pd.Series(np.maximum(-dlt, 0)).ewm(alpha=1 / n, adjust=False).mean()
            self._cache[key] = (100 - 100 / (1 + up / dn.replace(0, np.nan))).fillna(50).to_numpy()
        return self._cache[key]

    def donchian(self, n):
        key = ("don", n)
        if key not in self._cache:
            hh = pd.Series(self.h).rolling(n).max().shift(1).to_numpy()
            ll = pd.Series(self.l).rolling(n).min().shift(1).to_numpy()
            self._cache[key] = (hh, ll)
        return self._cache[key]

    def htf_close_ema(self, rule, n):
        """+1/-1: last COMPLETED higher-timeframe close above/below its EMA(n)."""
        key = ("htf", rule, n)
        if key not in self._cache:
            s = pd.Series(self.c, index=self.idx)
            htf = s.resample(rule, label="left", closed="left").last().dropna()
            ema = htf.ewm(span=n, adjust=False).mean()
            tr = np.sign(htf - ema).shift(1)
            self._cache[key] = np.nan_to_num(tr.reindex(self.idx.floor(rule)).to_numpy())
        return self._cache[key]

    def htf_ma(self, rule, n):
        """EMA(n) of completed higher-timeframe closes, aligned to M15 bars (no lookahead)."""
        key = ("htfma", rule, n)
        if key not in self._cache:
            s = pd.Series(self.c, index=self.idx)
            htf = s.resample(rule, label="left", closed="left").last().dropna()
            ema = htf.ewm(span=n, adjust=False).mean().shift(1)
            self._cache[key] = ema.reindex(self.idx.floor(rule)).to_numpy()
        return self._cache[key]


def load_segment(path, start):
    """The data has a 2020-2024 gap. Indicators must not run across it, so return only the
    continuous block that contains `start` (2010-2019 or 2025-2026)."""
    df = pd.read_pickle(path)
    if pd.Timestamp(start) >= pd.Timestamp("2020-01-01"):
        return Data(df.loc["2025-01-01":])
    return Data(df.loc[:"2019-12-31 23:59"])


def run(data, sig, stop_mult, rr=0.0, trail_mult=0.0, max_bars=0, atr_n=14, costs=None):
    cst = dict(DEFAULT_COSTS, **(costs or {}))
    n = len(data.o)
    z = np.zeros(n, np.bool_)
    ls = sig.get("long", z).astype(np.bool_)
    ss = sig.get("short", z).astype(np.bool_)
    el = sig.get("exit_long", z).astype(np.bool_)
    es = sig.get("exit_short", z).astype(np.bool_)
    return _run(data.o, data.h, data.l, data.c, data.atr(atr_n), ls, ss, el, es,
                data.day, data.wday, float(stop_mult), float(rr), float(trail_mult), int(max_bars),
                cst["min_spread"], cst["spread_bp"], cst["swap_long_bp"], cst["swap_short_bp"])


def metrics(data, trades, start, end, risk=0.01):
    """Stats for trades ENTERED in [start, end)."""
    te, tx, td, tr = trades
    t0 = data.idx[te]
    m = (t0 >= pd.Timestamp(start)) & (t0 < pd.Timestamp(end))
    r = tr[m]
    yrs = data.year[te[m]]
    out = dict(trades=int(m.sum()))
    if out["trades"] == 0:
        return dict(out, pf=0.0, avg_r=0.0, ret_pct=0.0, max_dd_pct=0.0, years_pos=0, years=0,
                    long_avg_r=0.0, short_avg_r=0.0, win_rate=0.0)
    gw = r[r > 0].sum(); gl = -r[r < 0].sum()
    eq = np.cumprod(1 + risk * r)
    peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))[1:]
    dd = float((1 - eq / peak).max())
    yr = pd.Series(r).groupby(yrs).sum()
    dirs = td[m]
    return dict(out,
                pf=round(float(gw / gl) if gl > 0 else 99.0, 3),
                avg_r=round(float(r.mean()), 4),
                win_rate=round(100 * float((r > 0).mean()), 1),
                ret_pct=round(100 * float(eq[-1] - 1), 1),
                max_dd_pct=round(100 * dd, 1),
                years_pos=int((yr > 0).sum()), years=int(len(yr)),
                long_avg_r=round(float(r[dirs == 1].mean()), 4) if (dirs == 1).any() else 0.0,
                short_avg_r=round(float(r[dirs == -1].mean()), 4) if (dirs == -1).any() else 0.0)
