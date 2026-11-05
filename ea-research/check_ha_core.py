"""
Cross-check of the user's MQL5 HeikenAshiMTFCore.mqh logic against ha_backtest.py.

HACalculateLast() is ported line for line: it rebuilds Heikin Ashi from a window of `count`
completed real candles (seeded from the first one) and classifies the LAST candle:
direction +1/-1/0 from HA close vs open, and wickSignal +1/-1 when the body is at least
minimumBody and the lower (buy) or upper (sell) wick is within tickTolerance.

ha_backtest.ha_states() runs the recurrence over the whole history instead. For random candles
on several frames, this script compares the two classifications, and shows how much the
window's warmup depth (seed) matters.

Usage: python3 check_ha_core.py M1_ROOT
"""
import sys
import numpy as np

import ha_backtest as hb
import m1_backtest as mb


def ha_calculate_last(o, h, l, c, tick_tolerance, minimum_body):
    """Port of HACalculateLast(): returns (direction, wickSignal) of the last candle."""
    prev_open = prev_close = 0.0
    for i in range(len(o)):
        cur_close = (o[i] + h[i] + l[i] + c[i]) / 4.0
        cur_open = (o[i] + c[i]) / 2.0 if i == 0 else (prev_open + prev_close) / 2.0
        cur_high = max(h[i], cur_open, cur_close)
        cur_low = min(l[i], cur_open, cur_close)
        prev_open, prev_close = cur_open, cur_close
    direction = 1 if cur_close > cur_open else (-1 if cur_close < cur_open else 0)
    body = abs(cur_close - cur_open)
    lower = max(0.0, min(cur_open, cur_close) - cur_low)
    upper = max(0.0, cur_high - max(cur_open, cur_close))
    eps = 1.0e-12 * max(1.0, abs(cur_open), abs(cur_close))
    wick = 0
    if body + eps >= minimum_body:
        if direction == 1 and lower <= tick_tolerance + eps:
            wick = 1
        if direction == -1 and upper <= tick_tolerance + eps:
            wick = -1
    return direction, wick


def main(root, samples=4000, seed=3):
    t, c = mb.load_m1(root)
    m = hb.server_minutes(t)
    bo, bh, bl, bc = (c[k] for k in ("open_b", "high_b", "low_b", "close_b"))
    rng = np.random.default_rng(seed)
    print(f"Tolerance {hb.WICK_TOL:.2f}, minimum body {hb.MIN_BODY:.2f} (1 and 2 ticks of 0.01)\n")
    print(f"{'frame':<6} {'window':>6} {'candles':>8} {'same wickSignal':>16} {'same direction':>15} "
          f"{'signals (EA port)':>18} {'signals (backtest)':>19}")
    for tf in (1, 5, 60):
        if tf == 1:
            bars = dict(o=bo, h=bh, l=bl, c=bc)
        else:
            bars = hb.resample(m, bo, bh, bl, bc, tf)
        strict, colour = hb.ha_states(bars["o"], bars["h"], bars["l"], bars["c"], hb.WICK_TOL, hb.MIN_BODY)
        n = len(bars["o"])
        for window in (200, 50, 10):
            idx = rng.integers(1000, n, size=samples)
            same_w = same_d = sig_port = sig_bt = 0
            for i in idx:
                s = slice(i - window + 1, i + 1)
                d, w = ha_calculate_last(bars["o"][s], bars["h"][s], bars["l"][s], bars["c"][s],
                                         hb.WICK_TOL, hb.MIN_BODY)
                same_w += w == strict[i]
                # backtest colour state needs a body >= minimum; compare direction only where it is set
                same_d += (colour[i] == 0) or (d == colour[i])
                sig_port += w != 0
                sig_bt += strict[i] != 0
            name = {1: "M1", 5: "M5", 60: "H1"}[tf]
            print(f"{name:<6} {window:>6} {samples:>8} {100*same_w/samples:>15.2f}% {100*same_d/samples:>14.2f}% "
                  f"{100*sig_port/samples:>17.1f}% {100*sig_bt/samples:>18.1f}%")


if __name__ == "__main__":
    main(sys.argv[1])
